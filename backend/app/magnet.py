import base64
import binascii
import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse


MAGNET_PATTERN = re.compile(r"magnet:\?[^\s\"'<>]+", re.IGNORECASE)
HEX_HASH = re.compile(r"^[0-9a-fA-F]{40}$")
BASE32_HASH = re.compile(r"^[A-Z2-7]{32}$", re.IGNORECASE)


class MagnetError(ValueError):
    pass


def magnet_info_hash(uri: str) -> str:
    if not isinstance(uri, str) or not uri.lower().startswith("magnet:?"):
        raise MagnetError("Enter a valid magnet URI beginning with magnet:?")

    parsed = urlparse(uri)
    if parsed.scheme.lower() != "magnet":
        raise MagnetError("Only magnet URIs are supported.")

    values = parse_qs(parsed.query, keep_blank_values=True).get("xt", [])
    btih = next(
        (unquote(value).split(":")[-1] for value in values if unquote(value).lower().startswith("urn:btih:")),
        None,
    )
    if not btih:
        raise MagnetError("The magnet URI is missing a BitTorrent info hash.")

    if HEX_HASH.fullmatch(btih):
        return btih.lower()
    if BASE32_HASH.fullmatch(btih):
        try:
            return base64.b32decode(btih.upper()).hex()
        except binascii.Error as exc:
            raise MagnetError("The magnet URI contains an invalid info hash.") from exc
    raise MagnetError("The magnet URI contains an invalid info hash.")


def validate_magnet(uri: str) -> str:
    cleaned = html.unescape(uri).strip()
    if len(cleaned) > 16_384:
        raise MagnetError("The magnet URI is too long.")
    magnet_info_hash(cleaned)
    return cleaned


def magnet_size(uri: str) -> int:
    values = parse_qs(urlparse(uri).query, keep_blank_values=True).get("xl", [])
    if not values:
        return 0
    try:
        size = int(values[0])
    except (TypeError, ValueError):
        return 0
    return size if size > 0 else 0


class _MagnetHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for _name, value in attrs:
            if value and value.lstrip().lower().startswith("magnet:?"):
                self.candidates.append(value)

    def handle_data(self, data: str) -> None:
        self.candidates.extend(MAGNET_PATTERN.findall(data))


def extract_magnet(document: str) -> str:
    decoded = html.unescape(document)
    parser = _MagnetHTMLParser()
    parser.feed(decoded)
    candidates = parser.candidates + MAGNET_PATTERN.findall(decoded)
    for candidate in candidates:
        try:
            return validate_magnet(candidate)
        except MagnetError:
            continue
    raise MagnetError(
        "No valid magnet link was found. The page may require JavaScript or may block automated access."
    )
