"""Pure parsers for Maps data (rating, review counts, addresses, signals).

Everything here is a pure function — no browser, no network — so it is
unit-testable headlessly.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from ..utils.normalize import normalize_text
from ..utils.text import to_int

_REVIEWS_PAREN_RE = re.compile(r"\(([\d,]+)\)")
_REVIEWS_WORD_RE = re.compile(r"([\d,]+)\s*reviews?", re.I)

# Deliberately conservative address knowledge.  These codes are used only when
# the text itself supplies a recognizable region; the parser never fabricates a
# city or country from a weak postal-code guess.
_US_REGIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
_OTHER_REGIONS = {
    # Canada
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
    # Australia / New Zealand
    "ACT", "NSW", "QLD", "SA", "TAS", "VIC", "WA", "NSN", "AUK", "WGN",
    # Brazil / South Africa / common international abbreviations
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SE", "SP", "TO",
    "EC", "GP", "WC", "KZN", "LP", "MP", "NW", "FS", "GT",
}
_REGION_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}
_COUNTRY_ALIASES = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "united states": "US", "united states of america": "US",
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "united kingdom": "GB",
    "england": "GB", "canada": "CA", "australia": "AU", "new zealand": "NZ",
    "germany": "DE", "deutschland": "DE", "france": "FR", "spain": "ES",
    "italy": "IT", "italia": "IT", "ireland": "IE", "netherlands": "NL",
    "the netherlands": "NL", "belgium": "BE", "switzerland": "CH", "austria": "AT",
    "poland": "PL", "portugal": "PT", "brazil": "BR", "brasil": "BR",
    "mexico": "MX", "india": "IN", "pakistan": "PK", "united arab emirates": "AE",
    "uae": "AE", "saudi arabia": "SA", "south africa": "ZA", "singapore": "SG",
    "malaysia": "MY", "philippines": "PH", "japan": "JP", "china": "CN",
    "south korea": "KR", "korea": "KR", "sweden": "SE", "norway": "NO",
    "denmark": "DK", "finland": "FI", "czechia": "CZ", "czech republic": "CZ",
    "greece": "GR", "turkey": "TR", "israel": "IL", "argentina": "AR",
    "chile": "CL", "colombia": "CO", "russia": "RU", "россия": "RU",
}
_POSTAL_PATTERNS = {
    "US": re.compile(r"\b\d{5}(?:-\d{4})?\b"),
    "CA": re.compile(r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d\b", re.I),
    "GB": re.compile(r"\b(?:GIR[ -]?0AA|[A-Z]{1,2}\d[A-Z\d]?[ -]?\d[A-Z]{2})\b", re.I),
    "NL": re.compile(r"\b\d{4}[ -]?[A-Z]{2}\b", re.I),
    "IE": re.compile(r"\b[A-Z]\d{2}[ -]?[A-Z0-9]{4}\b", re.I),
    "PT": re.compile(r"\b\d{4}-\d{3}\b"),
    "BR": re.compile(r"\b\d{5}-\d{3}\b"),
    # Argentine CPA is distinctive (A1234ABC); a bare 4-digit run is ambiguous
    # with AU/NZ postcodes and must not be used to *infer* AR in the fallback.
    "AR": re.compile(r"\b[A-Z]\d{4}[A-Z]{3}\b", re.I),
    "AU": re.compile(r"\b\d{4}\b"),
    "NZ": re.compile(r"\b\d{4}\b"),
    "JP": re.compile(r"\b\d{3}-\d{4}\b"),
}
# Numeric postal systems are only consulted after an explicit country is known;
# otherwise a house number would be mistaken for a postal code.
for _numeric_country in {
    "AT", "BE", "CH", "CZ", "DE", "DK", "ES", "FI", "FR", "GR", "HU",
    "IT", "NO", "PL", "RO", "RU", "SE", "TR", "ZA",
}:
    _POSTAL_PATTERNS[_numeric_country] = re.compile(r"\b\d{4,6}\b")
_STREET_SUFFIX_RE = re.compile(
    r"\b(?:street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr|"
    r"highway|hwy|way|parkway|pkwy|platz|straße|strasse|str|road|rue|rua)\b",
    re.I,
)


def parse_rating_reviews(header_text: str | None) -> tuple[float | None, int | None]:
    """Extract (rating, review_count) from a Maps header text block.

    Layered regexes: "4.8" for rating; "(365)" then "365 reviews" for count.
    """
    if not header_text:
        return (None, None)
    rating = None
    m = re.search(r"\b([1-5][.,]\d)\b", header_text)
    if m:
        try:
            rating = float(m.group(1).replace(",", "."))
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


def _country_from_text(parts: list[str]) -> str | None:
    """Return an ISO alpha-2 country only when the address says it explicitly."""
    for part in reversed(parts):
        key = re.sub(r"[.]+", ".", part.strip().lower())
        if key in _COUNTRY_ALIASES:
            return _COUNTRY_ALIASES[key]
    return None


def country_code(value: str | None) -> str | None:
    """Normalize a country name or alpha-2 code when it is unambiguous."""
    if not value:
        return None
    cleaned = normalize_text(value)
    if cleaned == "N/A":
        return None
    explicit = _country_from_text([cleaned])
    if explicit:
        return explicit
    code = cleaned.strip().upper()
    return code if len(code) == 2 and code.isalpha() and code not in {"NA", "XX", "ZZ"} else None


def _select_postal_match(address: str, pattern: re.Pattern) -> re.Match | None:
    """Choose the last plausible postal match and reject street house numbers."""
    matches = list(pattern.finditer(address))
    for match in reversed(matches):
        segment_start = address.rfind(",", 0, match.start()) + 1
        segment_end = address.find(",", match.end())
        if segment_end < 0:
            segment_end = len(address)
        segment = address[segment_start:segment_end]
        if _STREET_SUFFIX_RE.search(segment):
            continue
        if segment.strip():
            return match
    return None


def _postal_match(address: str, country: str | None) -> tuple[str | None, str | None]:
    """Return (postal code, country evidence) without over-parsing free text."""
    if country:
        pattern = _POSTAL_PATTERNS.get(country)
        match = _select_postal_match(address, pattern) if pattern else None
        if match:
            return match.group(0).upper(), country
        # Do not let an explicit country be contradicted by a postal pattern
        # from another country.
        return None, None
    # Only patterns that are distinctive without an explicit country are
    # allowed to infer one.  An unrecognized numeric format is intentionally
    # bypassed because house numbers and postal codes are indistinguishable.
    # A bare five-digit value is ambiguous internationally, so US is returned
    # as postal evidence only.  The caller infers US when a US state is also
    # present; otherwise it leaves country unresolved.
    for candidate_country in ("CA", "GB", "NL", "IE", "PT", "BR", "AR"):
        match = _select_postal_match(address, _POSTAL_PATTERNS[candidate_country])
        if match:
            return match.group(0).upper(), candidate_country
    us_match = _select_postal_match(address, _POSTAL_PATTERNS["US"])
    if us_match:
        return us_match.group(0).upper(), None
    return None, None


def _region_from_segment(segment: str, country: str | None = None) -> str | None:
    """Extract a recognizable administrative region from one address segment."""
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ.'-]+", segment)
    if not words:
        return None
    upper = " ".join(words).upper().replace("’", "'")
    if country in (None, "US"):
        for name, code in sorted(_REGION_NAMES.items(), key=lambda item: -len(item[0])):
            if re.search(rf"\b{re.escape(name)}\b", upper):
                return code

    if country == "US":
        allowed = _US_REGIONS
    elif country in {"CA", "AU", "NZ", "BR", "ZA"}:
        allowed = _OTHER_REGIONS
    elif country is None:
        allowed = _US_REGIONS | _OTHER_REGIONS
    else:
        allowed = set()

    # Require abbreviation-like tokens to be uppercase in the source.  This
    # prevents ordinary words such as `in` or `or` from becoming US states.
    for token in reversed(words):
        token_upper = token.rstrip(".")
        if token_upper == token_upper.upper() and token_upper in allowed:
            return token_upper
    return None


def _city_candidate(segment: str, postal: str | None, state: str | None,
                    country: str | None) -> str | None:
    """Clean one possible city segment and reject street/country fragments."""
    candidate = normalize_text(segment)
    if candidate == "N/A":
        return None
    if country and _country_from_text([candidate]) == country:
        return None
    if postal:
        candidate = re.sub(re.escape(postal), " ", candidate, flags=re.I)
    if state:
        candidate = re.sub(rf"\b{re.escape(state)}\b", " ", candidate, flags=re.I)
    candidate = normalize_text(candidate).strip(" ,;:-–—")
    if country and _country_from_text([candidate]) == country:
        return None
    if candidate == "N/A" or re.search(r"\d", candidate):
        return None
    if _STREET_SUFFIX_RE.search(candidate):
        return None
    return candidate


def _city_from_segments(segments: list[str], postal: str | None, state: str | None,
                        country: str | None = None) -> str | None:
    """Select a city only from a delimited segment near the region/postal."""
    if not segments:
        return None

    # Prefer a city carried in the same segment as the postal code, such as
    # `75001 Paris`, `London SW1A 2AA`, or `Sydney NSW 2000`.
    if postal:
        compact_postal = re.sub(r"\s+", "", postal).lower()
        for segment in reversed(segments):
            if compact_postal in re.sub(r"\s+", "", segment).lower():
                candidate = _city_candidate(segment, postal, state, country)
                if candidate:
                    return candidate

    # For `city, region, postal`, the segment before the region is the city.
    if state:
        for index, segment in enumerate(segments):
            if re.search(rf"\b{re.escape(state)}\b", segment, re.I) and index > 0:
                candidate = _city_candidate(segments[index - 1], None, state, country)
                if candidate:
                    return candidate

    # Last-resort delimited scan, deliberately refusing street-like segments
    # and requiring address-like evidence before labelling a free-text token as
    # a city.  This avoids turning `Company, France` into city=`Company`.
    for index in range(len(segments) - 1, -1, -1):
        if index == 0:
            continue
        previous = segments[index - 1]
        if not re.search(r"\d", previous) and not _STREET_SUFFIX_RE.search(previous):
            continue
        candidate = _city_candidate(segments[index], postal, state, country)
        if candidate:
            return candidate
    return None


def decompose_address(address: str) -> dict:
    """Conservatively split an address into city/state/postal_code/country.

    US-style addresses are supported for compatibility, while common
    international postal formats are recognized when their pattern is
    unambiguous.  Unknown formats return ``N/A`` rather than assigning pieces
    of a street or non-English address to the wrong field.
    """
    out = {"city": "N/A", "state": "N/A", "postal_code": "N/A", "country": "N/A"}
    cleaned = normalize_text(address)
    if cleaned == "N/A":
        return out

    # Widen separators to include newline, middle-dot, and pipe so non-comma
    # address layouts no longer collapse to all-N/A (F22).
    segments = [normalize_text(part) for part in re.split(r"\s*[,\n·|]\s*", cleaned)]
    segments = [part for part in segments if part != "N/A"]
    explicit_country = _country_from_text(segments)
    postal, postal_country = _postal_match(cleaned, explicit_country)
    country = explicit_country or postal_country

    region = None
    postal_index = None
    if postal:
        compact_postal = re.sub(r"\s+", "", postal).lower()
        for index, segment in enumerate(segments):
            if compact_postal in re.sub(r"\s+", "", segment).lower():
                postal_index = index
                break
    if postal_index is None:
        postal_index = len(segments) - 1
    # The region should be in the postal-bearing segment or its immediately
    # preceding administrative segment, never in an arbitrary street token
    # such as the French preposition `de`.
    for index in range(postal_index, max(-1, postal_index - 3), -1):
        segment = segments[index]
        if index != postal_index and re.search(r"\d|\b(?:street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr|rue|rua)\b", segment, re.I):
            continue
        candidate = _region_from_segment(segment, country)
        if candidate:
            region = candidate
            break

    city = _city_from_segments(segments, postal, region, country)
    if postal:
        out["postal_code"] = postal
    if region:
        out["state"] = region
    if city:
        out["city"] = city
    if country:
        out["country"] = country

    # Only infer a country from a highly distinctive postal system or a US
    # region plus a five-digit ZIP.  Do not infer a country from city names.
    if out["country"] == "N/A" and postal_country:
        out["country"] = postal_country
    elif out["country"] == "N/A" and postal and region in _US_REGIONS and _POSTAL_PATTERNS["US"].fullmatch(postal):
        out["country"] = "US"
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
    """Extract place_id / coordinates / cid / kgmid / query from a Maps URL.

    All regexes run against the percent-DECODED URL (``u``), because production
    URLs carry the kgmid token as ``!16s%2Fg%2F…``. ``cid`` is derived ONLY from
    the authoritative ``!1s`` place token (never ``!5s``/``!3s`` ad tokens), and
    coordinates come ONLY from the ``!3d…!4d…`` place pin — never the
    ``/maps/@…`` search-camera viewport.
    """
    out = {"query": None}
    if not url:
        return out
    u = unquote(str(url))
    m = re.search(r"/maps/search/([^/]+)", u)
    if m:
        out["query"] = m.group(1)
    m = re.search(r"/maps/place/([^/]+)", u)
    if m:
        out["place_name"] = m.group(1)
    m = re.search(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", u)
    if m:
        out["place_id"] = m.group(1)
        # cid is the same hex pair by definition (F04).
        out["cid"] = m.group(1)
    # Coordinates: only the true place pin (!3d…!4d…). NEVER the /maps/@
    # viewport center, which is the search camera position, not the business
    # location (F05).
    m3 = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", u)
    if m3:
        out["lat"] = float(m3.group(1))
        out["lng"] = float(m3.group(2))
    m5 = re.search(r"(?:/g/|%2Fg%2F)([0-9a-zA-Z_-]+)", u, re.I)
    if m5:
        out["kgmid"] = m5.group(1)
    return out


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
