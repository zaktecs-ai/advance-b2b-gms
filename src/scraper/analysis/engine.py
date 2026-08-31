"""Review quality analysis — THE FREE ADD-ON FEATURE.

For every business we capture a handful of its latest Google-Maps reviews, then
derive three cheap, fully-offline signals:

  * sentiment_score : a transparent lexicon score in [-1, 1] of the top review
  * review_keywords: the most frequent meaningful topics across those reviews
  * lead_score     : 0-100 scoring combining rating, review volume, sentiment
                     and keyword strength — a single "how qualified is this lead"
                     number
  * pitch_hook     : a one-sentence, data-grounded opening line for outreach

Everything is computed locally (no paid APIs, no licensing). The LexiconConstant
keeps the sentiment tables in one place; the functions are pure and unit-tested.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from ..models import Business

# stopwords: common English filler, excluded from keyword extraction
_STOPWORDS: set[str] = set(
    "the a an and or but for with without at on in of to is are was were be been "
    "it its it's they their them this that these those we you your our i me my "
    "he she his her not no do does did have has had can could should would will "
    "just really very so too also more most about from by as if then than "
    "service services owner staff place business restaurant group called got "
    "get got getting come came going go went "
    "since again week today yesterday still ever never always already even "
    "well good great there here".split()
)


@dataclass(frozen=True)
class LexiconConstant:
    """Holds the keyword weight tables and thresholds for scoring."""

    topic_terms: tuple[str, tuple[str, ...], float] = (
        ("service", ("service", "professional", "helpful", "responsive"), 1.0),
        ("cleanliness", ("clean", "dirty", "germ", "odor", "spotless"), 1.0),
        ("pricing", ("price", "pricing", "affordable", "cost", "expensive", "value"), 1.0),
        ("speed", ("fast", "quick", "quickly", "rapid", "slow", "waited", "wait"), 0.9),
        ("quality", ("quality", "well-done", "excellent", "outstanding", "quality work"), 1.0),
        ("friendliness", ("friendly", "courteous", "welcoming", "kind"), 0.9),
        ("communication", ("communication", "responsive", "clear", "kept informed"), 0.8),
        ("financing", ("financing", "financing options", "payment plan", "credit"), 1.0),
        ("licensed", ("licensed", "insured", "insurance", "certified"), 1.0),
    )

    rating_high_wt: float = 1.0
    rating_neutral_wt: float = 0.4
    rating_low_wt: float = 0.0
    review_volume_cap: int = 500

    review_volume_cap: int = 500


LEXICON = LexiconConstant()

# flat set of every topic term (used by the keyword scoring component)
_TOPIC_TERMS: set[str] = {w for _, terms, _ in LEXICON.topic_terms for w in terms}


def normalize_text(raw: str) -> str:
    """Collapse whitespace and strip control characters."""
    return re.sub(r"\s+", " ", raw).strip()


def extract_keywords(reviews: list[str], max_keywords: int = 5) -> list[str]:
    """Most frequent meaningful topics across reviews (frequency, then alpha)."""
    counter: Counter[str] = Counter()
    for review in reviews:
        low = normalize_text(review).lower()
        for word in re.findall(r"[a-z]{3,}", low):
            if word not in _STOPWORDS and not word.isdigit():
                counter[word] += 1
    top = [w for w, _ in counter.most_common(max_keywords)]
    return sorted(top)


def score_reviews(reviews: list[str]) -> float:
    """Average lexicon sentiment [-1,1] across the (already-cleaned) reviews."""
    if not reviews:
        return 0.0
    scratch = Business()
    total = sum(scratch.classify_sentiment(r) for r in reviews)
    return total / len(reviews)


def compute_lead_score(business: Business, reviews: list[str]) -> float:
    """0-100 composite. Pure function; used directly in tests."""
    if not reviews:
        # no review signal: base score on listing only
        rating = business.rating or 0.0
        count = business.review_count or 0
        base = min(count, LEXICON.review_volume_cap) / LEXICON.review_volume_cap  # 0-1
        rating_piece = rating / 5.0  # 0-1
        return round(100 * (0.5 * base + 0.5 * rating_piece), 1)

    # rating component (up to 50)
    rating = business.rating or 0.0
    if rating >= 4.5:
        rating_pts = 50 * LEXICON.rating_high_wt
    elif rating >= 3.5:
        rating_pts = 50 * LEXICON.rating_neutral_wt
    else:
        rating_pts = 50 * LEXICON.rating_low_wt

    # volume component (up to 25)
    count = business.review_count or 0
    vol = min(count, LEXICON.review_volume_cap) / LEXICON.review_volume_cap  # 0-1
    vol_pts = 25 * vol

    # sentiment component (up to 20)
    sent = score_reviews(reviews)  # [-1,1]
    sent_pts = 20 * max(0.0, sent)  # clamp negatives to 0 for the "qualified" number

    # keyword component (up to 5) — presence of a high-signal topic
    kws = extract_keywords(reviews)
    matched = [k for k in kws if k in _TOPIC_TERMS]
    kw_pts = 5 * min(1.0, len(matched) / 2.0)

    return round(rating_pts + vol_pts + sent_pts + kw_pts, 1)


def analyze(business: Business, reviews: list[str], reviews_per_business: int = 5) -> Business:
    """Attach the free add-on fields to a business from its captured reviews."""
    cleaned = [normalize_text(r) for r in reviews]
    cleaned = [r for r in cleaned if r][:reviews_per_business]
    business.reviews = cleaned
    business.sentiment_score = score_reviews(cleaned)
    business.review_keywords = extract_keywords(cleaned)
    business.lead_score = compute_lead_score(business, cleaned)
    business.evidence["top_review"] = business.top_review
    business.evidence["reviews_scored"] = len(cleaned)
    return business
