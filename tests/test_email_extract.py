"""Email extraction + cleaning + obfuscation decoding."""
from scraper.email.extract import extract_emails, extract_emails_from_text, clean_emails


def test_extract_mailto():
    html = '<a href="mailto:hello@acme.com">email</a>'
    assert "hello@acme.com" in extract_emails(html)


def test_extract_json_ld():
    html = '<script type="application/ld+json">{"email": "contact@acme.com"}</script>'
    assert "contact@acme.com" in extract_emails(html)


def test_extract_inline_script_ignored():
    # B2: inline <script> bodies (GA4/GTM config, Sentry DSN, obfuscated vars)
    # are NOT a valid contact-email source — a tracking config must not yield
    # a business email.
    html = "<script>var GA='G-ABCDEF'; var e = 'tracking@googletagmanager.com';</script>"
    assert "tracking@googletagmanager.com" not in extract_emails(html)
    assert extract_emails(html) == []


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


# -- Adversarial regressions (B2 / B6 / E3) --------------------------------

def test_extract_ignores_html_comments():
    # B6: a commented-out, stale mailto link must not be resurrected.
    html = '<!-- <a href="mailto:ghost@acme.com"> --><p>x@acme.com</p>'
    emails = extract_emails(html)
    assert "ghost@acme.com" not in emails
    assert "x@acme.com" in emails


def test_extract_ignores_testimonial_emails():
    # B2: a customer testimonial's personal Gmail must not be harvested as the
    # business's contact email.
    html = ('<p>support@acme.com</p>'
            '<blockquote class="testimonial">Great work! — happy.customer@gmail.com</blockquote>')
    emails = extract_emails(html)
    assert "support@acme.com" in emails
    assert "happy.customer@gmail.com" not in emails


def test_clean_rejects_vendor_domains():
    # B2/E3: analytics/ad/error-tracking vendor addresses are never business emails.
    out = clean_emails([
        "tracking@googletagmanager.com",
        "lead@doubleclick.net",
        "//pubkey@o123.ingest.sentry.io",
        "support@acme.com",
    ], website_url="https://acme.com")
    assert out == ["support@acme.com"]


def test_clean_rejects_asset_filenames():
    # E3: file-extension "TLDs" are not emails.
    out = clean_emails(["logo@2x.png", "react@17.js", "support@acme.com"],
                       website_url="https://acme.com")
    assert "logo@2x.png" not in out
    assert "react@17.js" not in out
    assert "support@acme.com" in out


def test_clean_rejects_off_domain_by_default():
    # B2: an off-domain non-personal address is rejected by default, not only
    # when a suspicious word is present in the local part.
    out = clean_emails(["someone@backlinkfarm.com"], website_url="https://acme.com")
    assert out == []
    # A personal provider's off-domain address is still allowed (a sole
    # proprietor legitimately lists a Gmail).
    out2 = clean_emails(["owner@gmail.com"], website_url="https://acme.com")
    assert out2 == ["owner@gmail.com"]


def test_prose_at_dot_not_decoded():
    # F17: prose "at/dot" must NOT be decoded into a fake email.
    from scraper.email.extract import extract_emails_from_text
    assert extract_emails_from_text("Order now at shop dot com and save") == []
