"""Signal detection: social classification + business signals + decision maker."""
from scraper.signals.detector import PageContext, detect_signals, extract_decision_maker
from scraper.signals.social import detect_social, platform_for_url


def test_platform_facebook():
    assert platform_for_url("https://www.facebook.com/acme") == "facebook"


def test_platform_no_cross_contamination():
    # A Facebook URL must never land in instagram.
    d = detect_social(["https://facebook.com/a"])
    assert d["facebook"] != "N/A"
    assert d["instagram"] == "N/A"


def test_platform_map():
    urls = [
        "https://facebook.com/a", "https://instagram.com/a",
        "https://linkedin.com/company/a", "https://youtube.com/@a",
        "https://x.com/a", "https://tiktok.com/@a", "https://pinterest.com/a",
        "https://github.com/a", "https://snapchat.com/add/a",
    ]
    d = detect_social(urls)
    for k in d:
        assert d[k] != "N/A", k


def test_platform_unknown():
    assert platform_for_url("https://example.com") is None
    assert platform_for_url("") is None


def test_detect_signals_pricing():
    ctx = PageContext(text="Our pricing starts at $99", html="", urls=[], scripts=[])
    r = detect_signals(ctx)
    assert r["pricing"][0] is True


def test_detect_signals_custom():
    ctx = PageContext(text="we offer free consultations")
    custom = {"free_consult": {"keywords": ["free consultations"]}}
    r = detect_signals(ctx, custom)
    assert r["free_consult"][0] is True


def test_extract_decision_maker():
    name, title = extract_decision_maker("John Smith, CEO of Acme Plumbing")
    assert name == "John Smith"
    assert title == "CEO"


def test_extract_decision_maker_variants_and_unicode():
    assert extract_decision_maker("Founder: Maria García") == ("Maria García", "Founder")
    assert extract_decision_maker("CEO John Smith") == ("John Smith", "CEO")


def test_extract_decision_maker_none():
    assert extract_decision_maker("") == ("", "")
    assert extract_decision_maker("no title here") == ("", "")
