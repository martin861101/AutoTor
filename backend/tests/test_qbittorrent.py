import httpx
import pytest

from app.qbittorrent import QBittorrentClient, QBittorrentError


@pytest.mark.asyncio
async def test_qbittorrent_login_and_api_handling():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/torrents/info"):
            return httpx.Response(200, json=[{"hash": "abc"}])
        return httpx.Response(404)

    client = QBittorrentClient("http://qb", "admin", "secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="http://qb", transport=httpx.MockTransport(handler))
    assert await client.torrents() == [{"hash": "abc"}]
    assert requests == ["/api/v2/auth/login", "/api/v2/torrents/info"]
    await client.close()


@pytest.mark.asyncio
async def test_qbittorrent_accepts_empty_204_login_response():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(204)
        if request.url.path.endswith("/app/version"):
            return httpx.Response(200, text="v5.2.3")
        return httpx.Response(404)

    client = QBittorrentClient("http://qb", "admin", "secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="http://qb", transport=httpx.MockTransport(handler))
    assert await client.version() == "v5.2.3"
    await client.close()


@pytest.mark.asyncio
async def test_qbittorrent_accepts_v5_json_add_response():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(204)
        if request.url.path.endswith("/torrents/add"):
            return httpx.Response(200, json={"success_count": 1, "failure_count": 0})
        return httpx.Response(404)

    client = QBittorrentClient("http://qb", "admin", "secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="http://qb", transport=httpx.MockTransport(handler))
    await client.add("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567", "/downloads/series")
    await client.close()


@pytest.mark.asyncio
async def test_qbittorrent_rejects_bad_credentials():
    client = QBittorrentClient("http://qb", "admin", "wrong")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://qb", transport=httpx.MockTransport(lambda _request: httpx.Response(200, text="Fails."))
    )
    with pytest.raises(QBittorrentError, match="credentials"):
        await client.torrents()
    await client.close()


@pytest.mark.asyncio
async def test_pause_falls_back_to_qbittorrent_v5_stop_endpoint():
    paths = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Ok.")
        if request.url.path.endswith("/torrents/pause"):
            return httpx.Response(404)
        if request.url.path.endswith("/torrents/stop"):
            return httpx.Response(200)
        return httpx.Response(500)

    client = QBittorrentClient("http://qb", "admin", "secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="http://qb", transport=httpx.MockTransport(handler))
    await client.action("pause", ["abc"])
    assert paths[-2:] == ["/api/v2/torrents/pause", "/api/v2/torrents/stop"]
    await client.close()
