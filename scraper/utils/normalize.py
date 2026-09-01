"""Normalization primitives for URLs, phones, emails, and text.

These are pure functions with no global state — the single source of truth for
the canonical keys used by dedup, filters, and validation, and trivially
unit-testable. All code here is original; only the *concepts* of canonical
URL/phone/email normalization are shared with the wider scraping ecosystem.
"""
from __future__ import annotations

import html
import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit, urlunsplit

try:  # Runtime dependency; the fallback keeps imports usable in minimal tools.
    from ftfy import fix_text as _fix_text
except ImportError:  # pragma: no cover - requirements.txt installs ftfy
    _fix_text = None

try:  # Runtime dependency; the fallback is deliberately conservative.
    import phonenumbers
    from phonenumbers import NumberParseException
except ImportError:  # pragma: no cover - requirements.txt installs phonenumbers
    phonenumbers = None
    NumberParseException = ValueError

# Tracking / analytics params that never contribute to identity or stored URLs.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "gclsrc", "dclid", "fbclid", "msclkid", "mc_cid",
    "mc_eid", "igshid", "ref", "ref_src", "source", "cmpid", "_ga",
    "_gl", "yclid", "zanpid", "twclid", "wbraid", "gbraid",
    "sc_cid", "_vsrefdom", "y_source",
}

# Phone-extension keyword (F21): ext/extension/x/#/poste/anexo followed by 1-6
# digits. Captured separately so the extension is preserved on the output.
_EXT_RE = re.compile(
    r"[\s,;]*(?:ext(?:ension)?\.?|x|#|poste|anexo)\s*[:.#\- ]*\s*(\d{1,6})\b.*$",
    re.I,
)

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

# Analytics / ad / CDN / error-tracking vendor domains whose addresses are
# never a business's contact email. Matched by suffix (subdomain-inclusive).
_VENDOR_DOMAINS = {
    "googletagmanager.com", "google-analytics.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "sentry.io",
    "facebook.net", "cloudflare.com", "gstatic.com", "googlegroups.com",
    "zendesk.com", "hubspot.com", "intercom.io", "mailchimp.com",
    "campaignmonitor.com", "constantcontact.com", "sendgrid.net",
}

# File extensions that a regex email parser may mistake for a TLD.
_ASSET_TLDS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "mjs", "ico",
    "woff", "woff2", "ttf", "map", "json", "xml", "php", "html", "htm",
}


_HTML_TAG_RE = re.compile(r"<[^>]*>")
_FORMATTING_NOISE = {
    "\ufeff", "\u200b", "\u00ad", "\u061c", "\u180e", "\u200e", "\u200f",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2060", "\u2066",
    "\u2067", "\u2068", "\u2069", "\u206a", "\u206b", "\u206c", "\u206d",
    "\u206e", "\u206f",
}


def _coerce_text(value) -> str:
    """Decode text without silently losing non-ASCII characters."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("cp1252", errors="replace")
    return str(value)


def normalize_text(value) -> str:
    """Return clean, language-preserving text or ``N/A`` for empty input.

    Scraped values can contain mojibake (for example ``FranÃ§ais``), HTML
    entities, tags, zero-width markers, bidi controls, and invalid replacement
    characters.  Fix encoding first, decode entities, remove markup/control
    noise, normalize Unicode composition, and finally collapse whitespace.
    Valid non-English scripts and diacritics are intentionally preserved.
    """
    if value is None:
        return "N/A"

    s = _coerce_text(value)
    if _fix_text is not None:
        s = _fix_text(s)
    # Decode at most twice so double-escaped entities are cleaned without
    # repeatedly transforming ordinary ampersands.
    for _ in range(2):
        decoded = html.unescape(s)
        if decoded == s:
            break
        s = decoded
    s = _HTML_TAG_RE.sub(" ", s)
    s = unicodedata.normalize("NFC", s).replace("\ufffd", "")

    cleaned: list[str] = []
    for ch in s:
        category = unicodedata.category(ch)
        if ch in "\t\r\n" or category.startswith("Z"):
            cleaned.append(" ")
        elif category == "Cc" or ch in _FORMATTING_NOISE:
            # Drop control and known formatting noise such as NUL, BOM, bidi
            # marks, and zero-width spaces; retain valid script joiners.
            continue
        else:
            cleaned.append(ch)
    result = re.sub(r"\s+", " ", "".join(cleaned)).strip()
    return result or "N/A"


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
    """Return a metadata-checked E.164 number or ``N/A``.

    ``default_country`` is used only for national-format input.  Explicit
    international formats (``+...`` or ``00...``) are parsed independently.
    Invalid, ambiguous, overlong, and too-short values are rejected instead of
    being returned as plausible-looking digit strings.
    """
    if raw is None:
        return "N/A"
    candidate = _coerce_text(raw).strip()
    if not candidate or candidate.upper() in {"N/A", "NA", "NONE", "NULL"}:
        return "N/A"

    # Google Maps commonly exposes ``phone:tel:+...`` as the attribute value;
    # other sources use a plain ``tel:`` URI.
    candidate = re.sub(r"^(?:phone:tel:|tel:)", "", candidate, flags=re.I).strip()
    # Capture the extension SEPARATELY (so it survives as `` x<ext>``) instead
    # of being stripped and lost forever — extensions are valuable for B2B
    # outreach (F21).
    ext = None
    m_ext = _EXT_RE.search(candidate)
    if m_ext:
        ext = m_ext.group(1)
        candidate = candidate[:m_ext.start()].strip()
    # After extension removal, keep only the leading numeric phone token and
    # drop any remaining trailing alphabetic words, so a stray "call anytime"
    # cannot poison an otherwise valid number.
    m = re.match(r"[+]?[\d\s().,\-]+", candidate)
    if m and m.group(0).strip() != candidate.strip():
        candidate = m.group(0).strip()
    if not candidate or not re.fullmatch(r"[\d\s().,+-]+", candidate):
        return "N/A"

    explicit_region = candidate.startswith("+") or candidate.startswith("00")
    if candidate.startswith("00"):
        candidate = "+" + candidate[2:]
    try:
        if phonenumbers is not None:
            parsed = phonenumbers.parse(
                candidate,
                None if explicit_region else (default_country or "US").upper(),
            )
            # `is_possible_number` checks country metadata and length without
            # rejecting valid-looking test/VoIP/reserved ranges that do not
            # have a carrier assignment in libphonenumber's data.
            if not phonenumbers.is_possible_number(parsed):
                return "N/A"
            formatted = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
            return f"{formatted} x{ext}" if ext else formatted
    except (NumberParseException, ValueError, TypeError):
        return "N/A"

    # Minimal fallback for environments that import this module without the
    # declared dependency. It never claims to validate a national number.
    digits = re.sub(r"\D", "", candidate)
    if not explicit_region or not 7 <= len(digits) <= 15:
        return "N/A"
    out = f"+{digits}" if digits else "N/A"
    return f"{out} x{ext}" if ext and out != "N/A" else out


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
    """Return a rejection reason string or None if the email is acceptable.

    Rejection categories (in priority order): malformed syntax, an asset
    filename "TLD", a disposable/placeholder domain, a tracking-vendor domain,
    an over-long address, and — when a website is known — an off-domain
    non-personal address (which defaults to rejected rather than accepted).
    """
    e = normalize_email(email)
    if not e:
        return "invalid_syntax"
    if "@" not in e:
        return "invalid_syntax"
    local, domain = e.rsplit("@", 1)
    if "/" in local or "\\" in local:
        return "invalid_syntax"
    tld = domain.rsplit(".", 1)[-1].lower()
    if tld in _ASSET_TLDS:
        return "asset_filename"
    if any(domain == d or domain.endswith("." + d) for d in _DISPOSABLE_DOMAINS):
        return "disposable_domain"
    if any(domain == d or domain.endswith("." + d) for d in _VENDOR_DOMAINS):
        return "vendor_or_disposable_domain"
    if len(e) > 120:
        return "too_long"
    if website_url:
        wd = extract_domain(website_url)
        if wd and domain != wd and not is_personal_provider(domain):
            # Off-domain non-personal emails default to rejected (privacy +
            # data-quality); a suspicious local part is no longer required to
            # trigger rejection, it is simply an additional signal.
            return "off_domain"
    return None


def is_usable_email(email: str, max_length: int = 120, website_url: str | None = None) -> bool:
    return email_rejection_reason(email, website_url) is None
