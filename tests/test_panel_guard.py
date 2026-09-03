"""Regression tests for the Maps collector's pure panel-scoping helpers.

Locks the F01 (facebook constant), F02 (one-row-shift), and F15 (phone
international dead branch) fixes at the pure-function level — no browser
required.
"""
from scraper.maps.collector import (
    _clean_plus_code,
    _names_compatible,
    _status_from_hours,
    clean_description,
    digits_to_intl,
    filter_panel_hrefs,
)


def test_names_compatible_guard():
    assert _names_compatible("village-plumbing", "Village Plumbing, Air & Electric")
    assert not _names_compatible("Cooper-Plumbing-Houston-Plumber",
                                 "Nick's Plumbing & Air Conditioning")
    assert _names_compatible("", "Anything At All")   # no slug -> cannot compare


def test_filter_panel_hrefs_drops_maps_nav_links():
    hrefs = [
        "https://www.facebook.com/championplumbers",
        "https://www.google.com/maps/place/Nick's+Plumbing/@29.8,-95.4,10z",
        "https://www.google.com/maps/dir/?api=1&destination=X",
        "https://instagram.com/nicksplumbingac",
    ]
    out = filter_panel_hrefs(hrefs)
    assert "https://www.facebook.com/championplumbers" in out
    assert "https://instagram.com/nicksplumbingac" in out
    assert not any(t in " ".join(out)
                   for t in ("maps/place", "maps/dir", "maps/search"))


def test_digits_to_intl():
    assert digits_to_intl("phone:tel:+15551234567") == "+15551234567"
    assert digits_to_intl("abc") == "N/A"
    assert digits_to_intl("+1 (555) 123-4567") == "+15551234567"


# --- G01: business_description must never carry UI chrome -------------------

def test_description_junk_rejected():
    # G1-E/G1-E2: "See photos" and rating-block text polluted most rows.
    for junk in ("See photos", "SEE PHOTOS", "4.9 (34)", "3.8 (10)",
                 "5.0 (1)", "Open 24 hours"):
        assert clean_description(junk) == "N/A"
    assert clean_description("") == "N/A"
    assert clean_description(None) == "N/A"


def test_description_real_text_kept():
    assert "plumbing" in clean_description("Sample plumbing service.").lower()


# --- G06: honest business_status fallback from hours ------------------------

def test_status_from_hours_open24():
    assert _status_from_hours(
        "Tuesday, Open 24 hours; Wednesday, Open 24 hours") == "Open"


def test_status_from_hours_normal_hours_is_honest_none():
    # Posted ranges do NOT imply open-right-now — stay honest (None).
    assert _status_from_hours("Tuesday: 8 AM to 5 PM") is None
    assert _status_from_hours("N/A") is None
    assert _status_from_hours("") is None
    assert _status_from_hours(None) is None


# --- G12: plus_code whitespace hygiene --------------------------------------

def test_plus_code_whitespace_normalized():
    assert (_clean_plus_code(" RJC2+2C  Northside, Houston, TX")
            == "RJC2+2C Northside, Houston, TX")
    assert _clean_plus_code("") == "N/A"
    assert _clean_plus_code(None) == "N/A"
