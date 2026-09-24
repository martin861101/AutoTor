import asyncio
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

from .magnet import MagnetError, extract_magnet, validate_magnet


class FetchError(ValueError):
    pass


EPISODE_PATTERN = re.compile(r"(?<![A-Z0-9])S(\d{1,3})[ ._-]*E(\d{1,3})(?!\d)", re.IGNORECASE)
MAX_BULK_LINKS = 100


@dataclass(frozen=True)
class EpisodeLink:
    title: str
    url: str
    season: int
    episode: int


class _PageLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._title = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        self._href = values.get("href")
        self._title = values.get("title") or ""
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = " ".join("".join(self._text).split())
        self.links.append((self._href, self._title.strip() or text))
        self._href = None
        self._title = ""
        self._text = []


def parse_episode_code(value: str) -> tuple[int, int]:
    match = EPISODE_PATTERN.fullmatch(value.strip())
    if not match:
        raise FetchError("Use an episode code such as S11E01.")
    return int(match.group(1)), int(match.group(2))


def _episode_range(start: str, end: str) -> tuple[int, int, int]:
    start_season, start_episode = parse_episode_code(start)
    end_season, end_episode = parse_episode_code(end)
    if start_season != end_season:
        raise FetchError("Bulk ranges must start and end in the same season.")
    if start_episode > end_episode:
        raise FetchError("The start episode must not come after the end episode.")
    if end_episode - start_episode + 1 > MAX_BULK_LINKS:
        raise FetchError(f"A bulk range can contain at most {MAX_BULK_LINKS} episodes.")
    return start_season, start_episode, end_episode


def _matching_episode(title: str, season: int, first: int, last: int) -> int | None:
    for match in EPISODE_PATTERN.finditer(title):
        if int(match.group(1)) == season and first <= int(match.group(2)) <= last:
            return int(match.group(2))
    return None


def extract_episode_links(document: str, base_url: str, start: str, end: str) -> list[EpisodeLink]:
    season, first, last = _episode_range(start, end)
    parser = _PageLinkParser()
    parser.feed(document)
    matches: list[EpisodeLink] = []
    seen: set[str] = set()
    for href, title in parser.links:
        episode = _matching_episode(title, season, first, last)
        url = urljoin(base_url, href.strip())
        if episode is None or url in seen or urlparse(url).scheme not in {"http", "https", "magnet"}:
            continue
        seen.add(url)
        matches.append(EpisodeLink(title, url, season, episode))
    matches.sort(key=lambda item: item.episode)
    return matches[:MAX_BULK_LINKS]


def _thepiratebay_api_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname not in {"thepiratebay.org", "www.thepiratebay.org"} or parsed.path != "/description.php":
        return None
    torrent_ids = parse_qs(parsed.query).get("id", [])
    if len(torrent_ids) != 1 or not torrent_ids[0].isdigit():
        return None
    return f"https://apibay.org/t.php?id={torrent_ids[0]}"


def _thepiratebay_search_api_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname not in {"thepiratebay.org", "www.thepiratebay.org"} or parsed.path != "/search.php":
        return None
    query = parse_qs(parsed.query)
    terms = query.get("q", [])
    if len(terms) != 1 or not terms[0].strip():
        return None
    parameters = {"q": terms[0]}
    categories = query.get("cat", [])
    if len(categories) == 1 and categories[0].isdigit():
        parameters["cat"] = categories[0]
    return f"https://apibay.org/q.php?{urlencode(parameters)}"


def _thepiratebay_magnet(metadata: object) -> str:
    if not isinstance(metadata, dict):
        raise FetchError("The Pirate Bay returned invalid torrent metadata.")
    info_hash = str(metadata.get("info_hash", ""))
    name = str(metadata.get("name", ""))
    parameters = {"xt": f"urn:btih:{info_hash}", "dn": name}
    size = str(metadata.get("size", ""))
    if size.isdigit() and int(size) > 0:
        parameters["xl"] = size
    try:
        return validate_magnet(f"magnet:?{urlencode(parameters)}")
    except MagnetError as exc:
        raise FetchError("The Pirate Bay returned invalid torrent metadata.") from exc


def _thepiratebay_episode_links(metadata: object, start: str, end: str) -> list[EpisodeLink]:
    season, first, last = _episode_range(start, end)
    if not isinstance(metadata, list):
        raise FetchError("The Pirate Bay returned invalid search results.")
    matches: list[EpisodeLink] = []
    seen: set[str] = set()
    for item in metadata:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name", "")).strip()
        torrent_id = str(item.get("id", ""))
        episode = _matching_episode(title, season, first, last)
        if episode is None or not torrent_id.isdigit() or torrent_id in seen:
            continue
        seen.add(torrent_id)
        matches.append(
            EpisodeLink(title, f"https://thepiratebay.org/description.php?id={torrent_id}", season, episode)
        )
    matches.sort(key=lambda item: item.episode)
    return matches[:MAX_BULK_LINKS]


def _validate_url_shape(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise FetchError("Only HTTP and HTTPS webpage URLs are supported.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise FetchError("Enter a public webpage URL without embedded credentials.")


async def validate_public_url(url: str) -> None:
    _validate_url_shape(url)
    host = urlparse(url).hostname
    assert host is not None
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError("The webpage host could not be resolved.") from exc
    if not addresses:
        raise FetchError("The webpage host could not be resolved.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise FetchError("Private, local, loopback and reserved webpage addresses are blocked.")


async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    declared_size = response.headers.get("content-length")
    if declared_size:
        try:
            if int(declared_size) > max_bytes:
                raise FetchError("The webpage is too large to inspect safely.")
        except ValueError:
            raise FetchError("The webpage returned an invalid content length.")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise FetchError("The webpage is too large to inspect safely.")
    return bytes(body)


async def scan_episode_links(url: str, start: str, end: str, timeout: float, max_bytes: int) -> list[EpisodeLink]:
    _episode_range(start, end)
    current = url.strip()
    headers = {"User-Agent": "AutoTor/1.0 (+LAN torrent manager)", "Accept": "text/html,application/xhtml+xml"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _ in range(6):
            await validate_public_url(current)
            try:
                api_url = _thepiratebay_search_api_url(current)
                if api_url:
                    await validate_public_url(api_url)
                    async with client.stream("GET", api_url) as response:
                        response.raise_for_status()
                        body = await _read_limited(response, max_bytes)
                    try:
                        return _thepiratebay_episode_links(json.loads(body), start, end)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise FetchError("The Pirate Bay returned invalid search results.") from exc

                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("The webpage returned an invalid redirect.")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    if "html" not in response.headers.get("content-type", "").lower():
                        raise FetchError("The URL did not return an HTML webpage.")
                    body = await _read_limited(response, max_bytes)
                    encoding = response.encoding or "utf-8"
                    return extract_episode_links(body.decode(encoding, errors="replace"), current, start, end)
            except httpx.TimeoutException as exc:
                raise FetchError("The webpage request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                raise FetchError(f"The webpage returned HTTP {exc.response.status_code}.") from exc
            except httpx.RequestError as exc:
                raise FetchError("The webpage could not be fetched.") from exc
        raise FetchError("The webpage redirected too many times.")


async def fetch_magnet(url: str, timeout: float, max_bytes: int) -> str:
    current = url.strip()
    headers = {"User-Agent": "AutoTor/1.0 (+LAN torrent manager)", "Accept": "text/html,application/xhtml+xml"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _ in range(6):
            await validate_public_url(current)
            try:
                api_url = _thepiratebay_api_url(current)
                if api_url:
                    await validate_public_url(api_url)
                    async with client.stream("GET", api_url) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise FetchError("The webpage is too large to inspect safely.")
                        try:
                            return _thepiratebay_magnet(json.loads(body))
                        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                            raise FetchError("The Pirate Bay returned invalid torrent metadata.") from exc

                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("The webpage returned an invalid redirect.")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if "html" not in content_type:
                        raise FetchError("The URL did not return an HTML webpage.")
                    declared_size = response.headers.get("content-length")
                    if declared_size:
                        try:
                            if int(declared_size) > max_bytes:
                                raise FetchError("The webpage is too large to inspect safely.")
                        except ValueError:
                            raise FetchError("The webpage returned an invalid content length.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError("The webpage is too large to inspect safely.")
                    encoding = response.encoding or "utf-8"
                    return extract_magnet(body.decode(encoding, errors="replace"))
            except httpx.TimeoutException as exc:
                raise FetchError("The webpage request timed out. Try a direct magnet URI.") from exc
            except httpx.HTTPStatusError as exc:
                raise FetchError(f"The webpage returned HTTP {exc.response.status_code}. Try a direct magnet URI.") from exc
            except httpx.RequestError as exc:
                raise FetchError("The webpage could not be fetched. Try a direct magnet URI.") from exc
        raise FetchError("The webpage redirected too many times.")
