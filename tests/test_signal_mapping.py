"""Regression tests for the signal-detector tech mapping + reviews flow.

Locks the fixes that keep meta_pixel/ga4/gtm/advertising/booking/chat from
coming out blank, and verifies the SignalDetector's YES/NO output shape.
"""
from scraper.signals.detector import TECH_FIELDS, PageContext, SignalDetector
from scraper.websites.tech_detect import TechDetector


def _ctx(html="", text="", urls=None, scripts=None):
    return PageContext(html=html, text=text, urls=urls or [], scripts=scripts or [])


def test_meta_pixel_detected():
    ctx = _ctx(html="<script>fbq('init','123')</script>")
    out, _ = SignalDetector().run(ctx)
    assert out["meta_pixel"] == "YES"


def test_ga4_detected():
    html = "<script src='https://www.googletagmanager.com/gtag/js?id=G-ABC123'></script>"
    out, _ = SignalDetector().run(_ctx(html=html))
    assert out["ga4"] == "YES"


def test_gtm_detected():
    html = "<script src='https://www.googletagmanager.com/gtm.js?id=GTM-XXXX'></script>"
    out, _ = SignalDetector().run(_ctx(html=html))
    assert out["gtm"] == "YES"


def test_advertising_detected_via_pixel():
    ctx = _ctx(html="<script src='https://connect.facebook.net/en_US/fbevents.js'></script>")
    out, _ = SignalDetector().run(ctx)
    assert out["advertising"] == "YES"


def test_booking_and_chat_detected():
    html = ("<a href='https://calendly.com/acme'>book</a>"
            "<script src='https://embed.tawk.to/abc'></script>")
    ctx = _ctx(html=html, urls=["https://calendly.com/acme"])
    out, _ = SignalDetector().run(ctx)
    assert out["booking_system"] == "YES"
    assert out["chat_widget"] == "YES"


def test_all_bool_fields_present():
    # Every boolean tech field must always be present with YES or NO (never
    # absent), so the export contract never shows a blank cell for these.
    out, _ = SignalDetector().run(_ctx(html=""))
    for f in TECH_FIELDS:
        assert f in out, f"missing field {f}"
        assert out[f] in ("YES", "NO")


def test_tech_classify_does_not_fabricate_missing_fields():
    assert TechDetector.classify(set()) == {}
    assert TechDetector.classify({"Google Analytics"}) == {"analytics": "Google Analytics"}
    assert "ga4" not in TechDetector.classify({"Google Analytics"})
    assert TechDetector.classify({"Google Analytics 4"})["ga4"] == "detected"


def test_blank_page_all_no():
    out, _ = SignalDetector().run(_ctx(html="<html><body>nothing</body></html>"))
    for f in ("meta_pixel", "ga4", "gtm", "advertising", "booking_system", "chat_widget"):
        assert out[f] == "NO"


def test_prose_mentions_do_not_flip_tech_signals():
    # B4: a brand/keyword mention in body copy is not an installed integration.
    out, _ = SignalDetector().run(_ctx(
        text="Our blog explains how gtag() works and how to set up analytics."))
    assert out["ga4"] == "NO"
    out2, _ = SignalDetector().run(_ctx(
        text="Unlike fresha.com, we do everything in-house."))
    assert out2["booking_system"] == "NO"
    out3, _ = SignalDetector().run(_ctx(
        html="<p>An intercom system was installed in our office.</p>"))
    assert out3["chat_widget"] == "NO"
    out4, _ = SignalDetector().run(_ctx(
        html="<p>We use a tag manager to organize our marketing scripts.</p>"))
    assert out4["advertising"] == "NO"


def test_real_ga4_and_booking_still_detected():
    out, _ = SignalDetector().run(_ctx(
        html="<script src='https://www.googletagmanager.com/gtag/js?id=G-ABCDEFGH'></script>"))
    assert out["ga4"] == "YES"
    out2, _ = SignalDetector().run(
        _ctx(html="<a href='https://calendly.com/acme/30min'>book</a>",
             urls=["https://calendly.com/acme/30min"]))
    assert out2["booking_system"] == "YES"


def test_review_panel_sentiment_roundtrip():
    # Reviews from the collector must flow into analysis: a positive review
    # produces a positive sentiment and populated keywords.
    from scraper.analysis.engine import analyze
    a = analyze(["Great service, very professional and friendly!"],
                rating=4.8, review_count=50, business_name="Acme", category="Plumber")
    assert a["sentiment_score"] > 0
    assert a["review_keywords"]
    assert a["top_review"]


def test_fallback_detect_ignores_body_prose():
    # F20: tech must be detected from markup/headers, never body prose.
    from scraper.websites.tech_detect import _fallback_detect
    html = "<html><body>We migrated from Django and love Cloudflare.</body></html>"
    assert _fallback_detect(html, {}) == []


def test_fallback_detect_finds_markup_artifact():
    from scraper.websites.tech_detect import _fallback_detect
    html = "<script src='https://cdn.shopify.com/a.js'></script>"
    assert "Shopify" in _fallback_detect(html, {})
