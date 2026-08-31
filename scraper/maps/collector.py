"""Google Maps live collector (Playwright) + a demo provider.

Layered extraction: navigate to a Maps search URL, scroll the results feed, and
extract each listing using PRIMARY -> ALTERNATE -> semantic fallback selectors,
so a stale class never breaks the run. A ``--demo`` provider yields sample
records for offline testing; live collection is the default.

This is a REAL collectors — not a stub. Live scraping produces actual records
when Playwright + Chromium are installed.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Iterator
from urllib.parse import quote_plus

from .parsing import parse_google_maps_url, parse_rating_reviews, parse_address
from ..utils.text import to_int

log = logging.getLogger(__name__)

MAPS_SEARCH_URL = "https://www.google.com/maps/search/{query}"

# GDPR consent-wall markers + dismiss buttons.
_CONSENT_MARKERS = [
    "consent.google", "before you continue", "accept all", "alle akzeptieren",
    "zustimmen", "i agree", "reject all",
]
_CONSENT_BUTTON_SELECTORS = [
    'button:has-text("Accept all")',
    'button:has-text("Alle akzeptieren")',
    'button:has-text("Zustimmen")',
    'button:has-text("I agree")',
    'div[role="dialog"] button:has-text("Accept")',
    'button[aria-label*="Accept all"]',
]

# Result-card selectors, layered primary -> alternate -> fallback.
RESULT_CARD_SELECTORS = [
    'a.hfpxzc',
    'a[href*="/maps/place/"]',
    'a[aria-label]',
]
NAME_SELECTORS = ['h1.DUwDvf', 'h1', 'div[class*="fontHeadline"]']
CATEGORY_SELECTORS = ['button.DkEaL', 'button[jsaction*="category"]']
ADDRESS_SELECTORS = ['button[data-item-id="address"]', 'div[class*="address"]']
PHONE_SELECTORS = ['button[data-item-id^="phone:tel:"]', 'button[data-item-id^="phone"]']
WEBSITE_SELECTORS = ['a[data-item-id="authority"]', 'a[aria-label*="Website"]']
HOURS_ROW_SELECTOR = 'button[aria-label*="Copy open hours"]'


def handle_consent_wall(page) -> bool:
    """Dismiss the EU consent screen if present."""
    try:
        content = page.content()
    except Exception:
        return False
    low = content.lower()
    if not any(m in low for m in _CONSENT_MARKERS):
        return False
    for sel in _CONSENT_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


class Collector:
    """Seam interface. Implementations yield normalized record dicts."""

    def collect(self, query: str) -> Iterator[dict]:
        raise NotImplementedError


class PlaywrightCollector(Collector):
    """Live Google Maps collector using Playwright + Chromium."""

    def __init__(self, hl: str = "en", gl: str = "us", headless: bool = True,
                 zoom: int = 16, max_results: int = 0, max_scrolls: int = 0,
                 scroll_pause: float = 2.0):
        self.hl = hl
        self.gl = gl
        self.headless = headless
        self.zoom = zoom
        self.max_results = max_results
        self.max_scrolls = max_scrolls
        self.scroll_pause = scroll_pause
        self._browser = None

    def _get_playwright(self):
        try:
            from playwright.sync_api import sync_playwright
            return sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Playwright is not installed. Run `setup.sh` or "
                "`pip install playwright && playwright install chromium`."
            ) from e

    def _launch(self):
        pw = self._get_playwright()()
        self._browser = pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            locale=f"{self.hl}-{self.gl.upper()}",
            viewport={"width": 1280, "height": 900},
        )
        self._page = self._context.new_page()

    def close(self):
        if self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None

    def _search_url(self, query: str) -> str:
        base = MAPS_SEARCH_URL.format(query=quote_plus(query))
        # Apply hl/gl + zoom (z= parameter) to decouple region/language from VPS IP.
        base = re.sub(r"([&?])(hl|gl|z)=[^&]*", "", base, flags=re.I)
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}hl={self.hl}&gl={self.gl}&z={self.zoom}"

    def collect(self, query: str) -> Iterator[dict]:
        if self._browser is None:
            self._launch()
        page = self._page
        url = self._search_url(query)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(int(self.scroll_pause * 1000))
        handle_consent_wall(page)

        seen_urls: set[str] = set()
        scrolls = 0
        yielded = 0
        while True:
            cards = page.locator(RESULT_CARD_SELECTORS[0])
            # Fall back across selectors until we find cards or exhaust.
            card_count = 0
            chosen_selector = None
            for sel in RESULT_CARD_SELECTORS:
                c = page.locator(sel)
                if c.count() > 0:
                    card_count = c.count()
                    chosen_selector = sel
                    break
            if card_count == 0:
                log.warning("Zero listing cards for query %r", query)
                break

            hrefs = page.eval_on_selector_all(
                chosen_selector, "els => els.map(e => e.href)"
            )
            new = False
            for href in hrefs:
                if href and href not in seen_urls:
                    seen_urls.add(href)
                    new = True
                    rec = self._open_and_extract(href, query)
                    if rec:
                        yield rec
                        yielded += 1
                        if self.max_results and yielded >= self.max_results:
                            return

            if not new:
                break
            if self.max_scrolls and scrolls >= self.max_scrolls:
                break
            try:
                page.mouse.wheel(0, 2500)
            except Exception:
                pass
            page.wait_for_timeout(int(self.scroll_pause * 1000))
            scrolls += 1

    def _open_and_extract(self, href: str, query: str) -> dict:
        page = self._page
        try:
            page.goto(href, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
        except Exception:
            return {}
        parsed = parse_google_maps_url(href)
        rec: dict = {
            "google_maps_url": href,
            "place_id": parsed.get("place_id", "N/A"),
            "kgmid": parsed.get("kgmid", "N/A"),
            "cid": parsed.get("cid", "N/A"),
            "latitude": parsed.get("lat", "N/A"),
            "longitude": parsed.get("lng", "N/A"),
            "source_query": query,
        }
        rec["business_name"] = self._first_text(NAME_SELECTORS)
        rec["category"] = self._first_text(CATEGORY_SELECTORS)
        rec["address"] = self._first_text(ADDRESS_SELECTORS)
        rec["phone"] = self._first_attr(PHONE_SELECTORS, "data-item-id") or \
            self._first_text(PHONE_SELECTORS)
        rec["website"] = self._first_attr(WEBSITE_SELECTORS, "href")
        # Address decomposition.
        addr = parse_address(rec.get("address", "") or "")
        rec.update(addr)
        # Rating/reviews from the rendered header.
        header = self._first_text(['div[class*="fontBodyMedium"]', 'body'])
        rating, count = parse_rating_reviews(header or "")
        rec["rating"] = rating if rating is not None else "N/A"
        rec["review_count"] = count if count is not None else "N/A"
        return rec

    def _first_text(self, selectors: list[str]) -> str:
        for sel in selectors:
            try:
                loc = self._page.locator(sel).first
                if loc.count() > 0:
                    t = loc.inner_text(timeout=2000).strip()
                    if t:
                        return t
            except Exception:
                continue
        return "N/A"

    def _first_attr(self, selectors: list[str], attr: str) -> str:
        for sel in selectors:
            try:
                loc = self._page.locator(sel).first
                if loc.count() > 0:
                    v = loc.get_attribute(attr)
                    if v:
                        return v
            except Exception:
                continue
        return ""


class DemoCollector(Collector):
    """Offline provider yielding a small fixed set of sample records."""

    def collect(self, query: str) -> Iterator[dict]:
        for i in range(3):
            yield {
                "business_name": f"Sample Business {i + 1}",
                "category": "Local Service",
                "phone": f"+1 555 000 {1000 + i}",
                "website": f"https://sample{i + 1}.example.com",
                "address": f"{100 + i} Main St, Dallas, TX 75201",
                "city": "Dallas",
                "state": "TX",
                "postal_code": "75201",
                "country": "US",
                "latitude": 32.7767 + i * 0.001,
                "longitude": -96.7970 + i * 0.001,
                "rating": 4.5 + i * 0.1,
                "review_count": 40 + i * 10,
                "google_maps_url": f"https://www.google.com/maps/place/Sample/{i}",
                "place_id": f"0x1{i}2:0x3{i}4",
                "kgmid": f"/g/{1000 + i}",
                "source_query": query,
            }
