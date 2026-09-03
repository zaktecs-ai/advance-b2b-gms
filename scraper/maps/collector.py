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
from urllib.parse import quote_plus

from ..signals.social import detect_social
from .parsing import clean_maps_url, parse_google_maps_url
from .reviews import extract_reviews_from_panel
from .transform import (
    apply_url_identity,
    extract_rating_reviews,
    fallback_business_name,
)

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
                      'button[class*="category"]',
                      # G06: hotel/vertical detail panels render the category
                      # outside a <button> element.
                      'div[jsaction*="category"]', 'span[jsaction*="category"]']
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
# G01: only AUTHORITATIVE description sources. The generic
# `div[class*="fontBodyMedium"]` / bare `div.PYvSYb` selectors matched UI
# chrome ("See photos" gallery button, the "4.9 (34)" rating block) on ~100%
# of panels, polluting `business_description` on nearly every exported row.
DESCRIPTION_SELECTORS = ['div[data-item-id="editorial_summary"]',
                         'div.PZvSYb[data-item-id="editorial-summary"]']
# Text-quality fallback (G01 part 2): the bare editorial-styled block is only
# trusted when it is long enough to be real editorial text.
_DESCRIPTION_FALLBACK_SELECTOR = 'div.PYvSYb'
_DESC_FALLBACK_MIN_CHARS = 60

# UI chrome / rating-block shapes that must never be exported as a
# description (defense in depth — see G01 evidence G1-E/G1-E2).
_DESC_JUNK_RE = re.compile(
    r"^(?:see\s+(?:all\s+)?photos?|photos?|\d+\s*photos?)$"
    r"|^\d[.,]\d\s*\(\d[\d,]*\)\s*$"
    r"|^(?:open|closed)\b", re.I)


def clean_description(text: str | None) -> str:
    """Return a cleaned description, or 'N/A' when the text is UI junk."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if not t or _DESC_JUNK_RE.match(t):
        return "N/A"
    return t


def _status_from_hours(hours: str | None) -> str | None:
    """G06: conservative open-state inference from hours text.

    Only unambiguous evidence ("Open 24 hours") is inferred; posted ranges
    ("8 AM to 5 PM") say nothing about open-right-now, so they yield None.
    """
    h = (hours or "").lower()
    if not h or h == "n/a":
        return None
    if re.search(r"open\s*24\s*hours", h):
        return "Open"
    return None


def _clean_plus_code(text: str | None) -> str:
    """G12: collapse internal whitespace and strip edges of a plus code."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return t or "N/A"
ABOUT_SELECTORS = ['div[data-item-id="about"]', 'button[jsaction*="about"]']



def _first_text(page, selectors, timeout=2000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                txt = loc.inner_text(timeout=timeout).strip()
                if txt:
                    return txt
        except Exception as e:
            log.debug("selector miss: %s (%s)", sel, e)
    return None


def _first_attr(page, selector, attr, timeout=2000):
    try:
        loc = page.locator(selector).first
        if loc.count() > 0:
            return loc.get_attribute(attr, timeout=timeout)
    except Exception as e:
        log.debug("selector miss: %s (%s)", selector, e)
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
    except Exception as e:
        log.debug("hours selector miss: %s", e)
    for sel in HOURS_TABLE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                txt = loc.inner_text(timeout=1500).strip()
                if txt:
                    return txt
        except Exception as e:
            log.debug("hours selector miss: %s (%s)", sel, e)
    return "N/A"


# Internal/utility href tokens that must never be treated as a business's own
# social profile. Google's own place/dir/search URLs dominate the surrounding
# results feed and previously cross-contaminated the facebook column (F01).
_FORBIDDEN_HREF_TOKENS = ("google.com/maps/place/", "/maps/dir/",
                          "google.com/maps/search/")


def filter_panel_hrefs(hrefs: list[str]) -> list[str]:
    """Drop Google Maps navigation URLs from a candidate social-link set.

    Pure helper so scoping is unit-testable without a browser.
    """
    return [h for h in (hrefs or [])
            if h and not any(tok in h for tok in _FORBIDDEN_HREF_TOKENS)]


def _extract_social_links(page) -> dict:
    """Read anchors ONLY from the open detail panel; classify in pure code."""
    hrefs: list[str] = []
    for sel in ('div[role="main"] div[role="complementary"] a[href]',
                'div[role="main"] a[href]'):
        try:
            loc = page.locator(sel)
            n = loc.count()
            if n:
                hrefs = [loc.nth(i).get_attribute("href") or "" for i in range(n)]
                break
        except Exception as e:
            log.debug("social panel scope miss: %s (%s)", sel, e)
    return detect_social(filter_panel_hrefs(hrefs))


# Generic category/service words that appear in the slug AND the query keyword
# but carry no identity signal. Excluding them keeps the panel/URL coherence
# check discriminating ("Cooper Plumbing" vs "Nick's Plumbing" both contain
# "plumbing" but are clearly different businesses).
_NAME_GENERIC_WORDS = {
    "plumbing", "plumber", "plumbers", "heating", "cooling", "air",
    "conditioning", "electric", "electrical", "service", "services",
    "company", "the", "and", "repair", "repairs", "contractor",
    "contractors", "llc", "inc",
}


def _names_compatible(a: str, b: str) -> bool:
    """True when two name strings share at least one significant token.

    Used to detect a panel/URL mismatch (the one-row-shift bug): the business
    name read from the detail panel must share a word with the slug in its own
    URL. Generic service words are ignored; empty side → assume compatible.
    """
    def _tokens(s: str) -> set[str]:
        return {
            t for t in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(t) > 2 and t not in _NAME_GENERIC_WORDS
        }
    ta = _tokens(a)
    tb = _tokens(b)
    if not ta or not tb:
        return True
    return len(ta & tb) >= 1


def digits_to_intl(raw: str) -> str:
    """Normalize a scraped phone attribute to international form.

    Pure helper so the transformation is unit-testable without a browser. A
    ``+...`` international fragment is preserved as-is; a bare digit run gets
    a leading ``+``; anything else is ``N/A``. (Previously ``re.sub(r"\\D", …)``
    could never yield a leading ``+``, so the ``startswith("+")`` check was a
    dead branch.)
    """
    raw = (raw or "").strip()
    m = re.search(r"\+[\d\s().-]+", raw)
    if m:
        digits = re.sub(r"[^\d+]", "", m.group(0))
        return digits if digits else "N/A"
    digits = re.sub(r"\D", "", raw)
    return ("+" + digits) if digits else "N/A"


def _extract_phone_international(page) -> str:
    raw = _first_attr(page, PHONE_SELECTORS[0], "data-item-id") or ""
    return digits_to_intl(raw)


class MapsCollector:
    """Stream raw browser-extracted listing dicts for one Maps query.

    Deterministic normalization and schema projection happen in
    ``scraper.maps.transform`` rather than in this Playwright adapter.
    """

    def __init__(self, browser_manager, *, max_results_per_query: int = 0,
                 max_total_results: int = 0, include_permanently_closed: bool = False,
                 scroll_delay: tuple = (800, 1600), cooldown_seconds: float = 0.0,
                 hl: str = "en", gl: str = "us",
                 maps_delay: tuple = (0.0, 0.0),
                 reviews_per_business: int = 5, collect_reviews: bool = True,
                 on_query_total=None):
        self._bm = browser_manager
        self._max_per_query = max_results_per_query
        self._max_total = max_total_results
        self._include_closed = include_permanently_closed
        self._scroll_delay = scroll_delay
        self._cooldown = cooldown_seconds
        self._hl = hl
        self._gl = gl
        self._maps_delay = maps_delay
        self._reviews_per_business = reviews_per_business
        self._collect_reviews = collect_reviews
        self._on_query_total = on_query_total  # callable(len(listing_links))
        self._yielded_total = 0
        self.limit_reached = False

    def close(self) -> None:
        pass

    def collect(self, query: str) -> Iterator[dict]:
        # Create the context OUTSIDE try/finally is a leak when page creation
        # fails; wrap everything so a failure at any point still tears the
        # context down (F14).
        ctx = self._bm.new_context()
        try:
            page = ctx.new_page()
            try:
                page.set_default_timeout(self._bm.nav_timeout_ms)
                yield from self._collect_on_page(query, page)
            finally:
                try:
                    page.close()
                except Exception as e:
                    log.debug("page close: %s", e)
        finally:
            try:
                ctx.close()
            except Exception as e:
                log.debug("ctx close: %s", e)

    def _collect_on_page(self, query: str, page) -> Iterator[dict]:
        url = _with_region(MAPS_SEARCH_URL.format(query=quote_plus(query)),
                           self._hl, self._gl)
        yielded = 0
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        # Wait for the results feed (or a heading) instead of a blind sleep
        # so a slow network no longer drops data (F29).
        try:
            page.wait_for_selector('div[role="feed"], h1', timeout=15_000)
        except Exception:
            time.sleep(2.0)

        if handle_consent_wall(page):
            time.sleep(3.0)

        if detect_bot_challenge(page.content()):
            log.warning("bot challenge for query %r — cooling down %.0fs",
                        query, self._cooldown)
            # A bot challenge often means the egress IP (proxy) is flagged;
            # feed that back so the proxy drops out of rotation (A3).
            try:
                self._bm.report_proxy_failure()
            except Exception:
                pass
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

        # Notify the caller of the total number of result cards found, so
        # progress can render a "processing 12 of 96" style counter.
        if self._on_query_total is not None:
            try:
                self._on_query_total(len(listing_links))
            except Exception:
                pass

        for pos, place_url in enumerate(listing_links, start=1):
            if self._max_total and self._yielded_total >= self._max_total:
                self.limit_reached = True
                break
            if self._max_per_query and yielded >= self._max_per_query:
                break
            data = self._open_and_extract(page, place_url, position=pos,
                                          total=len(listing_links))
            if not data.get("business_name"):
                data["business_name"] = fallback_business_name(place_url)
            data["source_query"] = query
            status = (data.get("business_status") or "").lower()
            if ("permanently closed" in status) and not self._include_closed:
                continue
            yielded += 1
            self._yielded_total += 1
            yield data
            self._small_pause()
            self._maps_pacing_pause()

    # -- click-driven detail-panel extraction -----------------------------
    def _open_and_extract(self, page, place_url: str, position: int = 0,
                          total: int = 0) -> dict:
        data: dict = {}
        data["_position"] = position
        data["_total"] = total
        opened = self._click_to_open(page, place_url)
        if not opened:
            try:
                page.goto(_with_region(place_url, self._hl, self._gl),
                          wait_until="domcontentloaded",
                          timeout=self._bm.nav_timeout_ms)
                # Wait for the detail panel (or a heading) to hydrate instead of
                # a blind sleep, so a slow switch cannot drop the panel text
                # (F02/F29).
                try:
                    page.wait_for_selector('div[role="feed"], h1, div[role="main"]',
                                           timeout=15_000)
                except Exception:
                    time.sleep(1.5)
            except Exception as e:  # A5: log so a failed goto isn't silently N/A
                log.debug("goto fallback failed for %s: %s", place_url, e)
                try:
                    self._bm.report_proxy_failure()
                except Exception:
                    pass
                return data

        try:
            page.wait_for_selector('h1', timeout=10_000)
        except Exception as e:
            log.debug("detail-panel h1 wait missed for %s: %s", place_url, e)
        # Panel identity guard: prove the detail panel belongs to the clicked
        # place before reading its fields. On a slow switch the panel still
        # shows the PREVIOUS business, which is the exact one-row-shift
        # contamination seen in production (F02).
        expected_slug = (parse_google_maps_url(place_url).get("place_name") or "")
        try:
            page.wait_for_function(
                """slug => {
                    const h = document.querySelector('h1');
                    if (!h || !h.textContent.trim()) return false;
                    if (!slug) return true;
                    const key = decodeURIComponent(slug).toLowerCase()
                        .replace(/[-+]/g, ' ').split(/\\s+/)
                        .filter(t => t.length > 2)[0] || '';
                    return !key || h.textContent.trim().toLowerCase().includes(key);
                }""",
                arg=expected_slug, timeout=6_000)
        except Exception:
            log.debug("panel identity guard timeout for %s", place_url)
        # Bounded wait on the panel name marker instead of a blind sleep (A4).
        try:
            page.wait_for_selector(NAME_SELECTORS[0], timeout=5_000)
        except Exception:
            time.sleep(1.0)

        data["business_name"] = _first_text(page, NAME_SELECTORS)
        data["category"] = _first_text(page, CATEGORY_SELECTORS)
        data["full_address"] = _first_text(page, ADDRESS_SELECTORS) or "N/A"
        data["address"] = data["full_address"]

        data["phone"] = _first_attr(page, PHONE_SELECTORS[0], "data-item-id") or \
            _first_text(page, PHONE_SELECTORS) or "N/A"
        data["phone_international"] = _extract_phone_international(page)
        data["website"] = _first_attr(page, WEBSITE_SELECTORS[0], "href") or \
            _first_attr(page, WEBSITE_SELECTORS[1], "href") or "N/A"

        data["plus_code"] = _clean_plus_code(
            _first_text(page, PLUS_CODE_SELECTORS))

        data["rating"], data["review_count"] = self._extract_rating_reviews(page)

        # Reviews: if enabled, scroll the detail panel's review feed and
        # capture top review texts for the analysis stage (sentiment/keywords).
        if self._collect_reviews:
            try:
                data["_reviews"] = extract_reviews_from_panel(
                    page, max_reviews=self._reviews_per_business)
            except Exception:
                data["_reviews"] = []

        data["business_hours"] = _extract_hours(page)
        data["business_status"] = self._business_status(page)
        # G06 part 2: when no status chip rendered, a conservative inference
        # from the hours text ("Open 24 hours" -> Open). Normal posted hours
        # do NOT imply open-now, so they stay honest "N/A".
        if data["business_status"] == "N/A":
            data["business_status"] = _status_from_hours(
                data["business_hours"]) or "N/A"
        data["claimed_status"] = self._claimed_status(page)
        raw_desc = _first_text(page, DESCRIPTION_SELECTORS)
        if not raw_desc:
            # G01 text-quality fallback: trust the generic editorial block
            # only when it is long enough to be real editorial text.
            fallback = _first_text(page, [_DESCRIPTION_FALLBACK_SELECTOR])
            if fallback and len(fallback) >= _DESC_FALLBACK_MIN_CHARS:
                raw_desc = fallback
        data["business_description"] = clean_description(raw_desc)
        data["about"] = _first_text(page, ABOUT_SELECTORS) or "N/A"

        data.update(_extract_social_links(page))

        data["google_maps_url"] = clean_maps_url(page.url)
        apply_url_identity(data, page.url)

        # Coherence sentinel: the panel's business name must share a token with
        # the URL's place slug. On a mismatch the panel still shows the previous
        # business, so retry once via the goto fallback before yielding
        # contaminated data (F02).
        url_name = (parse_google_maps_url(place_url).get("place_name") or "")
        if not _names_compatible(url_name, data.get("business_name") or ""):
            log.warning("panel/URL name mismatch for %s — retrying via goto", place_url)
            try:
                page.goto(_with_region(place_url, self._hl, self._gl),
                          wait_until="domcontentloaded",
                          timeout=self._bm.nav_timeout_ms)
                try:
                    page.wait_for_selector('h1, div[role="feed"], div[role="main"]',
                                           timeout=15_000)
                except Exception:
                    time.sleep(1.5)
                data["business_name"] = _first_text(page, NAME_SELECTORS)
            except Exception as e:
                log.debug("coherence retry failed for %s: %s", place_url, e)
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
                        # Prove the detail panel switched to the clicked place
                        # (URL changed) instead of sleeping blindly (F02).
                        try:
                            page.wait_for_function(
                                "href => location.href.includes(decodeURIComponent(href))",
                                arg=place_url, timeout=8_000)
                            return True
                        except Exception:
                            return False
            except Exception as e:
                log.debug("click-to-open miss: %s (%s)", sel, e)
        return False

    def _extract_rating_reviews(self, page):
        for sel in RATING_BLOCK_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    rating, count = extract_rating_reviews(loc.inner_text(timeout=2000))
                    if rating != "N/A" or count != "N/A":
                        return rating, count
            except Exception as e:
                log.debug("rating selector miss: %s (%s)", sel, e)
        for sel in REVIEW_COUNT_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    aria = loc.get_attribute("aria-label", timeout=2000) or ""
                    rating, count = extract_rating_reviews(aria)
                    if rating != "N/A" or count != "N/A":
                        return rating, count
            except Exception as e:
                log.debug("review-count selector miss: %s (%s)", sel, e)
        try:
            header = page.locator('div[role="main"]').inner_text(timeout=2000)
        except Exception as e:
            log.debug("rating header miss: %s", e)
            header = ""
        return extract_rating_reviews(header or "")

    def _claimed_status(self, page) -> str:
        try:
            if page.locator(CLAIM_SELECTOR).count() > 0:
                return "Unclaimed"
        except Exception as e:
            log.debug("claim selector miss: %s", e)
        return "Claimed"

    def _business_status(self, page) -> str:
        try:
            if page.locator("text=Permanently closed").count() > 0:
                return "Permanently closed"
        except Exception as e:
            log.debug("closed-status selector miss: %s", e)
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
            except Exception as e:
                log.debug("status selector miss: %s (%s)", sel, e)
        return "N/A"

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
        # Preserve DOM (Maps relevance-ranked) order while deduping via a side
        # set. A bare `set` -> `list` has no stable order across processes
        # (hash randomization), so a capped first-N slice would otherwise pick a
        # different subset of businesses on every run.
        links: list = []
        seen: set = set()
        for sel in RESULT_CARD_SELECTORS:
            try:
                locs = page.locator(sel)
                n = locs.count()
                for i in range(n):
                    href = locs.nth(i).get_attribute("href", timeout=1500)
                    if href and "/maps/place/" in href and href not in seen:
                        seen.add(href)
                        links.append(href)
            except Exception:
                continue
        return links

    def _small_pause(self) -> None:
        lo, hi = self._scroll_delay
        time.sleep(random.uniform(lo, hi) / 1000.0)

    def _maps_pacing_pause(self) -> None:
        lo, hi = self._maps_delay
        if hi > 0:
            time.sleep(random.uniform(lo, hi) if hi > lo else hi)


_DEMO_REVIEWS = [
    "Great service, very professional and friendly team!",
    "Quick, reliable, and reasonably priced. Highly recommend.",
    "They arrived on time and did an excellent clean job.",
]


class DemoCollector:
    """Offline provider yielding a fixed set of rich sample records."""

    def collect(self, query: str) -> Iterator[dict]:
        for i in range(3):
            yield {
                "_position": i + 1,
                "_total": 3,
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
                "_reviews": [r for j, r in enumerate(_DEMO_REVIEWS) if j <= i],
            }

    def close(self) -> None:
        pass
