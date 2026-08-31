"""Tests for the free review-quality add-on (pure logic)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scraper.analysis.engine import (  # noqa: E402
    extract_keywords,
    normalize_text,
    compute_lead_score,
    score_reviews,
    analyze,
)
from scraper.models import Business  # noqa: E402


def _biz(rating=4.8, count=200):
    return Business(business_name="X", category="Pool & Spa Service",
                    rating=rating, review_count=count)


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  great   service!!\n\n") == "great service!!"


def test_extract_keywords_excludes_stopwords_and_digits():
    kws = extract_keywords(["Great fast service and affordable pricing. 2024."], max_keywords=10)
    assert "and" not in kws
    assert not any(w.isdigit() for w in kws)
    # meaningful topic words should appear
    joined = " ".join(kws)
    assert "service" in joined or "pricing" in joined


def test_score_reviews_positive():
    s = score_reviews(["Excellent, professional, friendly staff.", "Great value."])
    assert s > 0


def test_score_reviews_negative():
    s = score_reviews(["Terrible service. Awful, rude staff. Disappointed."])
    assert s < 0


def test_compute_lead_score_ranks_reviewed_above_unreviewed():
    good = _biz(rating=4.9, count=500)
    good.reviews = ["Excellent professional service, great price, very friendly."]
    good.reviews += ["fast and clean", "highly recommend", "responsive communication"]
    s_good = compute_lead_score(good, good.reviews)

    low = _biz(rating=2.0, count=3)
    low.reviews = ["Terrible.", "Rude staff.", "Scam."]
    s_low = compute_lead_score(low, low.reviews)
    assert s_good > 30
    assert s_low < 30
    assert s_good > s_low


def test_compute_lead_score_no_reviews_uses_listing():
    biz = _biz(rating=4.9, count=500)
    assert 0 <= compute_lead_score(biz, []) <= 100


def test_analyze_populates_addon_fields():
    biz = _biz()
    out = analyze(biz, ["Great service and clean premise.", "Fast and friendly staff."])
    assert out.top_review
    assert out.review_keywords
    assert out.lead_score > 0
    assert out.pitch_hook
