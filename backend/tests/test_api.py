from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.fetcher import EpisodeLink
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

    async def fake_scan(*_args):
        return [
            EpisodeLink("Show S11E01", "https://example.com/1", 11, 1),
            EpisodeLink("Show S11E02", "https://example.com/2", 11, 2),
        ]

    async def fake_fetch(url, *_args):
        info_hash = HASH if url.endswith("/1") else second_hash
        return f"magnet:?xt=urn:btih:{info_hash}&xl=100"

    monkeypatch.setattr(main, "scan_episode_links", fake_scan)
    monkeypatch.setattr(main, "fetch_magnet", fake_fetch)
    monkeypatch.setattr(main, "ensure_storage", lambda *_args: None)
    qb = FakeQB([{"hash": HASH, "save_path": "/downloads/series"}])
    client = client_with(qb)

    response = client.post(
        "/api/downloads/bulk",
        json={"source": "https://example.com/list", "destination": "series", "start": "S11E01", "end": "S11E02"},
    )

    assert response.status_code == 200
    assert len(response.json()["added"]) == 1
    assert len(response.json()["skipped"]) == 1
    assert qb.added == [(f"magnet:?xt=urn:btih:{second_hash}&xl=100", "/downloads/series")]
    app.dependency_overrides.clear()
