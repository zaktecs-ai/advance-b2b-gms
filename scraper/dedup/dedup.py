"""Identity resolution and deduplication.

Composite identity ladder (strongest first):
    1. ``kgmid``   — Google's Knowledge Graph Machine ID. Never null, unique per
       business. This is the authoritative top key.
    2. ``place_id`` — legacy top key; authoritative *when present* but can be
       missing for some listings (e.g. rental-only data endpoints).
    3. ``(canonical domain + city)`` — shared franchise/chain fallback.
    4. ``normalized phone`` — shared front-desk line fallback.
    5. ``(name key + city)`` — last resort.

Two listings that share a phone/domain *but carry distinct kgmids* are multi-
location chains, NOT duplicates — the weak signals must never merge them. The
fallback sets are fed only by records that LACK both kgmid and place_id.
"""
from __future__ import annotations

import hashlib

from ..utils.normalize import (
    extract_domain,
    normalize_phone,
    normalize_text,
    normalize_url,
)


def _name_key(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _clean_id(value) -> str | None:
    raw = (value or "").strip()
    if not raw or raw.upper() in ("N/A", "NONE", "NULL"):
        return None
    return raw


def resolve_identity(record: dict, default_country: str = "US") -> dict:
    """Compute identity signals + a composite key for a record dict."""
    name = normalize_text(record.get("business_name"))
    raw_url = normalize_url(record.get("website", ""))
    domain = extract_domain(raw_url) if raw_url != "N/A" else ""
    record_country = str(record.get("country") or "").strip().upper()
    phone_country = record_country if len(record_country) == 2 and record_country.isalpha() else default_country
    phone = normalize_phone(
        record.get("phone") or record.get("phone_international"), phone_country
    )
    city = normalize_text(record.get("city")).lower()

    kgmid = _clean_id(record.get("kgmid"))
    place_id = _clean_id(record.get("place_id"))

    signals = {
        "kgmid": kgmid,
        "place_id": place_id,
        "canonical_domain": domain or None,
        "normalized_phone": phone if phone != "N/A" else None,
        "name_key": _name_key(name) if name != "N/A" else None,
        "city": city if city != "n/a" else None,
    }

    key_parts = []
    if kgmid:
        key_parts.append(f"kg:{kgmid}")
        signals["key_type"] = "kgmid"
    elif place_id:
        key_parts.append(f"pid:{place_id}")
        signals["key_type"] = "place_id"
    elif domain and city:
        key_parts.append(f"dom:{domain}")
        key_parts.append(f"city:{city}")
        signals["key_type"] = "domain+city"
    elif signals["normalized_phone"]:
        key_parts.append(f"ph:{signals['normalized_phone']}")
        signals["key_type"] = "phone"
    elif signals["name_key"] and city:
        key_parts.append(f"name:{signals['name_key']}")
        key_parts.append(f"city:{city}")
        signals["key_type"] = "name+city"
    else:
        signals["key_type"] = "none"

    composite = "|".join(key_parts)
    # SHA1 is used only to build a non-secret internal dedup fingerprint — never
    # for authentication or signing — so it is explicitly marked as such.
    signals["identity_key"] = (
        hashlib.sha1(composite.encode("utf-8"), usedforsecurity=False).hexdigest()
        if composite else ""
    )
    return signals


class IdentityResolver:
    """Stateful resolver seeded from the checkpoint store; first valid wins."""

    def __init__(self, seen_identities: set[str] | None = None,
                 seen_domains: set[str] | None = None,
                 seen_phones: set[str] | None = None,
                 seen_domain_city: set[str] | None = None,
                 default_country: str = "US"):
        self._identities: set[str] = set(seen_identities or set())
        self._domains: set[str] = set(seen_domains or set())
        self._phones: set[str] = set(seen_phones or set())
        self._domain_city: set[str] = set(seen_domain_city or set())
        self._default_country = default_country

    def is_duplicate(self, record: dict) -> tuple[bool, str, dict]:
        """Return (is_dup, reason, signals). New records are recorded as seen."""
        sig = resolve_identity(record, self._default_country)
        key = sig["identity_key"]
        domain = sig["canonical_domain"]
        phone = sig["normalized_phone"]
        city = sig["city"]
        has_strong_id = sig["kgmid"] is not None or sig["place_id"] is not None

        if key and key in self._identities:
            return True, f"duplicate_identity:{sig['key_type']}", sig

        # Weak fallback guards apply ONLY to records lacking a strong id.
        if not has_strong_id:
            if domain and city:
                dck = f"{domain}|{city}"
                if dck in self._domain_city:
                    return True, "duplicate_domain+city", sig
            if phone and phone in self._phones:
                return True, "duplicate_phone", sig

        if key:
            self._identities.add(key)
        if domain:
            self._domains.add(domain)
            if city and not has_strong_id:
                self._domain_city.add(f"{domain}|{city}")
        if phone and not has_strong_id:
            self._phones.add(phone)
        return False, "", sig

    def rollback(self, record: dict) -> None:
        """Remove a record's identity signals (on filter/reject) so a legit
        later re-discovery is not wrongly dropped as a duplicate."""
        sig = resolve_identity(record, self._default_country)
        key = sig["identity_key"]
        domain = sig["canonical_domain"]
        phone = sig["normalized_phone"]
        city = sig["city"]
        if key:
            self._identities.discard(key)
        if domain:
            self._domains.discard(domain)
            if city:
                self._domain_city.discard(f"{domain}|{city}")
        if phone:
            self._phones.discard(phone)
