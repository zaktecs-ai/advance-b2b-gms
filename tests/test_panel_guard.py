"""Regression tests for the Maps collector's pure panel-scoping helpers.

Locks the F01 (facebook constant), F02 (one-row-shift), and F15 (phone
international dead branch) fixes at the pure-function level — no browser
required.
"""
from scraper.maps.collector import (
    _names_compatible,
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
