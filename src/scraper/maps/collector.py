"""Google Maps collection seam.

This module defines the public interface the scraper uses to obtain business
listings and their review text. The concrete browser implementation (Playwright
navigating google.com/maps) is intentionally isolated behind `collect()` so the
entire review-analysis add-on can be developed, unit-tested and demoed without a
live session.

Selectors are expressed as layered candidates (primary -> fallback) following the
common convention of resilient DOM scraping.

Pure-function parsing helpers are factored out so they can be unit-tested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from ..models import Business

# Layered candidate selectors (primary -> alternate). Left as documentation of
# the DOM contract a live Playwright collector must satisfy.
SELECTORS: dict[str, tuple[str, ...]] = {
    "name": (".fontHeadlineLarge", "h1.DUwDvf", "h1"),
    "address": ("button[data-item-id='address']", ".ADHc2"),
    "review_text": (
        ".review-text",
        ".jftiEf .wiI7pd",
        ".MyEned .wiI7pd",
    ),
}

_REVIEW_TEXT_RE = re.compile(r"\b(?:review|rated)\b", re.IGNORECASE)


def parse_rating(text: str | None) -> float | None:
    """Extract a rating like '4.5' from a block of text."""
    if not text:
        return None
    m = re.search(r"(\d(?:[.,]\d)?)\s*star", text.lower())
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(r"^\s*(\d(?:[.,]\d)?)", text)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def parse_review_count(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:reviews?)", text.lower())
    if not m:
        return None
    return int(m.group(1).replace(",", "").split(".")[0])


def is_review_text(node_text: str) -> bool:
    """Heuristic to filter review-ish nodes from arbitrary page text."""
    return bool(_REVIEW_TEXT_RE.search(node_text)) or len(node_text) > 60


@dataclass
class Collector:
    """Streaming collector. In demo mode it yields prepared businesses.

    In live mode, a Playwright-backed implementation would:
      1. open google.com/maps with hl/gl/headless settings,
      2. run each query, scroll the feed,
      3. open each place and harvest `name/address/rating/...`,
      4. expand the reviews and harvest N review texts,
      5. yield a partially-populated Business for every record.
    """

    queries: Iterable[str]
    max_results_per_query: int = 0
    max_total_results: int = 0
    demo_provider: Callable[[], Iterable[Business]] | None = None

    def collect(self) -> Iterable[Business]:
        total = 0
        for q in self.queries:
            produced = 0
            for biz in self._one_query(q):
                yield biz
                produced += 1
                total += 1
                if self.max_total_results and total >= self.max_total_results:
                    return
            if self.max_results_per_query:
                # not enforced here (collector-level cap is caller's job in live mode)
                pass

    def _one_query(self, _q: str) -> Iterable[Business]:
        if self.demo_provider is not None:
            yield from self.demo_provider()
        else:
            # Live Playwright path placeholder: subclasses/impl inject the driver.
            yield from ()
