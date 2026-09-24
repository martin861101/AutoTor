from urllib.parse import parse_qs, urlparse

import pytest

from app.fetcher import (
    FetchError,
    _thepiratebay_search_api_url,
    _thepiratebay_api_url,
    _thepiratebay_magnet,
    extract_episode_links,
    validate_public_url,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/file", "http://user:pass@example.com"])
async def test_rejects_invalid_webpage_urls(url):
    with pytest.raises(FetchError):
        await validate_public_url(url)


@pytest.mark.asyncio
async def test_rejects_loopback_url():
    with pytest.raises(FetchError, match="blocked"):
        await validate_public_url("http://127.0.0.1/private")


def test_builds_magnet_from_thepiratebay_metadata():
    url = "https://thepiratebay.org/description.php?id=82669268"
    assert _thepiratebay_api_url(url) == "https://apibay.org/t.php?id=82669268"

    magnet = _thepiratebay_magnet(
        {"info_hash": "0123456789abcdef0123456789abcdef01234567", "name": "Example torrent", "size": "123"}
    )
    parameters = parse_qs(urlparse(magnet).query)
    assert parameters == {
        "xt": ["urn:btih:0123456789abcdef0123456789abcdef01234567"],
        "dn": ["Example torrent"],
        "xl": ["123"],
    }


def test_rejects_invalid_thepiratebay_metadata():
    with pytest.raises(FetchError, match="invalid torrent metadata"):
        _thepiratebay_magnet({"info_hash": "invalid", "name": "Example"})


def test_extracts_and_sorts_links_in_episode_range():
    document = """
        <a href="/show-s11e03">Show S11E03</a>
        <a href="/show-s11e01" title="Show.S11E01.1080p">Download</a>
        <a href="/show-s11e12">Show S11E12</a>
        <a href="/show-s11e03">Show S11E03 duplicate</a>
    """
    links = extract_episode_links(document, "https://example.com/list", "S11E01", "s11e11")
    assert [(item.episode, item.url) for item in links] == [
        (1, "https://example.com/show-s11e01"),
        (3, "https://example.com/show-s11e03"),
    ]


def test_rejects_cross_season_bulk_range():
    with pytest.raises(FetchError, match="same season"):
        extract_episode_links("", "https://example.com", "S10E10", "S11E02")


def test_builds_thepiratebay_search_api_url():
    url = "https://thepiratebay.org/search.php?q=Example+S11&cat=205"
    assert _thepiratebay_search_api_url(url) == "https://apibay.org/q.php?q=Example+S11&cat=205"
