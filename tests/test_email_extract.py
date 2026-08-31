"""Email extraction + cleaning + obfuscation decoding."""
from scraper.email.extract import extract_emails, extract_emails_from_text, clean_emails


def test_extract_mailto():
    html = '<a href="mailto:hello@acme.com">email</a>'
    assert "hello@acme.com" in extract_emails(html)


def test_extract_json_ld():
    html = '<script type="application/ld+json">{"email": "contact@acme.com"}</script>'
    assert "contact@acme.com" in extract_emails(html)


def test_extract_inline_script():
    html = "<script>var e = 'support@acme.com';</script>"
    assert "support@acme.com" in extract_emails(html)


def test_extract_visible_text():
    html = "<p>Reach us at info@acme.com today</p>"
    assert "info@acme.com" in extract_emails(html)


def test_obfuscated_at_dot():
    assert "a@b.com" in extract_emails_from_text("contact me at a [at] b [dot] com")
    assert "a@b.com" in extract_emails_from_text("a&#64;b&#46;com")


def test_clean_rejects_disposable():
    out = clean_emails(["john@example.com", "jane@acme.com"])
    assert "jane@acme.com" in out
    assert "john@example.com" not in out


def test_clean_dedup_preserves_order():
    out = clean_emails(["a@acme.com", "a@acme.com", "b@acme.com"])
    assert out == ["a@acme.com", "b@acme.com"]


def test_extract_deduplicates():
    html = '<a href="mailto:x@acme.com">x@acme.com</a>'
    assert extract_emails(html).count("x@acme.com") == 1
