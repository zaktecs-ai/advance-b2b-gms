"""Review-quality lead scoring (the free, offline add-on).

For each business derive:
  * ``sentiment_score`` (-1..1) via a transparent lexicon
  * ``review_keywords`` (top topics, stopword-filtered)
  * ``lead_score`` (0-100 composite of rating + volume + sentiment + topic)
  * ``pitch_hook`` (auto-generated, data-grounded opening line)
  * ``top_review`` (best representative review text)

All logic is pure and offline — no paid APIs.
"""
from __future__ import annotations

from collections import Counter

from ..utils.text import tokenize, _STOPWORDS

_POSITIVE = {
    "great", "excellent", "awesome", "amazing", "fantastic", "friendly",
    "professional", "helpful", "best", "love", "loved", "quick", "fast",
    "reliable", "recommend", "recommended", "outstanding", "wonderful",
    "perfect", "clean", "honest", "knowledgeable", "responsive", "polite",
    "courteous", "efficient", "highly", "quality", "superb", "top", "exceed",
    "impressed", "satisfied", "happy", "delighted", "thank", "trust",
    "caring", "attentive", "timely", "worth", "seamless", "smooth",
}

_NEGATIVE = {
    "terrible", "awful", "horrible", "bad", "poor", "rude", "unprofessional",
    "worst", "slow", "late", "unreliable", "disappointed", "disappointing",
    "waste", "overpriced", "expensive", "scam", "avoid", "dirty", "broken",
    "unresponsive", "never", "refund", "complain", "complaint", "ruined",
    "failed", "failure", "frustrating", "messy", "rip-off", "rip", "liar",
}

_PLATFORMS = ["facebook", "instagram", "twitter", "linkedin", "youtube",
              "tiktok", "pinterest", "github", "snapchat"]

# UI-chrome tokens that must never surface as review "topics". Previously
# "months", "ago", "reviews" flowed into pitch hooks (F08).
_UI_NOISE = {"ago", "month", "months", "week", "weeks", "year", "years",
             "day", "days", "hour", "hours", "review", "reviews", "photo",
             "photos", "like", "likes", "share", "shared", "edited",
             "response", "owner", "local", "guide", "helpful", "read",
             "more", "updated"}

# G04: common review VERBS and tokenization artifacts that survive stopword
# removal but say nothing about the business ("couldn" from "couldn't",
# "shutt" from "shutting"). In service-business reviews the most frequent
# surviving tokens are verbs, which produced nonsense pitch hooks like
# "customers consistently praising installed/gave/came".
# DESIGN CONSTRAINT: business-name tokens ("cooper", "halo", "yb") and topic
# words ("plumbing" — unlike the role word "plumber") stay legitimate.
_JUNK_TOKENS = {
    # verbs / function words
    "got", "get", "came", "come", "went", "took", "take", "made", "make",
    "called", "call", "needed", "need", "put", "did", "done", "said",
    "told", "gave", "give", "know", "knew", "think", "thought", "want",
    "wanted", "used", "use", "using", "let", "kept", "keep", "see",
    "seen", "explained", "fixed", "replaced", "installed", "scheduled",
    "arrived", "showed", "hired", "recommended", "recommend", "worked",
    "working", "asked", "ask", "answered", "left", "paid", "charged",
    "sent", "tried", "found", "believe", "felt", "feel", "noticed",
    "checked", "helped", "help",
    # intensifiers / time words not already in _UI_NOISE
    "definitely", "really", "always", "never", "every", "time",
    "back", "first", "second", "way", "recently",
    # generic nouns — NOTE (R11 narrowing): "service"/"services" stay
    # legitimate keywords because a positive review's only topic-bearing
    # token is often exactly that ("Great service, very professional…");
    # blocking them left review_keywords empty on real V1 surfaces.
    "job", "work", "place", "company", "business",
    "guy", "guys", "man", "men", "people", "team", "crew", "tech",
    "technician", "plumber", "plumbers", "home", "house", "money",
    "price", "prices", "pricing", "experience", "review", "reviews",
    # contraction stems produced by tokenize() ("couldn't", "didn't", …)
    "couldn", "wouldn", "shouldn", "didn", "wasn", "isn", "aren",
    "weren", "hasn", "haven", "won", "don", "ain", "shutt", "shut",
    "lot", "bit", "etc", "im", "id", "ive", "theyd", "theyre",
}


def extend_lexicon(path) -> None:
    """Load a custom sentiment lexicon and merge it into the module sets.

    ``analysis.lexicon_hint`` in config.yaml points at a JSON or YAML file:
        {"positive": ["punctual", "warranty"], "negative": ["hidden fees"→"hidden"]}
    NOTE: sentiment scoring matches single TOKENS (reviews are tokenized), so
    only single-word entries are effective; multi-word phrases are accepted
    into the sets but will not match tokenized reviews.
    Missing file raises FileNotFoundError — fail fast, config promised it.
    """
    import json
    from pathlib import Path

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    _POSITIVE.update(str(w).lower() for w in (data.get("positive") or []))
    _NEGATIVE.update(str(w).lower() for w in (data.get("negative") or []))


def sentiment_score(reviews: list[str]) -> float:
    """Return a -1..1 mean sentiment using a transparent lexicon."""
    if not reviews:
        return 0.0
    total = 0.0
    count = 0
    for r in reviews:
        words = tokenize(r)
        score = 0.0
        n = 0
        for w in words:
            if w in _POSITIVE:
                score += 1.0
                n += 1
            elif w in _NEGATIVE:
                score -= 1.0
                n += 1
        if n:
            total += score / n
            count += 1
    if not count:
        return 0.0
    return round(total / count, 3)


def review_keywords(reviews: list[str], top_n: int = 5) -> list[str]:
    """Top frequency keywords across reviews, stopword-filtered."""
    counter: Counter = Counter()
    for r in reviews:
        for w in tokenize(r):
            if w in _UI_NOISE or w in _JUNK_TOKENS:
                continue
            if w not in _STOPWORDS and w not in _POSITIVE and w not in _NEGATIVE:
                counter[w] += 1
    return [w for w, _ in counter.most_common(top_n)]


def top_review(reviews: list[str]) -> str:
    """Pick the best representative review (longest informative one)."""
    if not reviews:
        return ""
    # Prefer a positively-charged review, else the longest.
    scored = sorted(reviews, key=lambda r: (sentiment_score([r]), len(r)), reverse=True)
    return scored[0]


def lead_score(rating, review_count, sentiment: float, keyword_count: int) -> int:
    """0-100 composite lead score."""
    rating_component = 0.0
    try:
        r = float(rating)
        if r > 0:
            rating_component = min(40.0, r * 8.0)  # 5.0 -> 40
    except (TypeError, ValueError):
        rating_component = 0.0

    try:
        rc = int(review_count)
    except (TypeError, ValueError):
        rc = 0
    volume_component = min(20.0, rc / 5.0) if rc > 0 else 0.0  # 100+ -> 20

    sentiment_component = max(0.0, (sentiment + 1.0) / 2.0) * 25.0  # -1..1 -> 0..25

    topic_component = min(15.0, keyword_count * 3.0)  # 5 keywords -> 15

    return int(round(rating_component + volume_component + sentiment_component + topic_component))


def pitch_hook(business_name: str, category: str, rating, review_count,
               keywords: list[str], sentiment: float) -> str:
    """Generate a data-grounded opening line for outreach."""
    name = (business_name or "your business").strip()
    cat = (category or "your services").strip().lower()
    # G04 belt-and-braces: a junk token that survived review_keywords must
    # never be pitched as something customers praised.
    kw = keywords[0] if keywords and keywords[0] not in _JUNK_TOKENS else cat
    try:
        rc = int(review_count)
    except (TypeError, ValueError):
        rc = 0
    part = f"I noticed {name} has {rc} reviews"
    if rating not in (None, "N/A", ""):
        part += f" and a {rating} rating"
    if sentiment > 0.3:
        part += " with customers consistently praising " + kw
    elif sentiment < -0.3:
        part += " but some recent reviews mention " + kw + " — a quick fix could lift that"
    else:
        part += " — and I'd love to help you turn that into more qualified leads"
    return part + "."


def analyze(reviews: list[str], rating=None, review_count=None,
            business_name: str = "", category: str = "") -> dict:
    """Run the full analysis and return the derived fields."""
    sent = sentiment_score(reviews)
    kws = review_keywords(reviews)
    top = top_review(reviews)
    score = lead_score(rating, review_count, sent, len(kws))
    hook = pitch_hook(business_name, category, rating, review_count, kws, sent)
    return {
        "sentiment_score": sent,
        "review_keywords": ",".join(kws),
        "lead_score": score,
        "pitch_hook": hook,
        "top_review": top,
    }
