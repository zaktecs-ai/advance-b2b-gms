"""Pure parsers for Maps data (rating, review counts, addresses, signals).

Everything here is a pure function — no browser, no network — so it is
unit-testable headlessly.
"""
from __future__ import annotations

import re

from ..utils.text import to_float, to_int

_REVIEWS_PAREN_RE = re.compile(r"\(([\d,]+)\)")
_REVIEWS_WORD_RE = re.compile(r"([\d,]+)\s*reviews?", re.I)
_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_STATE_ZIP_RE = re.compile(r"\b([A-Z]{2})\b\s*\d{5}")
_CITY_RE = re.compile(r",\s*([^,]+?),\s*[A-Z]{2}\s*\d{5}")


def parse_rating_reviews(header_text: str) -> tuple[float | None, int | None]:
    """Extract (rating, review_count) from a Maps header text block."""
    if not header_text:
        return (None, None)
    rating = None
    rm = re.search(r"\b(\d(?:\.\d)?)\b", header_text)
    if rm:
        rating = to_float(rm.group(1))
    count = None
    m = _REVIEWS_PAREN_RE.search(header_text)
    if m:
        count = to_int(m.group(1))
    else:
        m = _REVIEWS_WORD_RE.search(header_text)
        if m:
            count = to_int(m.group(1))
    return (rating, count)


def parse_address(address: str) -> dict:
    """Decompose a US-style address into city/state/zip/street."""
    out = {"street": "", "city": "", "state": "", "postal_code": "", "country": ""}
    if not address:
        return out
    addr = address.strip()
    m = _STATE_ZIP_RE.search(addr)
    if m:
        out["state"] = m.group(1)
        out["postal_code"] = m.group(0).split()[-1]
    else:
        zm = _ZIP_RE.search(addr)
        if zm:
            out["postal_code"] = zm.group(0)
    if out["state"]:
        cm = re.search(r",\s*([^,]+?),\s*" + re.escape(out["state"]), addr)
        if cm:
            out["city"] = cm.group(1).strip()
    elif out["postal_code"]:
        cm = _CITY_RE.search(addr)
        if cm:
            out["city"] = cm.group(1).strip()
    # Street = everything before the city (best effort).
    if out["city"]:
        idx = addr.find(out["city"])
        if idx > 0:
            out["street"] = addr[:idx].rstrip(", ").strip()
    return out


def parse_google_maps_url(url: str) -> dict:
    """Extract place_id / coordinates / query from a Maps URL."""
    out = {"query": None}
    m = re.search(r"/maps/search/([^/]+)", url or "")
    if m:
        out["query"] = m.group(1)
    m = re.search(r"/maps/place/([^/]+)", url or "")
    if m:
        out["place_name"] = m.group(1)
    m = re.search(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", url or "")
    if m:
        out["place_id"] = m.group(1)
    m = re.search(r"/maps/@(-?\d+\.\d+),(-?\d+\.\d+)", url or "")
    if m:
        out["lat"] = float(m.group(1))
        out["lng"] = float(m.group(2))
    m3 = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url or "")
    if m3 and "lat" not in out:
        out["lat"] = float(m3.group(1))
        out["lng"] = float(m3.group(2))
    # kgmid can appear as a hex "0x...:0x..." (CID) or a /g/ id.
    m4 = re.search(r"0x[0-9a-fA-F]+:0x[0-9a-fA-F]+", url or "")
    if m4:
        out["cid"] = m4.group(0)
    m5 = re.search(r"/g/([0-9a-zA-Z]+)", url or "")
    if m5:
        out["kgmid"] = m5.group(1)
    return out


def parse_popular_times(hours_text: str) -> str:
    """Normalize a popular-times / hours blob into a compact string."""
    if not hours_text:
        return "N/A"
    collapsed = " ".join(hours_text.split())
    return collapsed[:500] if collapsed else "N/A"


# -- Open/closed + keyword signals ----------------------------------------

_OPEN_MARKERS = re.compile(r"\bopen\b|\bopens\b", re.I)
_CLOSED_MARKERS = re.compile(r"\bclosed\b|\bcloses\b", re.I)


def classify_open_status(text: str) -> str:
    if not text:
        return "N/A"
    if _CLOSED_MARKERS.search(text) and not _OPEN_MARKERS.search(text):
        return "Closed"
    if _OPEN_MARKERS.search(text):
        return "Open"
    return "N/A"
