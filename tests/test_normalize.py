"""Normalization tests (URLs, phones, emails, domains, IPv6)."""
from scraper.utils.normalize import (
    normalize_url, extract_domain, canonical_domain, normalize_phone,
    normalize_email, is_usable_email, is_personal_provider,
)


def test_normalize_url_basic():
    # Host is lowercased; path preserves case; trailing slash stripped.
    assert normalize_url("https://Example.com/Path/") == "https://example.com/Path"
    assert normalize_url("WWW.Example.COM") == "https://example.com"


def test_normalize_url_strips_tracking():
    url = "https://example.com/page?utm_source=x&utm_medium=y&keep=1"
    out = normalize_url(url)
    assert "utm_source" not in out
    assert "utm_medium" not in out
    assert "keep=1" in out


def test_normalize_url_google_wrapper():
    out = normalize_url("https://www.google.com/url?url=https://real.com/x&sa=D")
    assert out == "https://real.com/x"


def test_normalize_url_ipv6():
    out = normalize_url("http://2001:db8::1/page")
    assert out.startswith("http://[2001:db8::1]")


def test_normalize_url_rejects_non_http():
    assert normalize_url("mailto:foo@bar.com") == "N/A"
    assert normalize_url("tel:+1555") == "N/A"
    assert normalize_url("") == "N/A"
    assert normalize_url(None) == "N/A"


def test_extract_domain_cc_tld():
    assert extract_domain("https://www.example.co.uk/a") == "example.co.uk"
    assert extract_domain("https://sub.example.com") == "example.com"


def test_canonical_domain_ip():
    assert canonical_domain("1.1.1.1") == "1.1.1.1"


def test_normalize_phone():
    assert normalize_phone("+1 (555) 000-1234") == "15550001234"
    assert normalize_phone("555-000-1234") == "15550001234"
    assert normalize_phone("N/A") == "N/A"


def test_normalize_email():
    assert normalize_email("Foo@Bar.COM") == "foo@bar.com"
    assert normalize_email("not-an-email") == ""


def test_is_usable_email():
    assert is_usable_email("john@acme.com", website_url="https://acme.com")
    assert not is_usable_email("john@example.com")
    # off-domain + suspicious local part (info) -> rejected
    assert not is_usable_email("info@otherdomain.com", website_url="https://acme.com")


def test_personal_provider():
    assert is_personal_provider("gmail.com")
    assert not is_personal_provider("acme.com")
