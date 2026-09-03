"""Regression tests for the pure Maps transformation boundary."""
from scraper.maps.transform import normalize_listing
from scraper.models import BusinessRecord, OUTPUT_COLUMNS

_GHOST_COLUMNS = {
    "popular_times", "competitors", "owner", "owner_posts", "can_claim",
    "is_spending_on_ads", "gas_prices", "featured_question",
    "reviews_per_rating", "timezone",
    # Removed from the export by operator decision (Section 4 schema change).
    "kgmid", "cid", "subcategory", "about", "mx_enabled", "smtp_enabled",
    "filtered_out_reason",
}

# Columns that MUST remain in the export (explicitly protected by the schema
# section 4 "must stay" list).
_PROTECTED_COLUMNS = {
    "emails", "email_count", "website_status", "website_failure_reason",
    "cms", "analytics", "tag_manager", "meta_pixel", "ga4", "gtm",
    "advertising", "booking_system", "chat_widget", "ssl", "tech_stack",
    "signal_pricing", "signal_financing", "signal_licensed_insured",
    "signal_established", "signal_portfolio", "signal_mobile_service",
    "signal_membership", "sentiment_score", "review_keywords", "lead_score",
    "pitch_hook", "top_review", "decision_maker_name", "decision_maker_title",
    "mx_status", "mx_reason", "smtp_status", "smtp_reason",
    "business_name", "phone", "website", "address", "city", "state",
    "postal_code", "country", "latitude", "longitude", "rating",
    "review_count", "google_maps_url", "place_id", "source_query",
    "source_keyword", "source_location", "business_hours", "business_status",
    "claimed_status", "category",
}


def test_schema_has_no_unproduced_ghost_columns():
    assert not _GHOST_COLUMNS.intersection(OUTPUT_COLUMNS)
    assert len(OUTPUT_COLUMNS) == 72


def test_protected_columns_still_present():
    miss = _PROTECTED_COLUMNS - set(OUTPUT_COLUMNS)
    assert not miss, f"protected columns dropped from export: {sorted(miss)}"


def test_business_record_enforces_output_contract():
    record = BusinessRecord({"business_name": "Acme", "timezone": "UTC", "owner": "Jane"})
    assert record.get("business_name") == "Acme"
    assert record.get("timezone") is None
    assert record.get("owner") is None


def test_normalize_listing_cleans_values_and_discards_unknown_fields():
    record = normalize_listing(
        {
            "business_name": "FranÃ§ais &amp; <b>Café</b>",
            "full_address": "10 Rue de Rivoli, 75001 Paris, France",
            "phone": "01 42 60 30 00",
            "timezone": "America/New_York",
            "popular_times": "garbage",
            "_reviews": ["Great &amp; friendly", "Great &amp; friendly"],
        },
        query="cafes in Paris",
        keyword="cafes",
        default_country="US",
    )
    assert record["business_name"] == "Français & Café"
    assert record["city"] == "Paris"
    assert record["country"] == "FR"
    assert record["phone"] == "+33142603000"
    assert record["phone_international"] == "+33142603000"
    assert "timezone" not in record
    assert "popular_times" not in record
    assert record["_reviews"] == ["Great & friendly"]
