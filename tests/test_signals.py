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


# -- Adversarial regressions (B3) -----------------------------------------

def test_extract_decision_maker_rejects_boilerplate():
    # Nav/footer legal phrasing must not fabricate a decision maker.
    assert extract_decision_maker(
        "Terms Of Service. Our Managing Director oversees all.") == ("", "")
    assert extract_decision_maker(
        "Our website uses cookies. Contact us about our privacy policy.") == ("", "")


def test_extract_decision_maker_rejects_testimonial():
    # A testimonial author is NOT the business owner.
    assert extract_decision_maker(
        '"Best plumber!" says Mary Johnson, a happy customer.') == ("", "")


def test_extract_decision_maker_keeps_real():
    assert extract_decision_maker("John Smith, CEO of Acme Plumbing") == ("John Smith", "CEO")
    assert extract_decision_maker("Our team is led by Jane Doe, Managing Director") == (
        "Jane Doe", "Managing Director")


def test_no_decision_maker_from_production_false_positives():
    # F07: the real production false-positive rows must now resolve to ("", "").
    assert extract_decision_maker("Email Wayne William A, Manager for bookings.") == ("", "")
    assert extract_decision_maker("Meet Hugo — Founder of a friendlier plumbing visit.") == ("", "")
    assert extract_decision_maker("Main Sponsor — Founder Tier") == ("", "")
    assert extract_decision_maker("Texans. Co — Founder") == ("", "")
    assert extract_decision_maker("Jack Gilbert Jack Gilbert, president") == ("", "")


def test_vice_president_title_not_split():
    # F07: "Vice President" is one title token, so Villalobos resolves cleanly.
    name, title = extract_decision_maker("BRANDON VILLALOBOS, Vice President of Operations")
    assert name == "BRANDON VILLALOBOS" and title == "Vice President"


def test_real_decision_makers_still_detected():
    assert extract_decision_maker("Alan O'Neill, CEO") == ("Alan O'Neill", "CEO")
    assert extract_decision_maker("John Smith, CEO of Acme Plumbing") == ("John Smith", "CEO")


def test_segment_rejection_not_substring():
    # F18: reject by first path segment, not raw substring.
    assert platform_for_url("https://www.instagram.com/natgeo/travel/") == "instagram"
    assert platform_for_url("https://www.facebook.com/tr") is None
    assert platform_for_url("https://www.facebook.com/SharedOfficeSpace") == "facebook"


def test_keyword_word_boundaries():
    # F19: "licensed" must not match "unlicensed".
    from scraper.signals.detector import detect_signals
    ctx = PageContext(text="We are UNLICENSED contractors.", html="", urls=[], scripts=[])
    assert detect_signals(ctx)["licensed_insured"][0] is False
    ctx2 = PageContext(text="Licensed and insured team.", html="", urls=[], scripts=[])
    assert detect_signals(ctx2)["licensed_insured"][0] is True
