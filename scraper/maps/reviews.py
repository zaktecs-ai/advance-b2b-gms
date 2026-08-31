"""Review extraction: RPC-first (``listugcposts``) with DOM-scroll fallback.

For each business, capture the latest N review texts. The primary path fetches
the Google Maps reviews RPC endpoint using the browser session (cookies), which
returns structured review data; a DOM-scroll fallback reads the rendered review
feed when the RPC path is unavailable. Extraction is toggleable.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_RPC_PREFIX = ")]}'"
_PB_PLACE_TEMPLATE = "!6m4!4m1!1e1!4m1!1e3!2m2!1i{page_size}!2s{token}!5m2!1s{rid}!7e81"
_NEXT_TOKEN_RE = re.compile(r'"([^"]+)"')


def _generate_request_id(length: int = 20) -> str:
    import random
    import string
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


def parse_review_rpc_response(text: str) -> tuple[list[str], str]:
    """Parse a raw listugcposts response into (review_texts, next_page_token)."""
    data = text
    if data.startswith(_RPC_PREFIX):
        data = data[len(_RPC_PREFIX):]
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return [], ""
    reviews: list[str] = []
    next_token = ""
    try:
        # Structure (varies): parsed[1] is an array of review entries, parsed[2]
        # is the next-page token. Guard every step for resilience.
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


def _extract_review_text(entry: Any) -> str:
    """Walk a review entry (list or nested lists) to find the review body."""
    chunks: list[str] = []
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
        # Heuristic: strings that look like sentences.
        if any(c.isalpha() for c in entry) and len(entry.split()) >= 2:
            chunks.append(entry)
    # Prefer the longest string found at this node as the body.
    if chunks:
        return max(chunks, key=len)
    return ""


def parse_review_texts_dom(html: str) -> list[str]:
    """Fallback: pull review snippets from rendered review-feed HTML."""
    if not html:
        return []
    # Review bodies commonly render inside elements with a class containing
    # 'review' or a review text container; use a tolerant regex over visible text.
    soup_text = _strip_tags(html)
    # Split into sentences and keep those that look like review content.
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"[A-Za-z0-9][^.!?]{20,500}[.!?]", soup_text):
        s = m.group(0).strip()
        if s and s not in seen and len(s) >= 10:
            seen.add(s)
            out.append(s)
    return out


def _strip_tags(html: str) -> str:
    """Strip script/style blocks and all tags, leaving visible text."""
    txt = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(txt.split())


def filter_reviews(reviews: list[str], min_len: int = 0, max_len: int = 1000) -> list[str]:
    """Drop out-of-length and duplicate reviews."""
    out: list[str] = []
    seen: set[str] = set()
    for r in reviews:
        r = r.strip()
        if not r or r in seen:
            continue
        seen.add(r)
        if min_len and len(r) < min_len:
            continue
        if max_len:
            r = r[:max_len]
        out.append(r)
    return out
