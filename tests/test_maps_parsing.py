"""Maps parsing: rating/reviews, address decomposition, URL parsing."""
from scraper.maps.parsing import (
    classify_open_status,
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
    d = parse_google_maps_url("https://www.google.com/maps/@32.7767,-96.797,15z")
    assert d["lat"] == 32.7767 and d["lng"] == -96.797


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
