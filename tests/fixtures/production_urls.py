"""Real production URLs captured from the delivered ``leads.csv``.

Cross-verification corpus: parsing these exact shapes catches the "works in my
head" regressions that a synthetic URL cannot. Each URL reproduces a specific
production defect documented in the audit.
"""

# A URL carrying an AD-panel token (`!5s0x…`) alongside the authoritative
# `!1s` place token. The cid must derive from `!1s`, NEVER the `!5s` token.
ABERLE_AD_TOKEN_URL = (
    "https://www.google.com/maps/place/Aberle/@29.6,-95.5/data="
    "!3m1!5s0x8640e9d9c02012b9:0x83e9e18eae7877cf"
    "!4m10!1m2!2m1!1sq!3m6!1s0x8640e99260148083:0x103f08fd2afc21!8m2!3d29.6!4d-95.5"
)

# A URL whose kgmid token is PERCENT-ENCODED (`!16s%2Fg%2F…`); the raw `/g/`
# regex historically missed it, leaving kgmid N/A on 100% of rows.
KGID_ENCODED_URL = (
    "https://www.google.com/maps/place/X/@29.8,-95.4,10z/data="
    "!4m10!1m2!2m1!1sq!16s%2Fg%2F1tf719p9"
)

# A URL with the search-camera viewport (`/maps/@29.8200218,-95.9757371,10z`)
# AND a true pin (`!3d29.8677916!4d-95.5618629`). Coordinates must come from
# the pin, never the viewport.
VIEWPORT_PLUS_PIN_URL = (
    "https://www.google.com/maps/place/X/@29.8200218,-95.9757371,10z/data="
    "!4m10!1m2!2m1!1sq!3m6!1s0x1:0x2!8m2!3d29.8677916!4d-95.5618629"
)

# A viewport-only URL (no `!3d`/`!4d`): the business pin is absent, so lat/lng
# must be omitted rather than defaulting to the viewport center.
VIEWPORT_ONLY_URL = (
    "https://www.google.com/maps/place/X/@29.8200218,-95.9757371,10z/data="
    "!4m10!1m2!2m1!1sq"
)

# A plain-text kgmid URL (unencoded `/g/…`), verifying both forms parse.
KGID_PLAIN_URL = "https://maps.google.com/place/X/data=!16s/g/1abcXYZ"

# A full place URL with `!1s` place_id + `!3d/!4d` pin (happy path reference).
FULL_PLACE_URL = (
    "https://www.google.com/maps/place/Nick's+Plumbing/@29.8,-95.4,17z/data="
    "!4m10!1m2!2m1!1splumber!3m6!1s0x8640c9aa12345678:0x0abcdef12345678"
    "!8m2!3d29.7604!4d-95.3698"
)

# The known production duplicate pair: IGD Plumbing & Air under two distinct
# place_ids but identical phone/domain/name (E7).
IGD_DUP_A = {
    "place_id": "0x8640cd2263c8f9d5:0x1", "phone": "+18324027405",
    "website": "https://igdplumbing.com", "business_name": "IGD Plumbing & Air",
    "city": "Houston",
}
IGD_DUP_B = {
    "place_id": "0x8640cd3fac8c34e5:0x2", "phone": "+18324027405",
    "website": "https://igdplumbing.com", "business_name": "IGD Plumbing & Air",
    "city": "Houston",
}
