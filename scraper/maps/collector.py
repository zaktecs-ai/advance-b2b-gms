"""Google Maps live collector (Playwright) — deep detail-panel extraction.

Strategy (layered, resilient to selector drift):
  1. Navigate to a Maps search URL for a query.
  2. Scroll the results feed (div[role="feed"]) to reveal listings.
  3. For each listing, CLICK the card (or its place link) to open the live
     detail panel, then explicitly WAIT for the panel to hydrate before
     extracting every field via PRIMARY -> ALTERNATE -> semantic fallback
     selectors, plus regex fallbacks in pure helpers.

This is the fix for the original 52-empty-columns bug: the old collector only
did ``page.goto(href)`` and grabbed a dozen fields. This one drives the SPA
click flow and reads the fully-rendered detail panel.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Iterator
from urllib.parse import quote_plus, urlparse

from .parsing import (
    parse_google_maps_url,
    parse_rating_reviews,
    decompose_address,
    parse_popular_times,
)
from ..utils.text import to_int, to_float

log = logging.getLogger(__name__)

MAPS_SEARCH_URL = "https://www.google.com/maps/search/{query}"


class ZeroListingsError(RuntimeError):
    """Raised when a non-empty Maps search yields zero listing URLs."""

    def __init__(self, query: str, diagnostic: str = ""):
        self.query = query
        self.diagnostic = diagnostic
        detail = f" — diagnostic: {diagnostic}" if diagnostic else ""
        super().__init__(
            f"0 listings extracted for query '{query}'{detail} — likely "
            f"consent wall / selector drift / interstitial (not 'no results')."
        )


# ---------------------------------------------------------------------------
# Consent + bot-challenge handling
# ---------------------------------------------------------------------------

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

_BOT_MARKERS = [
    "unusual traffic", "unusual traffic from your computer network",
    "captcharedirect", "g-recaptcha",
]


def handle_consent_wall(page) -> bool:
    """Dismiss the EU GDPR consent screen if present. Returns True if it acted."""
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


def detect_bot_challenge(html_or_text: str) -> bool:
    if not html_or_text:
        return False
    low = html_or_text.lower()
    return any(m.lower() in low for m in _BOT_MARKERS)


def _page_diagnostic(page) -> str:
    try:
        text = page.inner_text("body") or ""
    except Exception:
        text = ""
    snippet = " ".join(text.split())[:200]
    url = ""
    try:
        url = page.url
    except Exception:
        pass
    return f"url={url[:120]} text={snippet!r}"


def _with_region(url: str, hl: str, gl: str) -> str:
    base = re.sub(r"([&?])(hl|gl)=[^&]*", "", url, flags=re.I)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}hl={hl}&gl={gl}"


# ---------------------------------------------------------------------------
# Selector layers
# ---------------------------------------------------------------------------

RESULT_CARD_SELECTORS = [
    'a.hfpxzc',
    'a[href*="/maps/place/"]',
    'a[aria-label]',
]

NAME_SELECTORS = ['h1.DUwDvf', 'h1[class*="fontHeadline"]', 'h1']
CATEGORY_SELECTORS = ['button.DkEaL', 'button[jsaction*="category"]',
                      'button[class*="category"]']
ADDRESS_SELECTORS = ['button[data-item-id="address"]', 'div[data-item-id="address"]',
                     'div[class*="address"]']
PHONE_SELECTORS = ['button[data-item-id^="phone:tel:"]', 'button[data-item-id^="phone"]']
WEBSITE_SELECTORS = ['a[data-item-id="authority"]', 'a[aria-label*="Website"]']
PLUS_CODE_SELECTORS = ['button[data-item-id*="oloc"]', 'button[aria-label*="Plus code"]',
                       'button[data-item-id*="plus"]']
CLAIM_SELECTOR = 'a[data-item-id="merchant_claim_business"]'
RATING_BLOCK_SELECTORS = ['div.F7nice', 'div[aria-label*="stars"]',
                          'span[aria-label*="stars"]']
REVIEW_COUNT_SELECTORS = ['button[aria-label*="reviews"]',
                          'button[jsaction*="review"]',
                          'span[aria-label*="reviews"]']
HOURS_TABLE_SELECTORS = ['table.eK4R0e', 'table[class*="hours"]']
HOURS_ROW_SELECTOR = 'button[aria-label*="Copy open hours"]'
STATUS_SELECTORS = ['span.ZDu9vd', 'div.o0Svhf span',
                    '[aria-label="Open"], [aria-label="Closed"]']
DESCRIPTION_SELECTORS = ['div[data-item-id="editorial_summary"]',
                         'div[class*="fontBodyMedium"]', 'div.PYvSYb']
ABOUT_SELECTORS = ['div[data-item-id="about"]', 'button[jsaction*="about"]']

_SOCIAL_HOSTS = {
    "facebook": r"(^|\.)facebook\.com$",
    "instagram": r"(^|\.)instagram\.com$",
    "linkedin": r"(^|\.)linkedin\.com$",
    "youtube": r"(^|\.)youtube\.com$|(^|\.)youtu\.be$",
    "twitter_x": r"(^|\.)twitter\.com$|(^|\.)x\.com$",
    "tiktok": r"(^|\.)tiktok\.com$",
    "pinterest": r"(^|\.)pinterest\.com$",
    "github": r"(^|\.)github\.com$",
    "snapchat": r"(^|\.)snapchat\.com$",
}


def _platform_for_host(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    for platform, pattern in _SOCIAL_HOSTS.items():
        if re.search(pattern, host):
            return platform
    return None


def _first_text(page, selectors, timeout=2000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                txt = loc.inner_text(timeout=timeout).strip()
                if txt:
                    return txt
        except Exception:
            continue
    return None


def _first_attr(page, selector, attr, timeout=2000):
    try:
        loc = page.locator(selector).first
        if loc.count() > 0:
            return loc.get_attribute(attr, timeout=timeout)
    except Exception:
        return None
    return None


def _extract_hours(page) -> str:
    try:
        rows = page.locator(HOURS_ROW_SELECTOR)
        if rows.count() > 0:
            labels = []
            for i in range(rows.count()):
                aria = rows.nth(i).get_attribute("aria-label", timeout=1500) or ""
                label = aria.split(", Copy open hours")[0]
                label = re.sub(r",\s*(?=\d)", ": ", label, count=1)
                labels.append(label)
            if labels:
                return "; ".join(labels)
    except Exception:
        pass
    for sel in HOURS_TABLE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                txt = loc.inner_text(timeout=1500).strip()
                if txt:
                    return txt
        except Exception:
            continue
    return "N/A"


def _extract_social_links(page) -> dict:
    result = {k: "N/A" for k in _SOCIAL_HOSTS}
    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    except Exception:
        hrefs = []
    for href in hrefs:
        if not href:
            continue
        p = _platform_for_host(href)
        if p and result[p] == "N/A":
            result[p] = href
    return result


def _extract_phone_international(page) -> str:
    raw = _first_attr(page, PHONE_SELECTORS[0], "data-item-id") or ""
    m = re.search(r"\+[\d\s().-]+", raw)
    if m:
        return re.sub(r"[^\d+]", "", m.group(0))
    if raw:
        digits = re.sub(r"\D", "", raw)
        if digits:
            return ("+" + digits) if not digits.startswith("+") else digits
    return "N/A"


class MapsCollector:
    """Streams normalized business dicts from Google Maps for one query."""

    def __init__(self, browser_manager, *, max_results_per_query: int = 0,
                 max_total_results: int = 0, include_permanently_closed: bool = False,
                 scroll_delay: tuple = (800, 1600), cooldown_seconds: float = 0.0,
                 hl: str = "en", gl: str = "us",
                 maps_delay: tuple = (0.0, 0.0)):
        self._bm = browser_manager
        self._max_per_query = max_results_per_query
        self._max_total = max_total_results
        self._include_closed = include_permanently_closed
        self._scroll_delay = scroll_delay
        self._cooldown = cooldown_seconds
        self._hl = hl
        self._gl = gl
        self._maps_delay = maps_delay
        self._yielded_total = 0
        self.limit_reached = False

    def close(self) -> None:
        pass

    def collect(self, query: str) -> Iterator[dict]:
        ctx = self._bm.new_context()
        page = ctx.new_page()
        page.set_default_timeout(self._bm.nav_timeout_ms)
        url = _with_region(MAPS_SEARCH_URL.format(query=quote_plus(query)),
                           self._hl, self._gl)
        yielded = 0
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            time.sleep(3.0)

            if handle_consent_wall(page):
                time.sleep(3.0)

            if detect_bot_challenge(page.content()):
                log.warning("bot challenge for query %r — cooling down %.0fs",
                            query, self._cooldown)
                if self._cooldown:
                    time.sleep(self._cooldown)
                raise ZeroListingsError(query, "bot challenge / CAPTCHA detected")

            self._scroll_results(page)
            listing_links = self._extract_listing_links(page)
            log.info("query %r: found %d listing place URLs", query, len(listing_links))

            if not listing_links:
                try:
                    body_text = page.locator("body").inner_text(timeout=2000).lower()
                except Exception:
                    body_text = ""
                if "no results" in body_text or "could not find" in body_text:
                    log.info("query %r has genuinely no results — done", query)
                    return
                raise ZeroListingsError(query, _page_diagnostic(page))

            for place_url in listing_links:
                if self._max_total and self._yielded_total >= self._max_total:
                    self.limit_reached = True
                    break
                if self._max_per_query and yielded >= self._max_per_query:
                    break
                data = self._open_and_extract(page, place_url)
                if not data.get("business_name"):
                    parsed = parse_google_maps_url(place_url)
                    data["business_name"] = parsed.get("place_name") or "N/A"
                data["source_query"] = query
                status = (data.get("business_status") or "").lower()
                if ("permanently closed" in status) and not self._include_closed:
                    continue
                yielded += 1
                self._yielded_total += 1
                yield data
                self._small_pause()
                self._maps_pacing_pause()
        finally:
            try:
                page.close()
                ctx.close()
            except Exception:
                pass

    # -- click-driven detail-panel extraction -----------------------------
    def _open_and_extract(self, page, place_url: str) -> dict:
        data: dict = {}
        opened = self._click_to_open(page, place_url)
        if not opened:
            try:
                page.goto(_with_region(place_url, self._hl, self._gl),
                          wait_until="domcontentloaded",
                          timeout=self._bm.nav_timeout_ms)
                time.sleep(1.5)
            except Exception:
                return data

        try:
            page.wait_for_selector('h1', timeout=10_000)
        except Exception:
            pass
        time.sleep(1.2)

        data["business_name"] = _first_text(page, NAME_SELECTORS)
        data["category"] = _first_text(page, CATEGORY_SELECTORS)
        data["full_address"] = _first_text(page, ADDRESS_SELECTORS) or "N/A"
        data["address"] = data["full_address"]

        data["phone"] = _first_attr(page, PHONE_SELECTORS[0], "data-item-id") or \
            _first_text(page, PHONE_SELECTORS) or "N/A"
        data["phone_international"] = _extract_phone_international(page)
        data["website"] = _first_attr(page, WEBSITE_SELECTORS[0], "href") or \
            _first_attr(page, WEBSITE_SELECTORS[1], "href") or "N/A"

        data["plus_code"] = _first_text(page, PLUS_CODE_SELECTORS) or "N/A"
        data["timezone"] = "N/A"

        data["rating"], data["review_count"] = self._extract_rating_reviews(page)

        data["business_hours"] = _extract_hours(page)
        data["business_status"] = self._business_status(page)
        data["claimed_status"] = self._claimed_status(page)
        data["business_description"] = _first_text(page, DESCRIPTION_SELECTORS) or "N/A"
        data["about"] = _first_text(page, ABOUT_SELECTORS) or "N/A"

        data.update(_extract_social_links(page))

        parsed = parse_google_maps_url(page.url)
        for key, col in (("lat", "latitude"), ("lng", "longitude"),
                         ("place_id", "place_id"), ("cid", "cid"), ("kgmid", "kgmid")):
            if parsed.get(key):
                data[col] = parsed[key]
        data["google_maps_url"] = page.url

        data.update(decompose_address(data.get("full_address") or ""))

        return data

    def _click_to_open(self, page, place_url: str) -> bool:
        for sel in RESULT_CARD_SELECTORS:
            try:
                locs = page.locator(sel)
                n = locs.count()
                for i in range(n):
                    href = locs.nth(i).get_attribute("href", timeout=1500)
                    if href and href == place_url:
                        locs.nth(i).click(timeout=5000)
                        time.sleep(1.0)
                        return True
            except Exception:
                continue
        return False

    def _extract_rating_reviews(self, page):
        for sel in RATING_BLOCK_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    rating, count = parse_rating_reviews(loc.inner_text(timeout=2000))
                    if rating is not None or count is not None:
                        return (rating if rating is not None else "N/A",
                                count if count is not None else "N/A")
            except Exception:
                continue
        for sel in REVIEW_COUNT_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    aria = loc.get_attribute("aria-label", timeout=2000) or ""
                    rating, count = parse_rating_reviews(aria)
                    if rating is not None or count is not None:
                        return (rating if rating is not None else "N/A",
                                count if count is not None else "N/A")
            except Exception:
                continue
        try:
            header = page.locator('div[role="main"]').inner_text(timeout=2000)
        except Exception:
            header = ""
        rating, count = parse_rating_reviews(header or "")
        return (rating if rating is not None else "N/A",
                count if count is not None else "N/A")

    def _claimed_status(self, page) -> str:
        try:
            if page.locator(CLAIM_SELECTOR).count() > 0:
                return "Unclaimed"
        except Exception:
            pass
        return "Claimed"

    def _business_status(self, page) -> str:
        try:
            if page.locator("text=Permanently closed").count() > 0:
                return "Permanently closed"
        except Exception:
            pass
        for sel in STATUS_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    txt = loc.inner_text(timeout=1500).strip()
                    if txt:
                        low = txt.lower()
                        if low.startswith(("open", "opens")):
                            return "Open"
                        if low.startswith(("closed", "closes")):
                            return "Closed"
                        return txt.split("·")[0].strip()
            except Exception:
                continue
        return "Open"

    # -- feed scrolling / link extraction ---------------------------------
    def _scroll_results(self, page) -> None:
        feed = None
        try:
            loc = page.locator('div[role="feed"]')
            if loc.count() > 0:
                feed = loc.first
        except Exception:
            feed = None

        for _ in range(12):
            try:
                if feed is not None:
                    feed.evaluate("el => el.scrollTo(0, el.scrollHeight)")
                else:
                    page.mouse.wheel(0, 1200)
            except Exception:
                page.mouse.wheel(0, 1200)
            lo, hi = self._scroll_delay
            time.sleep(random.uniform(lo, hi) / 1000.0)
            if self._has_no_more_results(page):
                break

    def _has_no_more_results(self, page) -> bool:
        try:
            body = page.locator("body")
            if body.count() == 0:
                return False
            return "You've reached the end of the list" in body.inner_text(timeout=1500)
        except Exception:
            return False

    def _extract_listing_links(self, page) -> list:
        links: set = set()
        for sel in RESULT_CARD_SELECTORS:
            try:
                locs = page.locator(sel)
                n = locs.count()
                for i in range(n):
                    href = locs.nth(i).get_attribute("href", timeout=1500)
                    if href and "/maps/place/" in href:
                        links.add(href)
            except Exception:
                continue
        return list(links)

    def _small_pause(self) -> None:
        lo, hi = self._scroll_delay
        time.sleep(random.uniform(lo, hi) / 1000.0)

    def _maps_pacing_pause(self) -> None:
        lo, hi = self._maps_delay
        if hi > 0:
            time.sleep(random.uniform(lo, hi) if hi > lo else hi)


class DemoCollector:
    """Offline provider yielding a fixed set of rich sample records."""

    def collect(self, query: str) -> Iterator[dict]:
        for i in range(3):
            yield {
                "business_name": f"Sample Business {i + 1}",
                "category": "Local Service",
                "subcategory": "Plumber",
                "phone": f"phone:tel:+1 555 000 {1000 + i}",
                "phone_international": f"+1555000{1000 + i}",
                "website": f"https://sample{i + 1}.example.com",
                "address": f"{100 + i} Main St, Dallas, TX 75201",
                "full_address": f"{100 + i} Main St, Dallas, TX 75201, United States",
                "city": "Dallas",
                "state": "TX",
                "postal_code": "75201",
                "country": "US",
                "latitude": 32.7767 + i * 0.001,
                "longitude": -96.797 + i * 0.001,
                "rating": 4.5 + i * 0.1,
                "review_count": 40 + i * 10,
                "claimed_status": "Claimed",
                "business_status": "Open",
                "business_hours": "Mon: 9 AM to 5 PM; Tue: 9 AM to 5 PM",
                "business_description": "Sample plumbing service.",
                "google_maps_url": f"https://www.google.com/maps/place/Sample/{i}",
                "place_id": f"0x1{i}2:0x3{i}4",
                "cid": f"0x1{i}2:0x3{i}4",
                "kgmid": f"/g/{1000 + i}",
                "source_query": query,
            }

    def close(self) -> None:
        pass
