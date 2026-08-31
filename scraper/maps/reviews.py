"""Review extraction: RPC-first (listugcposts) + panel-feed fallback.

For each business, capture the latest N review texts. The RPC path fetches the
Google Maps reviews endpoint using the browser session (cookies) and returns
structured data; a live-panel scroll fallback reads the rendered review feed
on the already-open detail panel when RPC is unavailable.

Extraction is toggleable via ``reviews.enabled``.
"""
from __future__ import annotations

import json
import logging
import random
import re
import string
import time

log = logging.getLogger(__name__)

_RPC_PREFIX = ")]}'"
_PB_PLACE_TEMPLATE = "!6m4!4m1!1e1!4m1!1e3!2m2!1i{page_size}!2s{token}!5m2!1s{rid}!7e81"


def _generate_request_id(length: int = 20) -> str:
    return "".join(random.choices(string.digits + string.ascii_letters, k=length))


def build_review_rpc_url(place_id: str, page_size: int = 20, token: str = "") -> str:
    """Build the ``google.com/maps/rpc/listugcposts`` URL for a place_id."""
    rid = _generate_request_id()
    pb = (
        f"!1m6!1s{place_id}"
        f"{_PB_PLACE_TEMPLATE.format(page_size=page_size, token=token, rid=rid)}"
        "!8m9!2b1!3b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!11m0!13m1!1e1"
    )
    return f"https://www.google.com/maps/rpc/listugcposts?authuser=0&hl=en&pb={pb}"


def parse_review_rpc_response(text: str) -> tuple[list, str]:
    """Parse a raw listugcposts response into (review_texts, next_page_token)."""
    data = text
    if data.startswith(_RPC_PREFIX):
        data = data[len(_RPC_PREFIX):]
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return [], ""
    reviews: list = []
    next_token = ""
    try:
        if isinstance(parsed, list) and len(parsed) > 1:
            entries = parsed[1]
            if isinstance(entries, list):
                for entry in entries:
                    text = _extract_review_text(entry)
                    if text:
                        reviews.append(text)
            if len(parsed) > 2:
                tok = parsed[2]
                if isinstance(tok, str):
                    next_token = tok
                elif isinstance(tok, list) and tok:
                    next_token = str(tok[0])
    except Exception as e:  # noqa: BLE001
        log.debug("review RPC parse error: %s", e)
    return reviews, next_token


def _extract_review_text(entry) -> str:
    """Walk a review entry (list or nested lists) to find the review body."""
    chunks: list = []
    if isinstance(entry, list):
        for item in entry:
            found = _extract_review_text(item)
            if found:
                chunks.append(found)
    elif isinstance(entry, dict):
        for key in ("text", "comment", "review", "snippet"):
            if isinstance(entry.get(key), str) and len(entry[key]) > 3:
                chunks.append(entry[key])
    elif isinstance(entry, str) and len(entry) > 3:
        if any(c.isalpha() for c in entry) and len(entry.split()) >= 2:
            chunks.append(entry)
    if chunks:
        return max(chunks, key=len)
    return ""


def parse_review_texts_dom(html: str) -> list:
    """Fallback: pull review snippets from rendered review-feed HTML."""
    if not html:
        return []
    soup_text = _strip_tags(html)
    out: list = []
    seen: set = set()
    for m in re.finditer(r"[A-Za-z0-9][^.!?]{20,500}[.!?]", soup_text):
        s = m.group(0).strip()
        if s and s not in seen and len(s) >= 10:
            seen.add(s)
            out.append(s)
    return out


def _strip_tags(html: str) -> str:
    txt = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(txt.split())


# -- Live-panel fallback ----------------------------------------------------

# Review body text renders inside rolling cards (jftiEf) whose text lives in a
# span. The "Reviews" tab button has an aria-label like "381 reviews".
_REVIEW_TAB_SELECTORS = [
    'button[aria-label*="reviews"]',
    'button[jsaction*="review"]',
    'button:has-text("Reviews")',
    'div[role="tab"]:has-text("Reviews")',
]
_REVIEW_TEXT_SELECTORS = [
    'div[class*="jftiEf"] span[class*="wiI7pd"]',
    'div[class*="jftiEf"]',
    'span[class*="wiI7pd"]',
]


def open_reviews_tab(page) -> bool:
    """Click the Reviews tab so the review feed becomes the scroll target."""
    for sel in _REVIEW_TAB_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=4000)
                time.sleep(1.5)
                return True
        except Exception:
            continue
    return False


def extract_reviews_from_panel(page, max_reviews: int = 5,
                               open_tab: bool = True) -> list:
    """Open the reviews feed and pull up to ``max_reviews`` review texts.

    ``open_tab`` first clicks the Reviews tab (so the feed becomes scrollable),
    then repeatedly scrolls and harvests unique review bodies.
    """
    texts: list = []
    seen: set = set()
    if open_tab:
        try:
            open_reviews_tab(page)
        except Exception:
            pass

    # Several scrolling passes; the feed lives inside the detail panel.
    scroll_attempts = max(3, max_reviews)
    for _ in range(scroll_attempts):
        try:
            page.mouse.wheel(0, 1800)
        except Exception:
            pass
        time.sleep(0.6)
        for sel in _REVIEW_TEXT_SELECTORS:
            try:
                locs = page.locator(sel)
                n = locs.count()
                for i in range(n):
                    txt = locs.nth(i).inner_text(timeout=1500).strip()
                    if txt and len(txt) > 8 and txt not in seen:
                        seen.add(txt)
                        texts.append(txt)
                        if len(texts) >= max_reviews:
                            return texts[:max_reviews]
            except Exception:
                continue
    return texts[:max_reviews]


def filter_reviews(reviews: list, min_len: int = 0, max_len: int = 1000) -> list:
    """Drop out-of-length and duplicate reviews."""
    out: list = []
    seen: set = set()
    for r in reviews:
        r = (r or "").strip()
        if not r or r in seen:
            continue
        seen.add(r)
        if min_len and len(r) < min_len:
            continue
        if max_len:
            r = r[:max_len]
        out.append(r)
    return out
