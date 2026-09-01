"""Review RPC parsing + DOM fallback + filtering."""
from scraper.maps.reviews import (
    build_review_rpc_url, parse_review_rpc_response, parse_review_texts_dom,
    filter_reviews,
)


def test_build_rpc_url():
    url = build_review_rpc_url("0x123:0x456")
    assert "listugcposts" in url
    assert "0x123:0x456" in url


def test_parse_rpc_response():
    # Simulated listugcposts shape: [meta, [review entries...], token]
    payload = [None, [
        [None, ["Great service, very clean!"], None],
        [None, ["Was a bit slow but fine."], None],
    ], "NEXT_TOKEN"]
    reviews, token = parse_review_rpc_response(")]}'" + __import__("json").dumps(payload))
    assert len(reviews) == 2
    assert "Great service" in reviews[0]
    assert token == "NEXT_TOKEN"


def test_parse_rpc_garbage():
    reviews, token = parse_review_rpc_response("not json at all")
    assert reviews == [] and token == ""


def test_dom_fallback_extracts_sentences():
    html = "<div class='review'>Excellent pizza and friendly staff.</div>"
    out = parse_review_texts_dom(html)
    assert any("pizza" in s for s in out)


def test_filter_reviews_length():
    out = filter_reviews(["ok", "a longer review body here"], min_len=5)
    assert "ok" not in out
    assert "a longer review body here" in out


def test_filter_reviews_dedup():
    out = filter_reviews(["same review", "same review"])
    assert len(out) == 1


def test_clean_review_text_strips_ui_chrome():
    from scraper.maps.reviews import clean_review_text
    raw = ("SpiceGirl 4 reviews · 1 photo 4 months ago Adam was very "
           "professional and courteous.  Like  Share")
    out = clean_review_text(raw)
    assert "Adam was very professional" in out
    assert "4 reviews" not in out and "ago" not in out
    assert "Like" not in out and "Share" not in out


def test_keywords_never_contain_ui_noise():
    from scraper.analysis.engine import review_keywords
    kws = review_keywords(["Great job 3 months ago Like Share Response from the owner",
                           "Fast service, fair price"])
    for banned in ("ago", "months", "like", "share", "response", "owner"):
        assert banned not in kws
