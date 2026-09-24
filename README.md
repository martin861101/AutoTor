# AutoTor

AutoTor is a LAN-only React and FastAPI interface for the existing qBittorrent container. It accepts a magnet URI or a public webpage containing one, validates the selected CIFS destination, and sends the download to the Movies or Series share. Bulk mode scans a listing page for episode titles within a same-season range such as `S11E01` through `S11E11`, then resolves and adds each matching link.

## Setup

1. Copy `.env.example` to `.env` and enter the **existing** qBittorrent Web UI username and password. Do not change or reset qBittorrent's configuration.
2. Confirm `/mnt/series` and `/mnt/movies` are writable CIFS mounts. AutoTor blocks downloads when either path is not a live, writable CIFS mount.
3. Start the stack:

   ```bash
   docker compose up -d --build
   ```

4. Open `http://<server-lan-address>:9091`.

qBittorrent remains available to AutoTor over the private Compose network. Its host port defaults to `127.0.0.1:9090`; set `QBITTORRENT_BIND_ADDRESS` to a trusted LAN interface in `.env` only if direct Web UI access is required.

Movies and Series default to one capacity group (`plex-media`) so active downloads across both shares are included in free-space checks. Give them different volume IDs only when the shares are backed by different Windows volumes.

## Safety behavior

AutoTor validates CIFS mount type and performs a small create/fsync/delete write probe before adding or resuming a torrent. A background monitor repeats that validation, pauses downloads after a storage failure, and requires a successful live check before resume. Magnet metadata with an exact length is checked before submission; downloads whose size becomes known later are paused if their shared volume cannot hold all remaining data.

Webpage fetching allows only public HTTP/HTTPS targets, checks every redirect, rejects local/private/reserved addresses, and applies response size and timeout limits. Pages that require JavaScript or block access return an actionable error.

## Tests

Run backend tests inside the built API image and build the frontend with:

```bash
docker compose run --rm autotor-api pytest -q -p no:cacheprovider
docker compose build autotor-web
```
