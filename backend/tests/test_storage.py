from pathlib import Path

import pytest

from app import storage
from app.storage import Destination, StorageError, StorageStatus, ensure_storage, mount_details


def test_mount_validation_uses_most_specific_mount(tmp_path: Path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 0:1 / / rw - ext4 /dev/root rw\n"
        "2 1 0:2 / /storage/series rw - cifs //server/Series rw\n",
        encoding="utf-8",
    )
    assert mount_details("/storage/series/show", str(mountinfo)) == ("cifs", "//server/Series")


def test_storage_fails_closed_when_not_cifs(tmp_path: Path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("1 0 0:1 / / rw - ext4 /dev/root rw\n", encoding="utf-8")
    assert mount_details("/storage/series", str(mountinfo)) == ("ext4", "/dev/root")


def test_ensure_storage_checks_capacity(monkeypatch):
    destination = Destination("series", "Series", "/storage/series", "/downloads/series", "plex")
    monkeypatch.setattr(
        storage,
        "validate_storage",
        lambda _destination: StorageStatus("series", "Series", "/downloads/series", "plex", True, True, 100, 1000),
    )
    with pytest.raises(StorageError, match="enough"):
        ensure_storage(destination, required_bytes=60, reserved_bytes=50)

