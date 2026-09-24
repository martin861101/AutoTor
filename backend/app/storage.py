import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Destination:
    key: str
    label: str
    host_path: str
    qb_path: str
    volume_id: str


@dataclass
class StorageStatus:
    destination: str
    label: str
    path: str
    volume_id: str
    mounted: bool = False
    writable: bool = False
    available_bytes: int = 0
    total_bytes: int = 0
    error: str | None = None

    def model(self) -> dict:
        return asdict(self)


def _decode_mount_path(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def mount_details(path: str, mountinfo_path: str = "/proc/self/mountinfo") -> tuple[str, str] | None:
    resolved = os.path.realpath(path)
    best: tuple[str, str, str] | None = None
    with open(mountinfo_path, encoding="utf-8") as mountinfo:
        for line in mountinfo:
            left, separator, right = line.partition(" - ")
            if not separator:
                continue
            left_fields = left.split()
            right_fields = right.split()
            if len(left_fields) < 5 or len(right_fields) < 2:
                continue
            mountpoint = _decode_mount_path(left_fields[4])
            if resolved == mountpoint or resolved.startswith(mountpoint.rstrip("/") + "/"):
                candidate = (mountpoint, right_fields[0], right_fields[1])
                if best is None or len(mountpoint) > len(best[0]):
                    best = candidate
    return (best[1], best[2]) if best else None


def validate_storage(destination: Destination, probe_write: bool = True) -> StorageStatus:
    status = StorageStatus(
        destination=destination.key,
        label=destination.label,
        path=destination.qb_path,
        volume_id=destination.volume_id,
    )
    path = Path(destination.host_path)
    try:
        details = mount_details(str(path))
        if not details or details[0].lower() != "cifs":
            raise StorageError("Storage is not mounted as CIFS.")
        status.mounted = True
        if probe_write:
            probe = path / f".autotor-write-check-{secrets.token_hex(8)}"
            try:
                descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(descriptor, b"autotor")
                os.fsync(descriptor)
                os.close(descriptor)
            finally:
                try:
                    probe.unlink()
                except FileNotFoundError:
                    pass
        status.writable = True
        stats = os.statvfs(path)
        status.available_bytes = stats.f_bavail * stats.f_frsize
        status.total_bytes = stats.f_blocks * stats.f_frsize
    except (OSError, StorageError) as exc:
        status.error = str(exc)
    return status


def ensure_storage(destination: Destination, required_bytes: int = 0, reserved_bytes: int = 0) -> StorageStatus:
    status = validate_storage(destination)
    if not status.mounted or not status.writable:
        raise StorageError(status.error or "Storage is unavailable.")
    needed = max(0, required_bytes) + max(0, reserved_bytes)
    if needed and status.available_bytes < needed:
        raise StorageError("The selected storage does not have enough available space.")
    return status

