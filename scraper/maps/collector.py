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
# --- Photos / owner-activity columns (owner decision: business_description
# ELIMINATED from the engine - production showed only "See photos" junk) ----

COVER_IMAGE_SELECTORS = ['button.aoRNLd img',
                         'button[jsaction*="heroHeaderImage"] img',
                         'div.ZKCDEc img',
                         'button.K4UgGe[data-carousel-index="0"] img']
LATEST_PHOTO_LABEL_SELECTOR = 'button[aria-label^="Latest"]'
BY_OWNER_PHOTO_SELECTOR = 'button[aria-label="By owner"]'
FROM_OWNER_HEADING_SELECTOR = 'h2:has-text("From the owner")'
FROM_OWNER_DATE_SELECTORS = ['div.S3NLN .lqMB', '.SBD2Rc .lqMB']


def parse_latest_upload_label(label):
    """Parse 'Latest · 11 days ago' (carousel aria-label) -> '11 days ago'.

    Returns 'N/A' when the label or the separator segment is missing.
    """
    if not label:
        return "N/A"
    parts = label.split("·")
    if len(parts) < 2:
        return "N/A"
    value = parts[1].strip()
    return value or "N/A"


def _yes_no(flag):
    return "YES" if flag else "NO"


def _settle_panel(page, rounds: int = 4, pause_ms: int = 400) -> None:
    """Scroll the detail panel so lazy sections (hero photo, photos carousel,
    owner posts) hydrate before extraction. Best-effort, never raises.

    The wheel only scrolls the element under the cursor, so the cursor is
    moved INTO the detail panel (div[role=main]) first - over the feed it
    would scroll the results list instead and the panel stays unhydrated
    (live-verified)."""
    try:
        panel = page.locator('div[role="main"]').first
        if panel.count() > 0:
            box = panel.bounding_box()
            if box:
                page.mouse.move(box["x"] + box["width"] / 2.0,
                                box["y"] + min(box["height"] / 2.0, 400.0))
        for _ in range(rounds):
            page.mouse.wheel(0, 900)
            time.sleep(pause_ms / 1000.0)
    except Exception:  # noqa: BLE001
        pass


def _scroll_photos_into_view(page) -> None:
    """Bring the photos carousel and hero header into view so their media
    hydrates. Called once per extraction, plus on retry when the cover image
    read still misses. Best-effort, never raises."""
    for sel in ('.fp2VUc', 'div.ZKCDEc', 'button.aoRNLd'):
        try:
            page.locator(sel).first.scroll_into_view_if_needed(timeout=1500)
        except Exception:  # noqa: BLE001 - selector may not exist per listing
            continue
    time.sleep(0.6)


def _deep_scroll_panel(page, steps: int = 6, pause_ms: int = 300) -> None:
    """Scroll every tall scrollable container to its bottom, in steps.

    Maps virtualizes deep panel sections ("From the owner" posts sit far
    below the photos carousel) - they only enter the DOM when scrolled into
    view. Best-effort, never raises.
    """
    try:
        for _ in range(steps):
            page.evaluate(
                "() => { for (const e of document.querySelectorAll('div')) {"
                " if (e.scrollHeight > e.clientHeight + 100 &&"
                " e.clientHeight > 300) { e.scrollTop = e.scrollHeight; } } }")
            time.sleep(pause_ms / 1000.0)
    except Exception:  # noqa: BLE001
        pass


def _read_photo_columns(page) -> dict:
    """Read the photos-carousel + hero columns from the currently open panel."""
    out: dict = {}
    cover = "N/A"
    for sel in COVER_IMAGE_SELECTORS:
        cover = _first_attr(page, sel, "src") or "N/A"
        if cover != "N/A":
            break
    out["cover_image_url"] = cover
    try:
        latest_label = page.locator(LATEST_PHOTO_LABEL_SELECTOR).first \
            .get_attribute("aria-label", timeout=1500)
    except Exception:  # noqa: BLE001
        latest_label = None
    out["latest_image_upload"] = parse_latest_upload_label(latest_label)
    try:
        out["by_owner_photos"] = _yes_no(
            page.locator(BY_OWNER_PHOTO_SELECTOR).count() > 0)
    except Exception:  # noqa: BLE001
        out["by_owner_photos"] = "NO"
    return out


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
                 on_query_total=None,
                 max_scrolls: int = 0, scroll_pause_seconds: float = 0.0):
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
        # maps.max_scrolls: hard cap on scroll rounds (0 = built-in safety
        # bound of 12). maps.scroll_pause_seconds: extra settle wait when the
        # feed height stops growing (lazy-loaded results), 0 = skip.
        self._max_scrolls = max_scrolls
        self._scroll_pause_seconds = scroll_pause_seconds
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
        # Lazy sections hydrate on scroll - settle the panel first,
        # else hero image / carousel / owner-post selectors miss.
        _settle_panel(page)
        _scroll_photos_into_view(page)
        # -- Photos / owner-activity columns --------------------------------
        photos = _read_photo_columns(page)
        if (photos["cover_image_url"] == "N/A"
                and photos["by_owner_photos"] == "NO"):
            # Google hydrates the photos section inconsistently across runs
            # (live-verified) - one deep-scroll + re-read round for stability.
            _deep_scroll_panel(page, steps=4)
            _scroll_photos_into_view(page)
            photos = _read_photo_columns(page)
        data.update(photos)
        data["about"] = _first_text(page, ABOUT_SELECTORS) or "N/A"

        data.update(_extract_social_links(page))

        data["google_maps_url"] = clean_maps_url(page.url)
        apply_url_identity(data, page.url)

        # -- Owner post (G: has_recent_post) --------------------------------
        # The "From the owner" section virtualizes until scrolled deep; do it
        # AFTER the top-of-panel reads so nothing above gets unmounted first.
        _deep_scroll_panel(page)
        try:
            has_post = page.locator(FROM_OWNER_HEADING_SELECTOR).count() > 0
        except Exception:
            has_post = False
        data["has_recent_post"] = _yes_no(has_post)
        data["latest_post_date"] = (
            _first_text(page, FROM_OWNER_DATE_SELECTORS) or "N/A"
            if has_post else "N/A")

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

        # maps.max_scrolls: 0 = built-in safety bound (12 rounds).
        max_rounds = self._max_scrolls if self._max_scrolls > 0 else 12
        last_height = -1
        stalled = 0
        for _ in range(max_rounds):
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
            # maps.scroll_pause_seconds: when the feed height stops growing,
            # lazy-loaded cards may still be inflight — wait and retry a
            # bounded number of times before giving up.
            height = -1
            try:
                if feed is not None:
                    height = int(feed.evaluate("el => el.scrollHeight"))
            except Exception:
                height = -1
            if height == last_height:
                stalled += 1
                if self._scroll_pause_seconds > 0:
                    time.sleep(self._scroll_pause_seconds)
                if stalled >= 3:
                    break
            else:
                stalled = 0
            last_height = height

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
                "cover_image_url": "https://lh3.googleusercontent.com/demo/cover.jpg",
                "latest_image_upload": "11 days ago",
                "by_owner_photos": "YES",
                "has_recent_post": "YES",
                "latest_post_date": "3 days ago",
                "google_maps_url": f"https://www.google.com/maps/place/Sample/{i}",
                "place_id": f"0x1{i}2:0x3{i}4",
                "cid": f"0x1{i}2:0x3{i}4",
                "kgmid": f"/g/{1000 + i}",
                "source_query": query,
                "_reviews": [r for j, r in enumerate(_DEMO_REVIEWS) if j <= i],
            }

    def close(self) -> None:
        pass
