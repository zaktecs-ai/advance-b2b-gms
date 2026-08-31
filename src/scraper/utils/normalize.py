"""Normalization primitives for URLs, phones, emails, and text.

These are pure functions with no global state — the single source of truth for
the canonical keys used by dedup, filters, and validation, and trivially
unit-testable. All code here is original; only the *concepts* of canonical
URL/phone/email normalization are shared with the wider scraping ecosystem.
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit, parse_qsl

# Tracking / analytics params that never contribute to identity or stored URLs.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "gclsrc", "dclid", "fbclid", "msclkid", "mc_cid",
    "mc_eid", "igshid", "ref", "ref_src", "source", "cmpid", "_ga",
    "_gl", "yclid", "zanpid", "twclid", "wbraid", "gbraid",
    "sc_cid", "_vsrefdom", "y_source",
}

# Redirect wrappers that resolve to a real destination via a url= param.
_GOOGLE_WRAPPERS = {
    "google.com", "www.google.com", "google.co.uk", "google.ca",
    "maps.google.com", "l.facebook.com", "lm.facebook.com",
}

# Personal/free email providers that are never "off-domain" in a bad way.
_PERSONAL_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "me.com", "live.com", "msn.com", "protonmail.com",
    "mail.com", "gmx.com", "zoho.com", "yandex.com", "fastmail.com",
}

# Suspicious words that, paired with an off-domain email, suggest a junk hit.
_SUSPICIOUS_WORDS = {
    "example", "test", "sample", "noreply", "no-reply", "donotreply",
    "spam", "demo", "admin", "info", "contact", "support", "sales",
    "email", "mail", "webmaster", "user", "foo", "bar", "john", "jane",
    "fake", "invalid", "none", "null", "placeholder", "yourname", "you",
}

_EMAIL_TOKEN_RE = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?\.[A-Za-z]{2,63}",
)

# Disposable / placeholder domains that should never be recorded as a real lead.
_DISPOSABLE_DOMAINS = {
    "example.com", "example.org", "example.net", "sentry.io", "yourdomain.com",
    "email.com", "domain.com", "test.com", "placeholder.com",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_text(value) -> str:
    """Collapse whitespace, strip control chars; return 'N/A' for empty."""
    if value is None:
        return "N/A"
    s = str(value)
    s = "".join(ch for ch in s if ch.isprintable() or ch in "\t ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or "N/A"


def _is_bare_ipv6_host(netloc: str) -> bool:
    if not netloc or netloc.startswith("[") or "@" in netloc:
        return False
    if netloc.count(":") < 2:
        return False
    try:
        ipaddress.IPv6Address(netloc)
        return True
    except ValueError:
        return False


def normalize_url(raw: str | None) -> str:
    """Return a canonical identity URL or 'N/A'."""
    if not raw or str(raw).strip().upper() == "N/A":
        return "N/A"
    raw = raw.strip()
    pre = urlsplit(raw)
    if pre.scheme and pre.scheme.lower() not in ("http", "https"):
        return "N/A"
    if not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "N/A"
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return "N/A"

    netloc = parts.netloc
    if _is_bare_ipv6_host(netloc):
        raw = urlunsplit((scheme, f"[{netloc}]", parts.path, parts.query, parts.fragment))
        parts = urlsplit(raw)

    host = (parts.hostname or "").lower()
    if not host:
        return "N/A"
    if host.startswith("www."):
        host = host[4:]

    if host in _GOOGLE_WRAPPERS:
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        for key in ("url", "u", "q", "target"):
            candidate = q.get(key) or q.get(key.lower())
            if candidate and candidate.lower().startswith(("http://", "https://")):
                return normalize_url(candidate)

    try:
        port = parts.port
    except ValueError:
        port = None
    is_ipv6 = ":" in host
    netloc = f"[{host}]" if is_ipv6 else host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"

    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        kl = k.lower()
        if kl in _TRACKING_PARAMS:
            continue
        if kl in ("redirect", "url", "target", "goto", "next", "return", "dest", "continue") and len(v or "") > 200:
            continue
        kept.append((k, v))
    query = "&".join(f"{k}={v}" for k, v in kept)

    path = parts.path or ""
    path = re.sub(r"/{2,}", "/", path)
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, query, ""))


def extract_domain(url: str) -> str:
    """Return a lowercase registrable domain or ''."""
    norm = normalize_url(url)
    if norm == "N/A":
        return ""
    host = urlsplit(norm).hostname or ""
    return canonical_domain(host)


def canonical_domain(host: str) -> str:
    """Reduce a hostname to its registrable domain conservatively."""
    host = (host or "").lower().strip().strip(".")
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split(".")
    _2ND = {"co", "com", "org", "net", "gov", "edu", "ac", "me", "ltd", "plc"}
    _PRIVATE = {"github.io", "pages.dev", "web.app", "firebaseapp.com"}
    if len(labels) >= 3 and labels[-2] in _2ND and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    if len(labels) >= 3:
        tail = ".".join(labels[-2:])
        if tail in _PRIVATE:
            return ".".join(labels[-3:])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


# ---------------------------------------------------------------------------
# Phone normalization (country-aware)
# ---------------------------------------------------------------------------

def normalize_phone(raw: str | None, default_country: str = "US") -> str:
    """Normalize a phone to a canonical E.164-ish numeric form or 'N/A'."""
    if not raw or str(raw).strip().upper() == "N/A":
        return "N/A"
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return "N/A"
    cc = _COUNTRY_CODES.get(default_country.upper(), "1")
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("+"):
        digits = digits[1:]
    # Strip a leading national trunk prefix (0 or 1) when the country code is
    # prepended and the number is plausibly national-length (>= 10 digits).
    if len(digits) >= 11 and digits.startswith(cc) and len(digits) - len(cc) >= 10:
        return digits
    if len(digits) >= 10 and not digits.startswith(cc):
        # Best-effort national-to-international: prepend the country code.
        candidate = cc + digits
        if len(candidate) >= 11:
            return candidate
    return digits


_COUNTRY_CODES = {
    "US": "1", "CA": "1", "GB": "44", "AU": "61", "DE": "49", "FR": "33",
    "IN": "91", "PK": "92", "NZ": "64", "IE": "353", "NL": "31", "ES": "34",
    "IT": "39", "AE": "971", "SA": "966",
}


# ---------------------------------------------------------------------------
# Email normalization + cleaning helpers
# ---------------------------------------------------------------------------

def normalize_email(raw: str | None) -> str:
    """Lowercase, strip, and basic-validate an email; '' if unusable."""
    if not raw:
        return ""
    e = str(raw).strip().lower().rstrip(".")
    e = re.sub(r"\s+", "", e)
    # Strip stray leading/trailing quote chars (e.g. from inline JS strings).
    e = e.strip("'\"`")
    if not _EMAIL_TOKEN_RE.fullmatch(e):
        return ""
    return e


def is_personal_provider(domain: str) -> bool:
    return domain.lower() in _PERSONAL_PROVIDERS


def email_rejection_reason(email: str, website_url: str | None = None) -> str | None:
    """Return a rejection reason string or None if the email is acceptable."""
    e = normalize_email(email)
    if not e:
        return "invalid_syntax"
    if "@" not in e:
        return "invalid_syntax"
    local, domain = e.rsplit("@", 1)
    if domain in _DISPOSABLE_DOMAINS:
        return "disposable_domain"
    if len(e) > 120:
        return "too_long"
    if website_url:
        wd = extract_domain(website_url)
        if wd and domain != wd and not is_personal_provider(domain):
            # Off-domain: reject only if the local part carries a suspicious word.
            if any(w in local for w in _SUSPICIOUS_WORDS):
                return "off_domain_suspicious"
    return None


def is_usable_email(email: str, max_length: int = 120, website_url: str | None = None) -> bool:
    return email_rejection_reason(email, website_url) is None
