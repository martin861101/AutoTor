from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.fetcher import BulkLink
from app.main import app, get_qb


HASH = "0123456789abcdef0123456789abcdef01234567"


class FakeQB:
    def __init__(self, torrents=None):
        self._torrents = torrents or []
        self.added = []

    async def torrents(self):
        return self._torrents

    async def add(self, magnet, save_path):
        self.added.append((magnet, save_path))


def client_with(qb):
    app.state.settings = Settings()
    app.state.storage_holds = set()
    app.dependency_overrides[get_qb] = lambda: qb
    return TestClient(app, client=("127.0.0.1", 50000))


def test_destination_selection_is_required_and_restricted():
    client = client_with(FakeQB())
    response = client.post("/api/downloads", json={"source": f"magnet:?xt=urn:btih:{HASH}", "destination": "music"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Choose Movies or Series."
    app.dependency_overrides.clear()


def test_duplicate_detection_uses_info_hash():
    client = client_with(FakeQB([{"hash": HASH.upper()}]))
    response = client.post("/api/downloads", json={"source": f"magnet:?xt=urn:btih:{HASH}", "destination": "series"})
    assert response.status_code == 409
    assert "already" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_delete_files_requires_explicit_confirmation():
    qb = FakeQB([{"hash": HASH, "save_path": "/downloads/series"}])
    client = client_with(qb)
    response = client.post(
        f"/api/torrents/{HASH}/remove",
        json={"delete_files": True, "confirm_delete_files": False},
    )
    assert response.status_code == 422
    assert "Confirm" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_bulk_download_adds_matches_and_skips_duplicates(monkeypatch):
    second_hash = "abcdef0123456789abcdef0123456789abcdef01"
    third_hash = "fedcba9876543210fedcba9876543210fedcba98"
    fourth_hash = "1111111111111111111111111111111111111111"

    async def fake_scan(*_args, **_kwargs):
        return [
            BulkLink("Example Movie", "https://example.com/1"),
            BulkLink("Example Album", "https://example.com/2"),
            BulkLink("Same album, another link", "https://example.com/3"),
            BulkLink("Same album, another torrent", "https://example.com/4"),
            BulkLink("Same movie, another torrent", "https://example.com/5"),
        ]

    async def fake_fetch(url, *_args):
        hashes = {"1": HASH, "2": second_hash, "3": second_hash, "4": third_hash, "5": fourth_hash}
        info_hash = hashes[url.rsplit("/", 1)[-1]]
        name = "Example Movie" if url.endswith(("/1", "/5")) else "Example Album"
        return f"magnet:?xt=urn:btih:{info_hash}&dn={name.replace(' ', '+')}&xl=100"

    monkeypatch.setattr(main, "scan_bulk_links", fake_scan)
    monkeypatch.setattr(main, "fetch_magnet", fake_fetch)
    monkeypatch.setattr(main, "ensure_storage", lambda *_args: None)
    qb = FakeQB([{"hash": HASH, "name": "Example Movie", "save_path": "/downloads/series"}])
    client = client_with(qb)

    response = client.post(
        "/api/downloads/bulk",
        json={"source": "https://example.com/list", "destination": "series"},
    )

    assert response.status_code == 200
    assert len(response.json()["added"]) == 1
    assert len(response.json()["skipped"]) == 4
    assert qb.added == [(f"magnet:?xt=urn:btih:{second_hash}&dn=Example+Album&xl=100", "/downloads/series")]
    app.dependency_overrides.clear()


def test_bulk_range_skips_episode_already_in_qbittorrent(monkeypatch):
    async def fake_scan(*_args, **kwargs):
        assert kwargs == {
            "start": "S01E01", "end": "S01E05", "must_include": "Lantern",
            "resolutions": ("1080p",), "other_filter": "WEB-DL",
        }
        return [BulkLink("Lantern S01E02 1080p WEB-DL", "https://example.com/2", ("lantern", 1, 2))]

    async def fake_fetch(*_args):
        return f"magnet:?xt=urn:btih:{HASH}&dn=Lantern+S01E02+1080p+WEB-DL"

    monkeypatch.setattr(main, "scan_bulk_links", fake_scan)
    monkeypatch.setattr(main, "fetch_magnet", fake_fetch)
    qb = FakeQB([{
        "hash": "abcdef0123456789abcdef0123456789abcdef01",
        "name": "Lantern S01E02 720p WEB-DL", "save_path": "/downloads/series",
    }])
    client = client_with(qb)
    response = client.post("/api/downloads/bulk", json={
        "source": "https://example.com/list", "destination": "series",
        "start": "S01E01", "end": "S01E05", "must_include": "Lantern",
        "resolutions": ["1080p"], "other_filter": "WEB-DL",
    })
    assert response.status_code == 200
    assert len(response.json()["skipped"]) == 1
    assert qb.added == []
    app.dependency_overrides.clear()
