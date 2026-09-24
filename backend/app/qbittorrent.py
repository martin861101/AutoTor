from collections.abc import Iterable

import httpx


class QBittorrentError(RuntimeError):
    pass


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        self._authenticated = False

    async def close(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        try:
            response = await self._client.post(
                "/api/v2/auth/login", data={"username": self.username, "password": self.password}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise QBittorrentError("Could not connect to qBittorrent.") from exc
        if response.status_code != 204 and response.text.strip() != "Ok.":
            raise QBittorrentError("qBittorrent rejected the configured credentials.")
        self._authenticated = True

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._authenticated:
            await self._login()
        try:
            response = await self._client.request(method, path, **kwargs)
            if response.status_code in {401, 403}:
                self._authenticated = False
                await self._login()
                response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise QBittorrentError("qBittorrent API request failed.") from exc

    async def torrents(self) -> list[dict]:
        response = await self._request("GET", "/api/v2/torrents/info")
        return response.json()

    async def add(self, magnet: str, save_path: str) -> None:
        response = await self._request(
            "POST",
            "/api/v2/torrents/add",
            data={"urls": magnet, "savepath": save_path, "paused": "false"},
        )
        body = response.text.strip()
        if body.lower() in {"ok.", ""}:
            return
        try:
            summary = response.json()
        except ValueError:
            summary = None
        if isinstance(summary, dict) and summary.get("success_count") == 1:
            return
        raise QBittorrentError("qBittorrent did not accept the magnet URI.")

    async def action(self, action: str, hashes: Iterable[str], delete_files: bool = False) -> None:
        joined = "|".join(hashes)
        if action == "delete":
            data = {"hashes": joined, "deleteFiles": str(delete_files).lower()}
        else:
            data = {"hashes": joined}
        try:
            await self._request("POST", f"/api/v2/torrents/{action}", data=data)
        except QBittorrentError:
            fallback = {"pause": "stop", "resume": "start"}.get(action)
            if not fallback:
                raise
            await self._request("POST", f"/api/v2/torrents/{fallback}", data=data)

    async def version(self) -> str:
        response = await self._request("GET", "/api/v2/app/version")
        return response.text.strip()
