import asyncio
import logging
import re
from pathlib import Path, PurePosixPath

from .config import Settings, get_settings
from .qbittorrent import QBittorrentClient, QBittorrentError
from .storage import Destination, validate_storage


logger = logging.getLogger(__name__)
EPISODE = re.compile(r"(?:^|[ ._-])S(?P<season>\d{1,2})E\d{1,3}(?=\D|$)", re.IGNORECASE)
YEAR = re.compile(r"(?:^|[ ._-])\(?(?P<year>(?:19|20)\d{2})\)?$", re.IGNORECASE)
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mp4", ".ts", ".webm"}


def episode_folder(filename: str, existing_folders: set[str]) -> tuple[str, str] | None:
    if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    match = EPISODE.search(Path(filename).stem)
    if not match:
        return None
    prefix = Path(filename).stem[: match.start()].strip(" ._-")
    year_match = YEAR.search(prefix)
    year = year_match.group("year") if year_match else None
    if year_match:
        prefix = prefix[: year_match.start()]
    name = re.sub(r"\s+", " ", re.sub(r"[._-]+", " ", prefix)).strip()
    name = re.sub(r'[<>:"/\\|?*]', "", name).strip()
    if not name or name in {".", ".."}:
        return None
    proposed = f"{name} ({year})" if year else name
    key = lambda value: re.sub(r"[^a-z0-9]", "", value.lower().replace("&", "and"))
    show = next((folder for folder in existing_folders if key(folder) == key(proposed)), None)
    if show is None:
        matches = [
            folder
            for folder in existing_folders
            if key(re.sub(r"\s*\(\d{4}\)$", "", folder)) == key(name)
        ]
        show = matches[0] if len(matches) == 1 else proposed
    return show, f"Season {int(match.group('season')):02d}"


async def organize_once(qb: QBittorrentClient, settings: Settings) -> int:
    root = Path(settings.series_path)
    destination = Destination("series", "Series", settings.series_path, settings.series_qb_path, settings.series_volume_id)
    if not validate_storage(destination, probe_write=False).mounted:
        return 0
    existing = {path.name for path in root.iterdir() if path.is_dir()}
    moved = 0
    for torrent in await qb.torrents():
        if torrent.get("save_path", "").rstrip("/") != settings.series_qb_path.rstrip("/"):
            continue
        if float(torrent.get("progress") or 0) < 1 or torrent.get("auto_tmm"):
            continue
        content_path = PurePosixPath(torrent.get("content_path") or "")
        if str(content_path.parent) != settings.series_qb_path.rstrip("/"):
            continue
        folder = episode_folder(content_path.name, existing)
        if not folder:
            continue
        show, season = folder
        source = root / content_path.name
        target_dir = root / show / season
        if not source.is_file() or (target_dir / source.name).exists():
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        location = f"{settings.series_qb_path.rstrip('/')}/{show}/{season}"
        await qb.set_location(torrent["hash"], location)
        existing.add(show)
        moved += 1
        logger.info("Requested move of completed episode %s to %s", content_path.name, location)
    return moved


async def run() -> None:
    settings = get_settings()
    qb = QBittorrentClient(settings.qb_url, settings.qb_username, settings.qb_password)
    try:
        while True:
            try:
                await organize_once(qb, settings)
            except (QBittorrentError, OSError, ValueError):
                logger.exception("Could not organize completed episodes")
            await asyncio.sleep(60)
    finally:
        await qb.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
