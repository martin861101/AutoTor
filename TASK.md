Build the AutoTor MVP in "/home/apps/autotor".

Existing infrastructure

- Ubuntu server with Docker Compose.
- Existing qBittorrent container: "autotor-qbittorrent", Web UI/API on port 9090.
- Windows SMB shares already mounted and verified:
  - "/mnt/series" → "\\PLEX.home\Series"
  - "/mnt/movies" → "\\PLEX.home\Movies"
- Inside qBittorrent, these are "/downloads/series" and "/downloads/movies".
- Preserve the existing qBittorrent configuration, credentials, downloads and Docker volumes. Do not recreate or reset its configuration.

Objective

Build a modern, responsive web application accessible on port 9091. Use React/Vite for the frontend and Python/FastAPI for the backend. Communicate with qBittorrent through its Web API.

Core functionality

1. Provide one input accepting either a webpage URL or a direct magnet URI.
2. For webpage URLs, fetch the HTML and extract the magnet URI from an anchor or other supported HTML location. Decode HTML entities correctly. Do not rely on a single website-specific selector.
3. If the page requires JavaScript or blocks extraction, show a useful error. Do not attempt to bypass access controls.
4. Provide a required destination selector: Movies or Series.
5. Submit the magnet URI to qBittorrent with the selected save path.
6. Display the live download queue, including name, total size, progress, speed, ETA and status.
7. Support pause, resume and remove. Require confirmation before deleting downloaded files.
8. Detect duplicate torrents using their info hashes.
9. Show the available space on the selected Windows share.

Storage safety

- Verify the selected destination is an actual CIFS mount before accepting a download.
- Check that the share is writable and has sufficient available space when the torrent size is known.
- Never silently fall back to Ubuntu's local filesystem.
- Handle SMB disconnections, including those occurring during active downloads. Pause affected torrents and require storage revalidation before resuming.
- Account for the possibility that Movies and Series reside on the same Windows volume.
- Implement a fail-closed storage strategy, not just a one-time mount check.

Security

- Keep qBittorrent credentials in backend environment variables or protected configuration.
- Do not expose credentials to the frontend.
- Restrict webpage fetching to HTTP/HTTPS, validate redirects, block loopback/private/internal IPs, and impose response-size and timeout limits to prevent SSRF.
- Validate magnet URIs before submission.
- Restrict the application to trusted LAN access or require authentication.
- Do not expose the qBittorrent API publicly.

Deployment

- Inspect the existing Compose file and project before making changes.
- Integrate the new services without disrupting the existing qBittorrent container.
- Use port 9091 for AutoTor.
- Provide an ".env.example" and concise setup instructions.
- Do not hardcode passwords or secrets.
- Keep the implementation simple; avoid unnecessary databases, external services or frameworks.

Verification

Implement and run tests for magnet extraction, invalid URLs, duplicate detection, destination selection, mount validation and qBittorrent API handling.

Build the frontend and verify that the backend starts.

Confirm that the existing qBittorrent container and both Windows mounts remain operational.

Inspect failures, fix them and rerun the relevant tests. Report any checks that cannot be completed, including live SMB-disconnection testing.

Completion criteria

A user can open AutoTor on port 9091, paste a supported webpage URL or magnet link, select Movies or Series, start a download directly to the corresponding Windows share, and monitor or manage that download.

Do not stop after generating files. Complete implementation and verification.

Example UI visual: ./ui_example.png
