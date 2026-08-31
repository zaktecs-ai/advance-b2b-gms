"""Data model: the master output schema and internal record representation.

Every column in ``OUTPUT_COLUMNS`` is the single source of truth for the CSV
header, the XLSX sheet, and validation. Unavailable data is rendered with the
configured missing-value (default ``"N/A"``) — never guessed or fabricated.

The schema contains only fields with a defined producer so every exported
column has a clear data-flow owner and missing values are meaningful.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Canonical output column order — THE single source of truth for exports.
OUTPUT_COLUMNS: list[str] = [
    # --- Identity (kgmid is the authoritative, never-null key) ---
    "kgmid", "place_id", "cid", "business_name", "category", "subcategory",
    "phone", "phone_international", "website", "address", "full_address",
    "city", "state", "postal_code", "country", "latitude", "longitude",
    "plus_code", "google_maps_url",
    # --- Maps intelligence ---
    "rating", "review_count", "claimed_status", "business_status",
    "business_hours", "business_description", "about",
    # --- Provenance ---
    "source_query", "source_location", "source_keyword",
    # --- Website intelligence ---
    "website_status", "website_failure_reason",
    "emails", "email_count",
    "facebook", "instagram", "linkedin", "youtube", "twitter_x",
    "tiktok", "pinterest", "github", "snapchat",
    "tech_stack",
    # Technologies (individual)
    "cms", "analytics", "tag_manager", "meta_pixel", "ga4", "gtm",
    "advertising", "booking_system", "chat_widget", "ssl",
    # Signals
    "signal_pricing", "signal_financing", "signal_licensed_insured",
    "signal_established", "signal_portfolio", "signal_mobile_service",
    "signal_membership",
    # --- Review-quality lead scoring (the free add-on) ---
    "sentiment_score", "review_keywords", "lead_score", "pitch_hook", "top_review",
    # --- Decision-maker enrichment ---
    "decision_maker_name", "decision_maker_title",
    # --- Verification ---
    "mx_enabled", "mx_status", "mx_reason",
    "smtp_enabled", "smtp_status", "smtp_reason",
    # --- Housekeeping ---
    "filtered_out_reason", "record_id",
]


@dataclass
class BusinessRecord:
    """Internal record before commit. ``data`` holds all OUTPUT_COLUMNS keys."""

    data: dict[str, Any] = field(default_factory=dict)
    # Evidence map for signals (name -> evidence string).
    evidence: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Keep the dataclass aligned with the export contract; collector-only
        # metadata is carried separately by the pipeline when needed.
        self.data = {
            key: value for key, value in self.data.items() if key in OUTPUT_COLUMNS
        }
        self.data.setdefault("record_id", "")

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if key not in OUTPUT_COLUMNS:
            raise KeyError(f"unsupported output column: {key}")
        self.data[key] = value

    def as_row(self, missing: str = "N/A") -> list[str]:
        """Return values in canonical column order."""
        return [_to_cell(self.data.get(col), missing) for col in OUTPUT_COLUMNS]


def _to_cell(value: Any, missing: str) -> str:
    if value is None or value == "":
        return missing
    if isinstance(value, float):
        if not math.isfinite(value):
            return missing
        return repr(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------------------------------------------------------------------------
# Website status classification — "blocked" must never become "dead".
# ---------------------------------------------------------------------------

class WebsiteStatus:
    LIVE = "LIVE"
    DEAD = "DEAD"


class FailureReason:
    HTTP_BLOCKED = "HTTP_BLOCKED"
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    JS_REQUIRED = "JS_REQUIRED"
    DNS_FAILURE = "DNS_FAILURE"
    TIMEOUT = "TIMEOUT"
    TLS_ERROR = "TLS_ERROR"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


# Temporary / scraper-side reasons that must never imply a dead site.
_TRANSIENT = {
    FailureReason.HTTP_BLOCKED, FailureReason.CAPTCHA_DETECTED,
    FailureReason.JS_REQUIRED, FailureReason.TIMEOUT, FailureReason.UNKNOWN,
}


def is_dead_signal(reason: str) -> bool:
    """True only when the reason strongly indicates the site is gone."""
    return reason in {
        FailureReason.DNS_FAILURE, FailureReason.CONNECTION_REFUSED,
        FailureReason.NOT_FOUND, FailureReason.TLS_ERROR,
    }


def resolve_website_status(reason: str) -> str:
    """Map a failure reason to a website_status without conflating transient
    failures (blocked, captcha, js-required) with a genuinely dead site."""
    if reason in _TRANSIENT:
        return WebsiteStatus.LIVE
    if is_dead_signal(reason):
        return WebsiteStatus.DEAD
    return WebsiteStatus.LIVE


# SMTP statuses — explicit; uncertainty is never collapsed into false certainty.
SMTP_STATUSES = {
    "Verified", "Invalid", "Catch-All", "Connection Failed",
    "Blocked", "Inconclusive", "Timeout", "Not Checked",
}
