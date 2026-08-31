"""Data model for the standalone B2B Google-Maps lead scraper.

Clean-room implementation (not derived from any other project's source): only the
domain vocabulary shared across Google-Maps lead scraping is used. This package is
self-contained and carries its own data contract.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

# Column order defines the CSV/XLSX output order.
OUTPUT_COLUMNS: list[str] = [
    # identity / identifiers
    "business_name",
    "category",
    "place_id",
    "kgmid",
    "google_maps_url",
    # contact
    "phone",
    "website",
    "address",
    "city",
    "state",
    "country",
    "latitude",
    "longitude",
    "plus_code",
    # maps intelligence
    "rating",
    "review_count",
    "business_status",
    "business_hours",
    # website + tech
    "website_status",
    "emails",
    "tech_stack",
    # ---- the free add-on feature block ----
    "top_review",
    "review_keywords",
    "sentiment_score",
    "lead_score",
    "pitch_hook",
]

MISSING = "N/A"

# Leading-word stems that signal one business type or another (keyword features).
_NEGATIVE_LEAD_WORDS: tuple[str, ...] = (
    "worst", "terrible", "terribly", "awful", "horrible", "rude", "scam",
    "disappoint", "not happy", "unhappy", "avoid", "regret", "mistake",
)


@dataclass
class Business:
    """A single business listing discovered on Google Maps."""

    business_name: str = MISSING
    category: str = MISSING
    place_id: str = MISSING
    kgmid: str = MISSING
    google_maps_url: str = MISSING
    phone: str = MISSING
    website: str = MISSING
    address: str = MISSING
    city: str = MISSING
    state: str = MISSING
    country: str = MISSING
    latitude: float | None = None
    longitude: float | None = None
    plus_code: str = MISSING
    rating: float | None = None
    review_count: int | None = None
    business_status: str = MISSING
    business_hours: str = MISSING
    # website enrichment
    website_status: str = MISSING
    emails: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    # the free add-on feature fields
    reviews: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0
    lead_score: float = 0.0
    review_keywords: list[str] = field(default_factory=list)

    # internal
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    evidence: dict[str, Any] = field(default_factory=dict)

    # ---- derived accessors ----
    @property
    def top_review(self) -> str:
        """The most representative review (highest sentiment, not a complaint)."""
        if not self.reviews:
            return MISSING
        return self.reviews[0]

    @property
    def pitch_hook(self) -> str:
        """A one-sentence, data-grounded hook an outreach agent can open with."""
        parts: list[str] = []
        if self.review_count and self.rating is not None:
            parts.append(f"{self.rating:.1f} stars across {self.review_count} reviews")
        if self.review_keywords:
            parts.append("customers mention " + ", ".join(self.review_keywords[:3]))
        if self.category and self.category != MISSING:
            parts.append("in " + self.category.lower())
        if not parts:
            return MISSING
        return "; ".join(parts) + "."

    def to_row(self) -> dict[str, Any]:
        """Serialize to the flat output row (order follows OUTPUT_COLUMNS)."""
        row: dict[str, Any] = {
            "business_name": self.business_name,
            "category": self.category,
            "place_id": self.place_id,
            "kgmid": self.kgmid,
            "google_maps_url": self.google_maps_url,
            "phone": self.phone,
            "website": self.website,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "latitude": _f(self.latitude),
            "longitude": _f(self.longitude),
            "plus_code": self.plus_code,
            "rating": _f(self.rating),
            "review_count": _f(self.review_count),
            "business_status": self.business_status,
            "business_hours": self.business_hours,
            "website_status": self.website_status,
            "emails": "; ".join(self.emails),
            "tech_stack": ", ".join(self.tech_stack),
            "top_review": self.top_review,
            "review_keywords": ", ".join(self.review_keywords),
            "sentiment_score": f"{self.sentiment_score:.2f}",
            "lead_score": f"{self.lead_score:.2f}",
            "pitch_hook": self.pitch_hook,
        }
        return row

    def classify_sentiment(self, text: str) -> float:
        """Simple, explainable lexicon sentiment in [-1, 1] (0 = neutral).

        Counts positive vs negative lead words; negatives weigh more because an
        angry review is more distinctive than a generic "great".
        """
        low = " " + text.lower() + " "
        neg = sum(1 for w in _NEGATIVE_LEAD_WORDS if (" " + w + " ") in low or w in low)
        pos = sum(1 for w in _POSITIVE_LEAD_WORDS if (" " + w + " ") in low)
        if pos == 0 and neg == 0:
            return 0.0
        raw = (pos - 2.0 * neg) / (pos + neg + 1e-9)
        return max(-1.0, min(1.0, raw))


# Positive stems (defined here so classify_sentiment stays self-contained).
_POSITIVE_LEAD_WORDS: tuple[str, str, ...] = (
    "great", "excellent", "amazing", "awesome", "fantastic", "wonderful",
    "love", "loved", "best", "recommend", "highly recommend", "helpful",
    "friendly", "professional", "clean", "fast", "quick", "affordable",
    "knowledgeable", "responsive", "top-notch", "pleased", "happy",
)


def _f(value: Any) -> str:
    if value is None:
        return MISSING
    return str(value)
