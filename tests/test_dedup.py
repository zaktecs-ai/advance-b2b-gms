"""Dedup identity ladder + multi-location non-merging + rollback tests."""
from scraper.dedup.dedup import IdentityResolver, resolve_identity


def _rec(**kw):
    base = {"business_name": "A", "city": "Dallas", "website": "https://a.com"}
    base.update(kw)
    return base


def test_kgmid_is_top_key():
    r1 = _rec(kgmid="/g/111", place_id="", phone="+15550001")
    r2 = _rec(kgmid="/g/111", place_id="", phone="+15559999")
    s1 = resolve_identity(r1)
    s2 = resolve_identity(r2)
    assert s1["key_type"] == "kgmid"
    assert s1["identity_key"] == s2["identity_key"]


def test_place_id_second():
    r = _rec(place_id="0x123:0x456")
    s = resolve_identity(r)
    assert s["key_type"] == "place_id"


def test_place_id_n_a_is_ignored():
    r = _rec(place_id="N/A", website="https://a.com", city="Dallas")
    s = resolve_identity(r)
    assert s["place_id"] is None
    assert s["key_type"] == "domain+city"


def test_domain_city_fallback():
    r = _rec(website="https://acme.com", city="Dallas")
    s = resolve_identity(r)
    assert s["key_type"] == "domain+city"


def test_phone_fallback():
    r = _rec(website="", city="", phone="+15550001234")
    s = resolve_identity(r)
    assert s["key_type"] == "phone"


def test_name_city_fallback():
    r = _rec(website="", city="Dallas", phone="N/A", business_name="Joe's Plumb")
    s = resolve_identity(r)
    assert s["key_type"] == "name+city"


def test_multilocation_not_merged():
    res = IdentityResolver(default_country="US")
    # Two distinct place_ids sharing a phone — must NOT merge.
    r1 = _rec(place_id="0x111:0x111", phone="+15550001234", business_name="Store A")
    r2 = _rec(place_id="0x222:0x222", phone="+15550001234", business_name="Store B")
    assert res.is_duplicate(r1)[0] is False
    assert res.is_duplicate(r2)[0] is False


def test_duplicate_detected():
    res = IdentityResolver(default_country="US")
    r1 = _rec(kgmid="/g/1")
    r2 = _rec(kgmid="/g/1")
    assert res.is_duplicate(r1)[0] is False
    assert res.is_duplicate(r2)[0] is True


def test_rollback_allows_rediscovery():
    res = IdentityResolver(default_country="US")
    r1 = _rec(kgmid="/g/9")
    assert res.is_duplicate(r1)[0] is False
    res.rollback(r1)
    r2 = _rec(kgmid="/g/9")
    assert res.is_duplicate(r2)[0] is False
