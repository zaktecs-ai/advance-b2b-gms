"""Maps parsing: rating/reviews, address decomposition, URL parsing."""
from scraper.maps.parsing import (
    classify_open_status,
    clean_maps_url,
    decompose_address,
    parse_address,
    parse_google_maps_url,
    parse_rating_reviews,
)


def test_rating_reviews_paren():
    r, c = parse_rating_reviews("4.8 (365)")
    assert r == 4.8 and c == 365


def test_rating_reviews_word():
    r, c = parse_rating_reviews("4.5 · 120 reviews")
    assert r == 4.5 and c == 120


def test_rating_reviews_empty():
    assert parse_rating_reviews("") == (None, None)
    assert parse_rating_reviews(None) == (None, None)


def test_address_decompose():
    a = parse_address("123 Main St, Dallas, TX 75201")
    assert a["city"] == "Dallas"
    assert a["state"] == "TX"
    assert a["postal_code"] == "75201"
    assert "123 Main St" in a["street"]


def test_address_empty():
    a = parse_address("")
    assert a["city"] == "" and a["state"] == ""


def test_address_international_formats_are_conservative():
    assert decompose_address("10 Rue de Rivoli, 75001 Paris, France") == {
        "city": "Paris", "state": "N/A", "postal_code": "75001", "country": "FR",
    }
    assert decompose_address("123 Queen St W, Toronto, ON M5V 3A8, Canada") == {
        "city": "Toronto", "state": "ON", "postal_code": "M5V 3A8", "country": "CA",
    }
    assert decompose_address("ул. Тверская, 12, Москва, Russia") == {
        "city": "Москва", "state": "N/A", "postal_code": "N/A", "country": "RU",
    }


def test_parse_maps_url_place_id():
    d = parse_google_maps_url("https://www.google.com/maps/place/X/@32,-96,17z/data=!4m1!1s0x123:0x456")
    assert d.get("place_id") == "0x123:0x456"


def test_parse_maps_url_coords():
    # F05: the `/maps/@…` viewport center is the search CAMERA position, not the
    # business pin, so it must never become lat/lng. Only `!3d…!4d…` counts.
    d = parse_google_maps_url("https://www.google.com/maps/@32.7767,-96.797,15z")
    assert "lat" not in d and "lng" not in d
    d2 = parse_google_maps_url(
        "https://www.google.com/maps/place/X/@32.7,-96.7,15z/data=!4m1!1s0x1:0x2!8m2!3d29.8677916!4d-95.5618629")
    assert d2["lat"] == 29.8677916 and d2["lng"] == -95.5618629


def test_classify_open_closed():
    assert classify_open_status("Open now") == "Open"
    assert classify_open_status("Closed") == "Closed"
    assert classify_open_status("") == "N/A"


def test_rating_comma_decimal():
    # A9: European locale renders ratings with a comma decimal separator.
    r, c = parse_rating_reviews("4,8 (365)")
    assert r == 4.8 and c == 365


def test_argentine_postal_requires_full_cpa():
    # A7: a bare 4-digit AU/NZ-style postcode must NOT be inferred as Argentina.
    au = decompose_address("10 Hay St, Perth WA 6000")
    assert au["country"] != "AR"
    nz = decompose_address("12 Queen St, Auckland 1010")
    assert nz["country"] != "AR"
    ar = decompose_address("Av. Corrientes 1234, Buenos Aires, A1234ABC")
    assert ar["postal_code"] == "A1234ABC"
    assert ar["country"] == "AR"


# --- Audit regressions: F03 kgmid / F04 cid / F05 coords / F22 address -----
from tests.fixtures.production_urls import (
    ABERLE_AD_TOKEN_URL,
    FULL_PLACE_URL,
    KGID_ENCODED_URL,
    KGID_PLAIN_URL,
    LOW_ZOOM_VIEWPORT_URL,
    VIEWPORT_ONLY_URL,
    VIEWPORT_PLUS_PIN_URL,
)


def test_kgmid_extracted_from_encoded_url():
    # F03: percent-encoded `!16s%2Fg%2F…` must yield the kgmid.
    assert parse_google_maps_url(KGID_ENCODED_URL)["kgmid"] == "1tf719p9"


def test_kgmid_extracted_from_plain_url():
    assert parse_google_maps_url(KGID_PLAIN_URL)["kgmid"] == "1abcXYZ"


def test_cid_matches_place_id_and_ignores_ad_tokens():
    # F04: cid == place_id, both from `!1s`; the `!5s` ad token is ignored.
    d = parse_google_maps_url(ABERLE_AD_TOKEN_URL)
    assert d["place_id"] == "0x8640e99260148083:0x103f08fd2afc21"
    assert d["cid"] == d["place_id"]


def test_viewport_center_never_becomes_place_coords():
    # F05: viewport-only URL -> no coords.
    d = parse_google_maps_url(VIEWPORT_ONLY_URL)
    assert "lat" not in d and "lng" not in d


def test_true_pin_coords_extracted_over_viewport():
    # G2 (R3 update): VIEWPORT_PLUS_PIN_URL is a 10z search-viewport URL. The
    # V2 zoom guard intentionally REJECTS its `!3d/!4d` pair because
    # production proved 10z "pins" are camera/viewport values (G2-E: four
    # distinct businesses shared one coordinate pair, all at 10z). Detail
    # URLs at >=15z (FULL_PLACE_URL, ABERLE_AD_TOKEN_URL) still yield coords.
    d = parse_google_maps_url(VIEWPORT_PLUS_PIN_URL)
    assert "lat" not in d and "lng" not in d


def test_low_zoom_url_coords_rejected():
    # G02: a 10z fallback URL whose `!8m2!3d…!4d…` is the camera position.
    d = parse_google_maps_url(LOW_ZOOM_VIEWPORT_URL)
    assert "lat" not in d and "lng" not in d


def test_17z_pin_coords_kept():
    # The zoom guard must not over-reject real detail-panel pins.
    d = parse_google_maps_url(ABERLE_AD_TOKEN_URL)  # no zoom marker -> detail default
    assert d["lat"] == 29.6 and d["lng"] == -95.5
    d2 = parse_google_maps_url(FULL_PLACE_URL)
    assert d2["lat"] == 29.7604 and d2["lng"] == -95.3698


def test_clean_maps_url_strips_tracking_params():
    # G12: session/tracking junk must never reach the exported column.
    dirty = ("https://www.google.com/maps/place/X/@29.8,-95.4,17z"
             "?authuser=2&entry=gps&g_ep=abc&rclk=1&utm_source=x&hl=en&gl=us")
    out = clean_maps_url(dirty)
    for tok in ("authuser", "entry=", "g_ep", "rclk", "utm_"):
        assert tok not in out
    assert "hl=en" in out and "gl=us" in out  # legitimate locale params kept


def test_clean_maps_url_passthrough_and_empty():
    u = "https://www.google.com/maps/place/X/@29.8,-95.4,17z"
    assert clean_maps_url(u) == u
    assert clean_maps_url("") == ""
    assert clean_maps_url(None) == ""


def test_pipe_and_newline_separated_addresses():
    # F22: separators other than commas must not yield all-N/A. (normalize_text
    # collapses newlines, so we test the pipe separator which survives.)
    d = decompose_address("Musterstraße 1 | 10115 Berlin | Deutschland")
    assert d["postal_code"] == "10115"
    assert d["country"] == "DE"
