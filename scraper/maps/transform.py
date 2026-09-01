"""Pure transformations for raw Google Maps listing data.

The collector owns browser interaction only.  This module owns the deterministic
normalization of extracted values, address decomposition, and removal of any
non-exported fields before a record reaches the pipeline.
"""
from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import unquote

from ..models import OUTPUT_COLUMNS
from ..utils.normalize import normalize_phone, normalize_text, normalize_url
from ..utils.text import to_float, to_int
from .parsing import (
    country_code,
    decompose_address,
    parse_google_maps_url,
    parse_rating_reviews,
)

_TEXT_COLUMNS = {
    "business_name", "category", "address", "full_address",
    "city", "state", "postal_code", "country", "plus_code", "business_status",
    "business_hours", "claimed_status", "business_description",
    "source_query", "source_location", "source_keyword", "website_status",
    "website_failure_reason", "emails", "email_count", "tech_stack", "cms",
    "analytics", "tag_manager", "meta_pixel", "ga4", "gtm", "advertising",
    "booking_system", "chat_widget", "ssl", "signal_pricing", "signal_financing",
    "signal_licensed_insured", "signal_established", "signal_portfolio",
    "signal_mobile_service", "signal_membership", "sentiment_score",
    "review_keywords", "lead_score", "pitch_hook", "top_review",
    "decision_maker_name", "decision_maker_title", "mx_status",
    "mx_reason", "smtp_status", "smtp_reason",
    "record_id", "place_id",
}
_URL_COLUMNS = {
    "website", "google_maps_url", "facebook", "instagram", "linkedin", "youtube",
    "twitter_x", "tiktok", "pinterest", "github", "snapchat",
}
_PHONE_COLUMNS = {"phone", "phone_international"}
_INT_COLUMNS = {"review_count", "email_count", "lead_score"}
_FLOAT_COLUMNS = {"latitude", "longitude", "rating", "sentiment_score"}
_ADDRESS_COLUMNS = {"city", "state", "postal_code", "country"}


def extract_rating_reviews(text: str | None) -> tuple[Any, Any]:
    """Return export-safe rating/review values from a Maps text fragment."""
    rating, review_count = parse_rating_reviews(text)
    return (
        rating if rating is not None else "N/A",
        review_count if review_count is not None else "N/A",
    )


def extract_url_identity(url: str | None) -> dict[str, Any]:
    """Extract identifiers and coordinates from a Google Maps URL."""
    parsed = parse_google_maps_url(url or "")
    return {
        key: parsed[key]
        for key in ("lat", "lng", "place_id", "cid", "kgmid")
        if parsed.get(key) is not None
    }


def fallback_business_name(url: str | None) -> str:
    """Decode a place slug for the rare case the detail panel has no name."""
    parsed = parse_google_maps_url(url or "")
    name = unquote(str(parsed.get("place_name") or "")).replace("+", " ")
    cleaned = normalize_text(name)
    return cleaned if cleaned != "N/A" else "N/A"


def apply_url_identity(data: dict[str, Any], url: str | None) -> dict[str, Any]:
    """Merge URL-derived identifiers into an extracted listing payload."""
    for key, value in extract_url_identity(url).items():
        output_key = {"lat": "latitude", "lng": "longitude"}.get(key, key)
        if data.get(output_key) in (None, "", "N/A"):
            data[output_key] = value
    if url and data.get("google_maps_url") in (None, "", "N/A"):
        data["google_maps_url"] = url
    return data


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().upper() in {"", "N/A", "NA", "NONE", "NULL"})


def _normalize_number(key: str, value: Any) -> Any:
    if _is_missing(value):
        return "N/A"
    if key in _INT_COLUMNS:
        parsed = to_int(value, default=-1)
        return parsed if parsed >= 0 else "N/A"
    parsed = to_float(value)
    if parsed is None:
        return "N/A"
    if key == "rating" and not 0.0 <= parsed <= 5.0:
        return "N/A"
    if key == "latitude" and not -90.0 <= parsed <= 90.0:
        return "N/A"
    if key == "longitude" and not -180.0 <= parsed <= 180.0:
        return "N/A"
    if key == "sentiment_score":
        return max(-1.0, min(1.0, parsed))
    return parsed


def _normalize_value(key: str, value: Any, default_country: str) -> Any:
    if _is_missing(value):
        return "N/A"
    if key in _PHONE_COLUMNS:
        return normalize_phone(value, default_country=default_country)
    if key in _URL_COLUMNS:
        return normalize_url(str(value))
    if key in _INT_COLUMNS or key in _FLOAT_COLUMNS:
        return _normalize_number(key, value)
    if key in _TEXT_COLUMNS or key in _ADDRESS_COLUMNS:
        return normalize_text(value)
    return value


def _country_hint(value: Any, parsed_address: Mapping[str, Any], default_country: str) -> str:
    """Choose an explicit address country, then the configured default region."""
    explicit = country_code(value if isinstance(value, str) else None)
    if explicit and explicit not in {"NA", "XX"}:
        return explicit
    parsed = country_code(parsed_address.get("country"))
    if parsed:
        return parsed
    configured = country_code(default_country)
    return configured or "US"


def normalize_listing(raw: Mapping[str, Any] | None, *, query: str = "",
                      keyword: str = "", default_country: str = "US") -> dict[str, Any]:
    """Normalize a collector payload into the canonical output contract.

    Only ``OUTPUT_COLUMNS`` plus the two internal review/progress keys are
    returned.  Missing values stay explicit as ``N/A``; no field is populated by
    an invented fallback.  National phone numbers use an address-derived
    country when it is explicit, otherwise ``default_country``.
    """
    raw = raw or {}
    raw_address = raw.get("full_address") if not _is_missing(raw.get("full_address")) else raw.get("address")
    parsed_address = decompose_address(raw_address if not _is_missing(raw_address) else "")
    phone_country = _country_hint(raw.get("country"), parsed_address, default_country)
    record: dict[str, Any] = {
        column: _normalize_value(column, raw.get(column), phone_country)
        for column in OUTPUT_COLUMNS
    }
    # kgmid/cid are internal identity signals (dedup top key) though no longer
    # exported. Carry them through so ``resolve_identity`` sees them.
    for _internal in ("kgmid", "cid"):
        if not _is_missing(raw.get(_internal)):
            record[_internal] = normalize_text(raw[_internal])

    if _is_missing(record.get("address")) and not _is_missing(record.get("full_address")):
        record["address"] = record["full_address"]
    elif _is_missing(record.get("full_address")) and not _is_missing(record.get("address")):
        record["full_address"] = record["address"]

    for field in _ADDRESS_COLUMNS:
        if _is_missing(record.get(field)) and not _is_missing(parsed_address.get(field)):
            record[field] = parsed_address[field]
    raw_country = country_code(raw.get("country") if isinstance(raw.get("country"), str) else None)
    parsed_country = country_code(parsed_address.get("country"))
    if raw_country:
        record["country"] = raw_country
    elif parsed_country:
        record["country"] = parsed_country

    if _is_missing(record.get("phone_international")) and record.get("phone") not in (None, "N/A"):
        record["phone_international"] = record["phone"]

    record["source_query"] = normalize_text(query or raw.get("source_query"))
    record["source_keyword"] = normalize_text(keyword or raw.get("source_keyword"))
    if _is_missing(raw.get("source_location")):
        record["source_location"] = "N/A"
    else:
        record["source_location"] = normalize_text(raw.get("source_location"))

    reviews = []
    for review in raw.get("_reviews") or []:
        cleaned = normalize_text(review)
        if cleaned != "N/A" and cleaned not in reviews:
            reviews.append(cleaned)
    if reviews:
        record["_reviews"] = reviews

    for key in ("_position", "_total"):
        if key in raw:
            record[key] = raw[key]
    return record
