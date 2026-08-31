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


def parse_rating_reviews(header_text: str | None) -> tuple[float | None, int | None]:
    """Extract (rating, review_count) from a Maps header text block.

    Layered regexes: "4.8" for rating; "(365)" then "365 reviews" for count.
    """
    if not header_text:
        return (None, None)
    rating = None
    m = re.search(r"\b([1-5]\.\d)\b", header_text)
    if m:
        try:
            rating = float(m.group(1))
        except ValueError:
            rating = None
    count = None
    m = _REVIEWS_PAREN_RE.search(header_text)
    if m:
        count = to_int(m.group(1))
    else:
        m = _REVIEWS_WORD_RE.search(header_text)
        if m:
            count = to_int(m.group(1))
    return (rating, count)


def decompose_address(address: str) -> dict:
    """Split a Maps address string into city / state / postal_code / country.

    Returns keys city, state, postal_code, country (each 'N/A' when unresolved).
    """
    out = {"city": "N/A", "state": "N/A", "postal_code": "N/A", "country": "N/A"}
    if not address:
        return out
    addr = address.strip()

    zm = _ZIP_RE.search(addr)
    if zm:
        out["postal_code"] = zm.group(0).split("-")[0]

    sm = _STATE_ZIP_RE.search(addr)
    if sm:
        out["state"] = sm.group(1)

    cm = re.search(r",\s*([^,]+?),\s*([A-Z]{2})\s*\d{5}", addr)
    if cm:
        out["city"] = cm.group(1).strip()
    elif out["postal_code"] and out["state"]:
        cm2 = _CITY_RE.search(addr)
        if cm2:
            out["city"] = cm2.group(1).strip()

    # Country: trailing token after city/state/zip that is a country name.
    if re.search(r"\bUnited States\b", addr, re.I):
        out["country"] = "US"
    elif re.search(r"\bUnited Kingdom\b", addr, re.I):
        out["country"] = "UK"
    elif re.search(r"\bCanada\b", addr, re.I):
        out["country"] = "CA"
    return out


# Backward-compatible alias used by older tests.
def parse_address(address: str) -> dict:
    """Decompose a US-style address into city/state/zip/street."""
    out = {"street": "", "city": "", "state": "", "postal_code": "", "country": ""}
    dec = decompose_address(address)
    out["city"] = dec["city"] if dec["city"] != "N/A" else ""
    out["state"] = dec["state"] if dec["state"] != "N/A" else ""
    out["postal_code"] = dec["postal_code"] if dec["postal_code"] != "N/A" else ""
    out["country"] = dec["country"] if dec["country"] != "N/A" else ""
    if out["city"] and address:
        idx = address.find(out["city"])
        if idx > 0:
            out["street"] = address[:idx].rstrip(", ").strip()
    return out


def parse_google_maps_url(url: str) -> dict:
    """Extract place_id / coordinates / cid / kgmid / query from a Maps URL."""
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
