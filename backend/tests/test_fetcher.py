from urllib.parse import parse_qs, urlparse

import pytest

from app.fetcher import (
    BulkLink,
    FetchError,
    _thepiratebay_search_api_url,
    _thepiratebay_api_url,
    _thepiratebay_magnet,
    extract_bulk_links,
    _thepiratebay_bulk_links,
    select_bulk_links,
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


def test_extracts_any_torrent_links_and_deduplicates_urls():
    document = """
        <a href="/movie">Example Movie</a>
        <a href="/album" title="Example Album">Download</a>
        <a href="/movie">Example Movie duplicate</a>
        <a href="javascript:alert(1)">Invalid</a>
    """
    links = extract_bulk_links(document, "https://example.com/list")
    assert [(item.title, item.url) for item in links] == [
        ("Example Movie", "https://example.com/movie"),
        ("Example Album", "https://example.com/album"),
    ]


def test_thepiratebay_bulk_links_include_non_episodes():
    links = _thepiratebay_bulk_links([
        {"id": "1", "name": "Example Movie"},
        {"id": "2", "name": "Example Album"},
        {"id": "1", "name": "Duplicate"},
    ])
    assert [link.title for link in links] == ["Example Movie", "Example Album"]


def test_bulk_filters_select_one_lantern_release_per_episode():
    links = [
        *[BulkLink(f"Lantern.S01E{episode:02d}.1080p.WEB-DL", f"https://example.com/{episode}") for episode in range(1, 6)],
        BulkLink("Lantern.S01E02.1080p.WEB-DL.x265", "https://example.com/variant"),
        BulkLink("Lantern.S01E03.720p.WEB-DL", "https://example.com/low"),
        BulkLink("Other.S01E04.1080p.WEB-DL", "https://example.com/other"),
        BulkLink("Lantern.S01E06.1080p.WEB-DL", "https://example.com/outside"),
    ]
    selected = select_bulk_links(links, "S01E01", "S01E05", "lantern", ("1080p",), "WEB-DL")
    assert [link.episode_key for link in selected] == [("lantern", 1, episode) for episode in range(1, 6)]
    assert len(selected) == 5


def test_bulk_filters_can_be_used_without_a_range():
    links = [BulkLink("Lantern Movie 1080p", "/one"), BulkLink("Other Movie 720p", "/two")]
    assert [link.title for link in select_bulk_links(links, must_include="Lantern")] == ["Lantern Movie 1080p"]
    assert len(select_bulk_links(links)) == 2
    assert len(select_bulk_links(links, resolutions=("720p", "1080p"))) == 2
    assert len(select_bulk_links([BulkLink("Lantern Movie 4K", "/4k")], resolutions=("2160p",))) == 1


def test_bulk_limit_applies_after_filtering():
    links = [BulkLink(f"Other {index}", f"https://example.com/{index}") for index in range(100)]
    links.append(BulkLink("Lantern S01E01", "https://example.com/lantern"))
    assert [link.title for link in select_bulk_links(links, must_include="Lantern")] == ["Lantern S01E01"]


def test_bulk_range_requires_two_ordered_codes_in_one_season():
    with pytest.raises(FetchError, match="both episode codes"):
        select_bulk_links([], "S01E01")
    with pytest.raises(FetchError, match="ordered within one season"):
        select_bulk_links([], "S02E01", "S01E05")


def test_builds_thepiratebay_search_api_url():
    url = "https://thepiratebay.org/search.php?q=Example+S11&cat=205"
    assert _thepiratebay_search_api_url(url) == "https://apibay.org/q.php?q=Example+S11&cat=205"
