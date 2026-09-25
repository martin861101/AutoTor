from types import SimpleNamespace

import pytest

from app import organizer
from app.organizer import episode_folder


def test_episode_folder_uses_existing_show_folder():
    assert episode_folder(
        "Lanterns.2026.S01E07.1080p.mkv", {"Lanterns (2026)"}
    ) == ("Lanterns (2026)", "Season 01")
    assert episode_folder("Lanterns.S01E08.mkv", {"Lanterns (2026)"}) == (
        "Lanterns (2026)", "Season 01"
    )
    assert episode_folder(
        "Law.and.Order.SVU.S25E14.mkv", {"Law & Order SVU"}
    ) == ("Law & Order SVU", "Season 25")


def test_episode_folder_skips_non_episode_media():
    assert episode_folder("Moment.of.Contact.2022.mkv", set()) is None
    assert episode_folder("Show.S01E02.nfo", set()) is None


@pytest.mark.asyncio
async def test_organize_completed_episode_uses_qbittorrent_move(tmp_path, monkeypatch):
    (tmp_path / "Lanterns.2026.S01E07.mkv").write_bytes(b"episode")
    monkeypatch.setattr(organizer, "validate_storage", lambda *_args, **_kwargs: SimpleNamespace(mounted=True))
    settings = SimpleNamespace(
        series_path=str(tmp_path), series_qb_path="/downloads/series", series_volume_id="media"
    )

    class FakeQB:
        moves = []

        async def torrents(self):
            return [
                {
                    "hash": "abc",
                    "save_path": "/downloads/series",
                    "content_path": "/downloads/series/Lanterns.2026.S01E07.mkv",
                    "progress": 1,
                    "auto_tmm": False,
                },
                {
                    "hash": "incomplete",
                    "save_path": "/downloads/series",
                    "content_path": "/downloads/series/Show.S01E01.mkv",
                    "progress": 0.5,
                },
            ]

        async def set_location(self, info_hash, location):
            self.moves.append((info_hash, location))

    qb = FakeQB()
    assert await organizer.organize_once(qb, settings) == 1
    assert qb.moves == [("abc", "/downloads/series/Lanterns (2026)/Season 01")]
