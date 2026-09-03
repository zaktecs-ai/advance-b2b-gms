"""Review analysis: sentiment, keywords, lead score, pitch hook."""
from scraper.analysis.engine import (
    sentiment_score, review_keywords, lead_score, pitch_hook, analyze, top_review,
)


def test_sentiment_positive():
    s = sentiment_score(["great service, very friendly and professional"])
    assert s > 0


def test_sentiment_negative():
    s = sentiment_score(["terrible, rude and unprofessional"])
    assert s < 0


def test_sentiment_neutral_empty():
    assert sentiment_score([]) == 0.0


def test_sentiment_range():
    s = sentiment_score(["amazing", "horrible", "awesome", "terrible"])
    assert -1.0 <= s <= 1.0


def test_keywords_stopwords_filtered():
    kws = review_keywords(["great plumbing repair service", "plumbing fixed my sink"])
    assert "plumbing" in kws
    assert "the" not in kws


def test_lead_score_bounds():
    score = lead_score(4.8, 200, 0.8, 5)
    assert 0 <= score <= 100
    assert lead_score(0, 0, -1.0, 0) == 0
    assert lead_score(5.0, 500, 1.0, 10) <= 100


def test_pitch_hook_grounded():
    hook = pitch_hook("Acme Plumbing", "Plumber", 4.9, 120, ["plumbing"], 0.8)
    assert "Acme Plumbing" in hook
    assert "120" in hook


def test_analyze_full():
    out = analyze(["great work!", "very professional"], rating=4.7, review_count=50,
                  business_name="Acme", category="Plumber")
    assert -1 <= out["sentiment_score"] <= 1
    assert 0 <= out["lead_score"] <= 100
    assert isinstance(out["pitch_hook"], str) and out["pitch_hook"]


def test_top_review():
    assert top_review([]) == ""
    assert top_review(["great", "excellent service"]) == "excellent service"


# --- G04: verb/tokenization junk must never become keywords or hooks --------

def test_keywords_exclude_verb_junk_g2():
    kws = review_keywords([
        "They came out and fixed my sink. Gave me options.",
        "Couldn't ask for better. Installed quickly.",
    ])
    for banned in ("came", "gave", "fixed", "installed", "couldn", "ask"):
        assert banned not in kws


def test_pitch_hook_never_says_praising_junk():
    hook = pitch_hook("Acme", "Plumber", 4.8, 100,
                      ["came", "gave", "recently"], 0.8)
    assert "praising came" not in hook
    assert "praising gave" not in hook
    assert "praising recently" not in hook


def test_keywords_keep_brand_and_topic_tokens():
    # Business-name tokens and topic words ("plumbing") stay legitimate; only
    # role words ("plumber") and verbs are junk.
    kws = review_keywords(["Halo plumbing was awesome, great price."])
    assert "halo" in kws and "plumbing" in kws
    assert "awesome" not in kws  # a POSITIVE word, excluded by design
