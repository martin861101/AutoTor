import pytest

from app.magnet import MagnetError, extract_magnet, magnet_info_hash, validate_magnet


HASH = "0123456789abcdef0123456789abcdef01234567"


def test_extracts_magnet_from_anchor_and_decodes_entities():
    document = f'<a class="anything" href="magnet:?xt=urn:btih:{HASH}&amp;dn=Example">Get it</a>'
    assert extract_magnet(document) == f"magnet:?xt=urn:btih:{HASH}&dn=Example"


def test_extracts_magnet_from_non_anchor_attribute():
    document = f'<meta content="magnet:?xt=urn:btih:{HASH}&amp;dn=Example">'
    assert magnet_info_hash(extract_magnet(document)) == HASH


@pytest.mark.parametrize("value", ["", "https://example.com", "magnet:?dn=missing", "magnet:?xt=urn:btih:nope"])
def test_rejects_invalid_magnets(value):
    with pytest.raises(MagnetError):
        validate_magnet(value)


def test_reports_javascript_or_blocked_page():
    with pytest.raises(MagnetError, match="JavaScript"):
        extract_magnet("<html><script>renderLink()</script></html>")

