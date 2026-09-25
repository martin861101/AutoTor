import asyncio
import contextlib
import ipaddress
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .fetcher import BulkLink, FetchError, episode_identity, fetch_magnet, scan_bulk_links
from .magnet import MagnetError, magnet_info_hash, magnet_size, validate_magnet
from .qbittorrent import QBittorrentClient, QBittorrentError
from .storage import Destination, StorageError, ensure_storage, validate_storage


ACTIVE_STATES = {
    "allocating",
    "checkingDL",
    "downloading",
    "forcedDL",
    "metaDL",
    "queuedDL",
    "stalledDL",
}
PAUSED_STATES = {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}


class DownloadRequest(BaseModel):
    source: str = Field(min_length=1, max_length=16_384)
    destination: str


class BulkDownloadRequest(BaseModel):
    source: str = Field(min_length=1, max_length=16_384)
    destination: str
    start: str | None = Field(default=None, max_length=16)
    end: str | None = Field(default=None, max_length=16)
    must_include: str = Field(default="", max_length=100)
    resolutions: list[Literal["480p", "720p", "1080p", "2160p"]] = Field(default_factory=list, max_length=4)
    other_filter: str = Field(default="", max_length=100)


class ActionRequest(BaseModel):
    delete_files: bool = False
    confirm_delete_files: bool = False


def destinations(settings: Settings) -> dict[str, Destination]:
    return {
        "series": Destination(
            "series", "Series", settings.series_path, settings.series_qb_path, settings.series_volume_id
        ),
        "movies": Destination(
            "movies", "Movies", settings.movies_path, settings.movies_qb_path, settings.movies_volume_id
        ),
    }


def destination_for_torrent(torrent: dict, choices: dict[str, Destination]) -> Destination | None:
    save_path = torrent.get("save_path", "")
    return next((item for item in choices.values() if save_path.rstrip("/").startswith(item.qb_path.rstrip("/"))), None)


def serialized_torrent(torrent: dict, choices: dict[str, Destination]) -> dict:
    destination = destination_for_torrent(torrent, choices)
    eta = torrent.get("eta")
    if not isinstance(eta, int) or eta >= 8_640_000 or eta < 0:
        eta = None
    state = torrent.get("state", "unknown")
    if state in ACTIVE_STATES:
        status = "downloading"
    elif state in PAUSED_STATES:
        status = "paused"
    elif state in {"uploading", "stalledUP", "queuedUP", "forcedUP"} or torrent.get("progress", 0) >= 1:
        status = "completed"
    elif state in {"error", "missingFiles"}:
        status = "error"
    else:
        status = "pending"
    return {
        "hash": torrent.get("hash"),
        "name": torrent.get("name") or "Fetching metadata…",
        "destination": destination.key if destination else "other",
        "destination_label": destination.label if destination else "Other",
        "size": max(0, int(torrent.get("total_size") or torrent.get("size") or 0)),
        "progress": min(1, max(0, float(torrent.get("progress") or 0))),
        "speed": max(0, int(torrent.get("dlspeed") or 0)),
        "eta": eta,
        "status": status,
        "state": state,
    }


def reserved_space(torrents: list[dict], volume_id: str, choices: dict[str, Destination]) -> int:
    total = 0
    for torrent in torrents:
        destination = destination_for_torrent(torrent, choices)
        if destination and destination.volume_id == volume_id:
            size = int(torrent.get("total_size") or torrent.get("size") or 0)
            completed = int(torrent.get("completed") or 0)
            total += max(0, size - completed)
    return total


async def storage_monitor(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    choices = destinations(settings)
    while True:
        try:
            torrents = await app.state.qb.torrents()
            statuses = {
                key: await asyncio.to_thread(validate_storage, destination)
                for key, destination in choices.items()
            }
            pause_hashes: set[str] = set()
            for torrent in torrents:
                destination = destination_for_torrent(torrent, choices)
                if destination and not statuses[destination.key].writable:
                    pause_hashes.add(torrent["hash"])

            by_volume: dict[str, list[Destination]] = defaultdict(list)
            for item in choices.values():
                by_volume[item.volume_id].append(item)
            for volume_id, members in by_volume.items():
                online = [statuses[item.key] for item in members if statuses[item.key].writable]
                if not online:
                    continue
                remaining = reserved_space(torrents, volume_id, choices)
                if remaining > min(status.available_bytes for status in online):
                    for torrent in torrents:
                        item = destination_for_torrent(torrent, choices)
                        if item and item.volume_id == volume_id and torrent.get("state") in ACTIVE_STATES:
                            pause_hashes.add(torrent["hash"])

            if pause_hashes:
                await app.state.qb.action("pause", sorted(pause_hashes))
                app.state.storage_holds.update(pause_hashes)
        except (QBittorrentError, OSError):
            pass
        await asyncio.sleep(settings.monitor_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.qb = QBittorrentClient(settings.qb_url, settings.qb_username, settings.qb_password)
    app.state.storage_holds = set()
    monitor = asyncio.create_task(storage_monitor(app))
    yield
    monitor.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await monitor
    await app.state.qb.close()


app = FastAPI(title="AutoTor API", version="1.0.0", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def trusted_lan_only(request: Request, call_next):
    settings = getattr(request.app.state, "settings", get_settings())
    peer_ip = request.client.host if request.client else ""
    forwarded_ip = request.headers.get("x-real-ip", "")
    client_ip = forwarded_ip if forwarded_ip and _is_private_peer(peer_ip) else peer_ip
    try:
        address = ipaddress.ip_address(client_ip)
        networks = [ipaddress.ip_network(item.strip()) for item in settings.trusted_networks.split(",") if item.strip()]
        if not any(address in network for network in networks):
            return JSONResponse(status_code=403, content={"detail": "AutoTor is restricted to trusted networks."})
    except ValueError:
        return JSONResponse(status_code=403, content={"detail": "AutoTor is restricted to trusted networks."})
    return await call_next(request)


def _is_private_peer(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        return address.is_private or address.is_loopback
    except ValueError:
        return False


@app.exception_handler(QBittorrentError)
async def qbittorrent_error_handler(_request: Request, exc: QBittorrentError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


def get_qb(request: Request) -> QBittorrentClient:
    return request.app.state.qb


@app.get("/api/health")
async def health(qb: QBittorrentClient = Depends(get_qb)):
    try:
        version = await qb.version()
        return {"status": "ok", "qbittorrent": {"connected": True, "version": version}}
    except QBittorrentError as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "qbittorrent": {"connected": False}, "detail": str(exc)},
        )


@app.get("/api/storage")
async def storage(request: Request):
    choices = destinations(request.app.state.settings)
    results = await asyncio.gather(
        *(asyncio.to_thread(validate_storage, destination) for destination in choices.values())
    )
    return {"destinations": [status.model() for status in results]}


@app.get("/api/torrents")
async def torrent_list(request: Request, qb: QBittorrentClient = Depends(get_qb)):
    try:
        items = await qb.torrents()
    except QBittorrentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    choices = destinations(request.app.state.settings)
    return {"torrents": [serialized_torrent(item, choices) for item in items]}


@app.post("/api/downloads", status_code=201)
async def add_download(payload: DownloadRequest, request: Request, qb: QBittorrentClient = Depends(get_qb)):
    choices = destinations(request.app.state.settings)
    destination = choices.get(payload.destination.lower())
    if not destination:
        raise HTTPException(status_code=422, detail="Choose Movies or Series.")

    source = payload.source.strip()
    try:
        magnet = (
            validate_magnet(source)
            if source.lower().startswith("magnet:?")
            else await fetch_magnet(
                source,
                request.app.state.settings.fetch_timeout_seconds,
                request.app.state.settings.fetch_max_bytes,
            )
        )
        info_hash = magnet_info_hash(magnet)
        torrents = await qb.torrents()
        if any(str(item.get("hash", "")).lower() == info_hash for item in torrents):
            raise HTTPException(status_code=409, detail="This torrent is already in qBittorrent.")
        reserved = reserved_space(torrents, destination.volume_id, choices)
        await asyncio.to_thread(ensure_storage, destination, magnet_size(magnet), reserved)
        await qb.add(magnet, destination.qb_path)
        return {"hash": info_hash, "destination": destination.key}
    except (MagnetError, FetchError, StorageError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QBittorrentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/downloads/bulk")
async def add_bulk_downloads(
    payload: BulkDownloadRequest, request: Request, qb: QBittorrentClient = Depends(get_qb)
):
    choices = destinations(request.app.state.settings)
    destination = choices.get(payload.destination.lower())
    if not destination:
        raise HTTPException(status_code=422, detail="Choose Movies or Series.")

    settings = request.app.state.settings
    try:
        links = await scan_bulk_links(
            payload.source,
            settings.fetch_timeout_seconds,
            settings.fetch_max_bytes,
            start=payload.start,
            end=payload.end,
            must_include=payload.must_include,
            resolutions=tuple(payload.resolutions),
            other_filter=payload.other_filter,
        )
    except FetchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not links:
        raise HTTPException(status_code=422, detail="No links matched the selected filters.")

    semaphore = asyncio.Semaphore(5)

    async def resolve(link: BulkLink) -> tuple[BulkLink, str | None, str | None]:
        try:
            async with semaphore:
                magnet = (
                    validate_magnet(link.url)
                    if link.url.lower().startswith("magnet:?")
                    else await fetch_magnet(link.url, settings.fetch_timeout_seconds, settings.fetch_max_bytes)
                )
            return link, magnet, None
        except (MagnetError, FetchError) as exc:
            return link, None, str(exc)

    resolved = await asyncio.gather(*(resolve(link) for link in links))
    try:
        torrents = await qb.torrents()
    except QBittorrentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    known_hashes = {str(item.get("hash", "")).lower() for item in torrents}
    known_names = {str(item.get("name", "")).strip().casefold() for item in torrents if item.get("name")}
    include = payload.must_include.strip().casefold()
    known_episodes: set[tuple[str, int, int]] = set()
    if payload.start is not None:
        for torrent in torrents:
            name = str(torrent.get("name", ""))
            if include and include not in name.casefold():
                continue
            identity = episode_identity(name, include)
            if identity is not None:
                known_episodes.add(identity)
    reserved = reserved_space(torrents, destination.volume_id, choices)
    batch_reserved = 0
    added: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for link, magnet, error in resolved:
        item = {"title": link.title}
        if error or magnet is None:
            failed.append({**item, "error": error or "The magnet link could not be resolved."})
            continue
        info_hash = magnet_info_hash(magnet)
        if info_hash in known_hashes:
            skipped.append({**item, "reason": "Already in qBittorrent."})
            continue
        display_name = parse_qs(urlparse(magnet).query).get("dn", [""])[0].strip().casefold()
        if display_name and display_name in known_names:
            skipped.append({**item, "reason": "A torrent with this name is already present."})
            continue
        if link.episode_key is not None and link.episode_key in known_episodes:
            skipped.append({**item, "reason": "This episode is already present."})
            continue
        size = magnet_size(magnet)
        try:
            await asyncio.to_thread(ensure_storage, destination, size, reserved + batch_reserved)
            await qb.add(magnet, destination.qb_path)
        except (StorageError, QBittorrentError) as exc:
            failed.append({**item, "error": str(exc)})
            continue
        known_hashes.add(info_hash)
        if display_name:
            known_names.add(display_name)
        if link.episode_key is not None:
            known_episodes.add(link.episode_key)
        batch_reserved += size
        added.append({**item, "hash": info_hash})

    return {"matched": len(links), "added": added, "skipped": skipped, "failed": failed}


async def _find_torrent(info_hash: str, qb: QBittorrentClient) -> dict:
    if len(info_hash) != 40 or any(character not in "0123456789abcdefABCDEF" for character in info_hash):
        raise HTTPException(status_code=422, detail="Invalid torrent hash.")
    items = await qb.torrents()
    torrent = next((item for item in items if str(item.get("hash", "")).lower() == info_hash.lower()), None)
    if not torrent:
        raise HTTPException(status_code=404, detail="Torrent not found.")
    return torrent


@app.post("/api/torrents/{info_hash}/pause", status_code=204)
async def pause_torrent(info_hash: str, qb: QBittorrentClient = Depends(get_qb)):
    await _find_torrent(info_hash, qb)
    await qb.action("pause", [info_hash])


@app.post("/api/torrents/{info_hash}/resume", status_code=204)
async def resume_torrent(info_hash: str, request: Request, qb: QBittorrentClient = Depends(get_qb)):
    torrent = await _find_torrent(info_hash, qb)
    choices = destinations(request.app.state.settings)
    destination = destination_for_torrent(torrent, choices)
    if not destination:
        raise HTTPException(status_code=422, detail="This torrent is outside a managed destination.")
    torrents = await qb.torrents()
    try:
        await asyncio.to_thread(
            ensure_storage,
            destination,
            0,
            reserved_space(torrents, destination.volume_id, choices),
        )
    except StorageError as exc:
        raise HTTPException(status_code=422, detail=f"Storage revalidation failed: {exc}") from exc
    await qb.action("resume", [info_hash])
    request.app.state.storage_holds.discard(info_hash.lower())


@app.post("/api/torrents/{info_hash}/remove", status_code=204)
async def remove_torrent(
    info_hash: str, payload: ActionRequest, request: Request, qb: QBittorrentClient = Depends(get_qb)
):
    await _find_torrent(info_hash, qb)
    if payload.delete_files and not payload.confirm_delete_files:
        raise HTTPException(status_code=422, detail="Confirm deletion of downloaded files.")
    await qb.action("delete", [info_hash], delete_files=payload.delete_files)
    request.app.state.storage_holds.discard(info_hash.lower())
