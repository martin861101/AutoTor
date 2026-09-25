import asyncio
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

import httpx

from .magnet import MagnetError, extract_magnet, validate_magnet


class FetchError(ValueError):
    pass


MAX_BULK_LINKS = 100
EPISODE_PATTERN = re.compile(r"(?<![A-Z0-9])S(\d{1,3})[ ._-]*E(\d{1,3})(?!\d)", re.IGNORECASE)
RESOLUTION_TERMS = {
    "480p": ("480p",), "720p": ("720p",), "1080p": ("1080p",), "2160p": ("2160p", "4k"),
}


@dataclass(frozen=True)
class BulkLink:
    title: str
    url: str
    episode_key: tuple[str, int, int] | None = None


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


def extract_bulk_links(document: str, base_url: str) -> list[BulkLink]:
    parser = _PageLinkParser()
    parser.feed(document)
    matches: list[BulkLink] = []
    seen: set[str] = set()
    for href, title in parser.links:
        if not href.strip():
            continue
        url = urljoin(base_url, href.strip())
        if url in seen or urlparse(url).scheme not in {"http", "https", "magnet"}:
            continue
        seen.add(url)
        matches.append(BulkLink(title or url, url))
    return matches


def episode_identity(title: str, must_include: str = "") -> tuple[str, int, int] | None:
    match = EPISODE_PATTERN.search(title)
    if not match:
        return None
    show = must_include.strip().casefold() or re.sub(r"[^a-z0-9]+", " ", title[:match.start()].casefold()).strip()
    return show, int(match.group(1)), int(match.group(2))


def select_bulk_links(
    links: list[BulkLink], start: str | None = None, end: str | None = None,
    must_include: str = "", resolutions: tuple[str, ...] = (), other_filter: str = "",
) -> list[BulkLink]:
    first = last = None
    season = None
    if start is not None or end is not None:
        start_match = EPISODE_PATTERN.fullmatch((start or "").strip())
        end_match = EPISODE_PATTERN.fullmatch((end or "").strip())
        if not start_match or not end_match:
            raise FetchError("Enter both episode codes, such as S01E01 and S01E05.")
        season, first = int(start_match.group(1)), int(start_match.group(2))
        end_season, last = int(end_match.group(1)), int(end_match.group(2))
        if season != end_season or first > last:
            raise FetchError("The episode range must be ordered within one season.")
        if last - first + 1 > MAX_BULK_LINKS:
            raise FetchError(f"An episode range can contain at most {MAX_BULK_LINKS} episodes.")
    if any(resolution not in RESOLUTION_TERMS for resolution in resolutions):
        raise FetchError("Choose a supported resolution filter.")

    selected: list[BulkLink] = []
    seen_urls: set[str] = set()
    seen_episodes: set[tuple[str, int, int]] = set()
    include = must_include.strip().casefold()
    extra = other_filter.strip().casefold()
    for link in links:
        parsed_url = urlparse(link.url)
        magnet_name = parse_qs(parsed_url.query).get("dn", [""])[0] if parsed_url.scheme == "magnet" else ""
        searchable = f"{link.title} {magnet_name} {unquote(parsed_url.path)}".casefold()
        if include and include not in searchable:
            continue
        if extra and extra not in searchable:
            continue
        if resolutions and not any(
            re.search(rf"(?<![a-z0-9]){term}(?![a-z0-9])", searchable)
            for value in resolutions for term in RESOLUTION_TERMS[value]
        ):
            continue
        identity = episode_identity(link.title or magnet_name, include) if season is not None else None
        if season is not None:
            if identity is None:
                identity = episode_identity(searchable, include)
            if identity is None or identity[1] != season or not first <= identity[2] <= last:
                continue
        if link.url in seen_urls or (identity is not None and identity in seen_episodes):
            continue
        seen_urls.add(link.url)
        if identity is not None:
            seen_episodes.add(identity)
        selected.append(BulkLink(link.title, link.url, identity))
        if len(selected) == MAX_BULK_LINKS:
            break
    return selected


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


def _thepiratebay_bulk_links(metadata: object) -> list[BulkLink]:
    if not isinstance(metadata, list):
        raise FetchError("The Pirate Bay returned invalid search results.")
    matches: list[BulkLink] = []
    seen: set[str] = set()
    for item in metadata:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name", "")).strip()
        torrent_id = str(item.get("id", ""))
        if not torrent_id.isdigit() or torrent_id in seen:
            continue
        seen.add(torrent_id)
        matches.append(
            BulkLink(title, f"https://thepiratebay.org/description.php?id={torrent_id}")
        )
    return matches


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


async def scan_bulk_links(
    url: str, timeout: float, max_bytes: int, *, start: str | None = None, end: str | None = None,
    must_include: str = "", resolutions: tuple[str, ...] = (), other_filter: str = "",
) -> list[BulkLink]:
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
                        return select_bulk_links(
                            _thepiratebay_bulk_links(json.loads(body)), start, end, must_include, resolutions, other_filter
                        )
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
                    return select_bulk_links(
                        extract_bulk_links(body.decode(encoding, errors="replace"), current),
                        start, end, must_include, resolutions, other_filter,
                    )
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
