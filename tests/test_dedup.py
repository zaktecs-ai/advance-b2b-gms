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


def test_same_domain_same_name_duplicate_even_with_distinct_place_ids():
    # F06: two place_ids sharing domain + phone + identical name are a dup.
    res = IdentityResolver(default_country="US")
    r1 = _rec(place_id="0xaaa:0x111", phone="+18324027405",
              website="https://igdplumbing.com", business_name="IGD Plumbing & Air",
              city="Houston")
    r2 = _rec(place_id="0xbbb:0x222", phone="+18324027405",
              website="https://igdplumbing.com", business_name="IGD Plumbing & Air",
              city="Houston")
    assert res.is_duplicate(r1)[0] is False
    assert res.is_duplicate(r2)[0] is True


def test_chain_locations_same_domain_diff_names_not_merged():
    res = IdentityResolver(default_country="US")
    a = _rec(place_id="0x1:0x1", website="https://missionac.com/locations/houston-tx",
             business_name="Mission Air Conditioning & Plumbing", city="Houston")
    b = _rec(place_id="0x2:0x2", website="https://missionac.com/locations/houston-tx",
             business_name="Mission Air Houston", city="Houston")
    assert res.is_duplicate(a)[0] is False
    assert res.is_duplicate(b)[0] is False


def test_same_phone_same_name_duplicate_with_distinct_place_ids():
    res = IdentityResolver(default_country="US")
    r1 = _rec(place_id="0x1:0x1", phone="+18328675309",
              website="http://wedgeworthplumbing.com",
              business_name="Wedgeworth Plumbing", city="Houston")
    r2 = _rec(place_id="0x2:0x2", phone="+18328675309",
              website="http://wedgeworthplumbing.com",
              business_name="Wedgeworth Plumbing", city="Houston")
    assert res.is_duplicate(r1)[0] is False
    assert res.is_duplicate(r2)[0] is True


def test_db_path_covers_older_history(tmp_path):
    # F32: a resolver seeded with EMPTY sets still detects a duplicate via the
    # checkpoint's SQLite lookup when the identity predates the in-memory preload.
    from scraper.checkpoint.store import CheckpointStore
    ck = CheckpointStore(tmp_path / "ck.sqlite")
    ck.register_record("old", "k-old", {"kgmid": "/g/999", "place_id": None,
                                        "canonical_domain": "acme.com",
                                        "normalized_phone": "+15550001",
                                        "name_key": "acme", "key_type": "kgmid",
                                        "city": "Dallas"}, "q",
                       {"business_name": "Acme"})
    ck.mark_committed("old", 0)
    # Simulate a cold resolver that did NOT preload this record.
    res = IdentityResolver(default_country="US")
    # Manual DB fallback: identity_exists is True for the committed key.
    assert ck.identity_exists("k-old")
    assert ck.domain_name_seen("acme.com", "acme")
    assert ck.phone_name_seen("+15550001", "acme")
    ck.close()
