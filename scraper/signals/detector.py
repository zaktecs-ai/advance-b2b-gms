"""Config-driven business signal detectors + decision-maker extraction.

Signals run over a page's collected context (normalized text, URLs, scripts,
structured data, tech). Custom signals can be added in YAML without editing
code. Decision-maker extraction (off by default) pulls person + title from
about/team/LinkedIn pages.

The detector exposes BOTH a compact ``detect_signals()`` (business keywords,
backward-compatible) and a richer ``SignalDetector`` that emits the YES/NO
outcome columns used by the export contract (meta_pixel, ga4, gtm, analytics,
booking_system, chat_widget, etc.).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..utils.normalize import normalize_text


@dataclass
class PageContext:
    text: str = ""
    html: str = ""
    url: str = ""
    urls: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    structured: str = ""
    technologies: set[str] = field(default_factory=set)


# Built-in lead-gen signals (keyword-driven, business signals).
_SIGNAL_DEFS: dict[str, tuple[str, ...]] = {
    "pricing": ("pricing", "price per", "starting at", "rate card", "quote online"),
    "financing": ("financing", "payment plan", "0% interest", "monthly installments"),
    "licensed_insured": ("licensed", "insured", "bonded", "certified"),
    "established": ("family owned", "established in", "since 19", "since 20", "years of experience"),
    "portfolio": ("portfolio", "case study", "our work", "projects", "gallery"),
    "mobile_service": ("mobile service", "we come to you", "on-site", "house calls"),
    "membership": ("membership", "subscribe", "join our", "loyalty program"),
}


def _kw_in_blob(kw: str, blob: str) -> bool:
    """Word-boundary match for single-word keywords.

    A bare substring check makes ``licensed`` match ``unlicensed`` (F19).
    Multi-word phrases match as substrings; intentional single words require a
    word boundary so a negation/compound is not falsely detected.
    """
    kw = (kw or "").strip().lower()
    if not kw:
        return False
    if " " in kw:
        return kw in blob
    return re.search(rf"\b{re.escape(kw)}\b", blob) is not None


def detect_signals(ctx: PageContext, custom: dict | None = None) -> dict:
    """Return {signal_name: (detected, evidence)} for built-in + custom."""
    blob = "\n".join([ctx.text or "", ctx.html or "",
                      "\n".join(ctx.urls or []), ctx.structured or ""]).lower()
    results: dict = {}
    for name, keywords in _SIGNAL_DEFS.items():
        for kw in keywords:
            if _kw_in_blob(kw, blob):
                results[name] = (True, kw)
                break
        else:
            results[name] = (False, None)

    for name, spec in (custom or {}).items():
        if not isinstance(spec, dict):
            continue
        kws = spec.get("keywords", [])
        regexes = spec.get("regex", [])
        match_any = spec.get("match_logic", "ANY").upper() != "ALL"
        hits = [kw for kw in kws if kw.lower() in blob]
        for r in regexes:
            if re.search(r, blob, re.I):
                hits.append(f"re:{r}")
        detected = any(hits) if match_any else len(hits) >= len(kws) + len(regexes)
        results[name] = (detected, hits[0] if hits else None)
    return results


# ---------------------------------------------------------------------------
# Rich YES/NO signal detectors for the tech / intent columns
# ---------------------------------------------------------------------------

def _any_src(scripts, pattern):
    p = re.compile(pattern, re.I)
    for s in scripts:
        if p.search(s):
            return True, s
    return False, None


def _any_src_or_html(scripts, html, pattern):
    """Match a script-`src` URL in either the extracted scripts or the HTML.

    The pattern is a resource-URL token (e.g. ``googletagmanager.com/gtag/js``)
    that appears in a real ``<script src=...>`` but not in ordinary prose, so
    anchoring detection here avoids false-YES on brand name-drops.
    """
    hit, ev = _any_src(scripts, pattern)
    if hit:
        return True, ev
    m = re.search(
        r"<script\b[^>]*\bsrc\s*=\s*['\"]([^'\"]*(?:"
        + pattern
        + r")[^'\"]*)['\"]",
        html or "", re.I,
    )
    return (True, m.group(1)) if m else (False, None)


def _meta_pixel(ctx):
    hit, ev = _any_src(ctx.scripts, r"connect\.facebook\.net|facebook\.com/tr|fbq\(")
    if hit:
        return True, ev or "facebook pixel script detected"
    if re.search(r"fbq\(", ctx.html, re.I):
        return True, "facebook pixel script detected"
    return False, None


def _ga4(ctx):
    hit, ev = _any_src_or_html(ctx.scripts, ctx.html, r"googletagmanager\.com/gtag/js|google-analytics\.com/(analytics|ga)\.js")
    if hit:
        return True, ev or "GA4/gtag measurement script detected"
    # Only an explicit config call or measurement id counts — NOT a prose
    # mention of "gtag()" in a blog post.
    if re.search(r"gtag\(\s*['\"]config['\"]|G-[A-Z0-9]{8,10}\b", ctx.html):
        return True, "GA4 config detected"
    return False, None


def _gtm(ctx):
    hit, ev = _any_src_or_html(ctx.scripts, ctx.html, r"googletagmanager\.com/gtm\.js")
    if hit:
        return True, ev or "Google Tag Manager script detected"
    if re.search(r"GTM-[A-Z0-9]{6,}\b", ctx.html):
        return True, "Google Tag Manager container detected"
    return False, None


def _booking(ctx):
    booking_domains = re.compile(
        r"(?:calendly\.com|acuityscheduling\.com|booksy\.com|"
        r"mindbodyonline\.com|vagaro\.com|fresha\.com|setmore\.com|appointy\.com|"
        r"youcanbook\.me|simplybook\.me|schedulicity\.com)(?:/[^\s\"'<>]*)", re.I)
    hay = "\n".join(ctx.urls) + "\n" + ctx.html
    m = booking_domains.search(hay)
    return (True, m.group(0)) if m else (False, None)


def _chat_widget(ctx):
    hit, ev = _any_src_or_html(ctx.scripts, ctx.html,
        r"tawk\.to|intercom|drift|livechatinc|zopim|crisp\.chat|freshchat|hubspot\.com/forms")
    if hit:
        return True, ev or "chat widget script detected"
    return False, None


def _analytics(ctx):
    hit, ev = _any_src_or_html(ctx.scripts, ctx.html,
        r"google-analytics\.com/(analytics|ga)\.js|segment\.com/analytics|static\.hotjar\.com|clarity\.ms/tag")
    if hit:
        return True, ev or "analytics script detected"
    if re.search(r"mixpanel\.init\(|matomo|_paq\.push", ctx.html):
        return True, "analytics init detected"
    return False, None


def _advertising(ctx):
    """Ad-spend intent: Meta Pixel, Google Ads, or display/ad tags.

    GTM is intentionally NOT ad-spend evidence on its own (it is a tag
    container, not an ad purchase). Detection is anchored to script sources and
    explicit init calls, never to a prose brand mention.
    """
    if re.search(r"fbq\(\s*['\"]init['\"]", ctx.html):
        return True, "meta pixel init detected"
    if re.search(r"adsbygoogle\s*=|googlesyndication\.com|doubleclick\.net", ctx.html):
        return True, "ad network tag detected"
    hit, ev = _any_src_or_html(ctx.scripts, ctx.html,
        r"doubleclick\.net|adsbygoogle\.js|connect\.facebook\.net.*fbevents|connect\.facebook\.net")
    if hit:
        return True, ev or "ad network script detected"
    return False, None


def _kw_signal(ctx, keywords):
    blob = (ctx.text or "").lower() + " " + (ctx.html or "").lower()
    for kw in keywords:
        if _kw_in_blob(kw, blob):
            return True, kw
    return False, None


def _kw_signal_all(ctx, keywords):
    """ALL-match (AND): every keyword must be present for the signal to fire."""
    blob = (ctx.text or "").lower() + " " + (ctx.html or "").lower()
    if not keywords:
        return False, None
    for kw in keywords:
        if not _kw_in_blob(kw, blob):
            return False, None
    return True, keywords[0]


RICH_SIGNALS: dict = {
    "meta_pixel":        {"fields": ["meta_pixel"], "fn": _meta_pixel},
    "ga4":               {"fields": ["ga4"], "fn": _ga4},
    "gtm":               {"fields": ["gtm"], "fn": _gtm},
    "analytics":         {"fields": ["analytics"], "fn": _analytics},
    "advertising":       {"fields": ["advertising"], "fn": _advertising},
    "booking_system":    {"fields": ["booking_system"], "fn": _booking},
    "chat_widget":       {"fields": ["chat_widget"], "fn": _chat_widget},
    "pricing":           {"fields": ["signal_pricing"], "fn": lambda c: _kw_signal(c, _SIGNAL_DEFS["pricing"])},
    "financing":         {"fields": ["signal_financing"], "fn": lambda c: _kw_signal(c, _SIGNAL_DEFS["financing"])},
    "licensed_insured":  {"fields": ["signal_licensed_insured"], "fn": lambda c: _kw_signal(c, _SIGNAL_DEFS["licensed_insured"])},
    "established":       {"fields": ["signal_established"], "fn": lambda c: _kw_signal(c, _SIGNAL_DEFS["established"])},
    "portfolio":         {"fields": ["signal_portfolio"], "fn": lambda c: _kw_signal(c, _SIGNAL_DEFS["portfolio"])},
    "mobile_service":    {"fields": ["signal_mobile_service"], "fn": lambda c: _kw_signal(c, _SIGNAL_DEFS["mobile_service"])},
    "membership":        {"fields": ["signal_membership"], "fn": lambda c: _kw_signal(c, _SIGNAL_DEFS["membership"])},
}

TECH_FIELDS = ["meta_pixel", "ga4", "gtm", "analytics", "advertising",
               "booking_system", "chat_widget"]


class SignalDetector:
    """Yield YES/NO outcome columns for the export contract."""

    def __init__(self, custom_signals: dict | None = None):
        self._custom = custom_signals or {}

    def run(self, ctx: PageContext) -> tuple[dict, dict]:
        outcome: dict = {}
        evidence: dict = {}
        for name, spec in RICH_SIGNALS.items():
            detected, ev = spec["fn"](ctx)
            for f in spec["fields"]:
                outcome[f] = "YES" if detected else "NO"
            if detected and ev:
                evidence[name] = ev
        for name, spec in self._custom.items():
            if not isinstance(spec, dict) or not spec.get("enabled", True):
                continue
            kws = spec.get("keywords", []) or []
            # match: any = OR across keywords (default); match: all = AND —
            # multiple distinct keyword filters combined into one signal.
            if str(spec.get("match", "any")).lower() == "all":
                detected, ev = _kw_signal_all(ctx, kws)
            else:
                detected, ev = _kw_signal(ctx, kws)
            # Config-driven custom signals may choose their own export column
            # name via `column:`; the legacy default key is signal_<name>.
            out_key = str(spec.get("column") or f"signal_{name}").strip().lower()
            outcome[out_key] = "YES" if detected else "NO"
            if detected and ev:
                evidence[name] = ev
        return outcome, evidence


# -- Decision-maker extraction ----------------------------------------------

_TITLE_PATTERN = (
    r"CEO|Chief Executive Officer|Founder|Co-Founder|Owner|President|"
    r"Vice President|Managing Director|Principal|Partner|Manager|Director|"
    r"Proprietor|General Manager"
)
_TITLE_RE = re.compile(rf"\b({_TITLE_PATTERN})\b", re.I)
# The name portion is deliberately case-SENSITIVE (title-case tokens) so that
# lowercase prose ("led by", "terms of service") is never captured as a name.
_NAME_RE = re.compile(
    r"\b(?:[A-ZÀ-ÖØ-Ý][\w'’.-]*)(?:\s+[A-ZÀ-ÖØ-Ý][\w'’.-]*){1,3}\b"
)
# The scoped inline flag syntax `(?-i:...)` requires Python >= 3.11
# (mirrored in `pyproject.toml`'s `requires-python`). Do not lower the pin
# without replacing these scoped flags with a compatible construct.
_NAME_TITLE_RE = re.compile(
    rf"(?P<name>(?-i:{_NAME_RE.pattern}))\s*[,;:\-–—]\s*"
    rf"(?P<title>{_TITLE_PATTERN})\b|"
    rf"(?P<title_before>{_TITLE_PATTERN})\s*[,;:\-–—:]\s*(?P<name_after>(?-i:{_NAME_RE.pattern}))|"
    rf"(?P<title_spaced>{_TITLE_PATTERN})\s+(?P<name_spaced>(?-i:{_NAME_RE.pattern}))",
    re.I,
)


# Non-person words that must never be captured as a name, plus the CTA verbs
# that glue onto a real-name match in footer text ("Email Wayne…", "Meet Hugo").
_NAME_CTA_PREFIX_RE = re.compile(
    r"^(?:email|call|contact|meet|ask|talk to|message|reach|hire)\s+"
    r"(?:to\s+|our\s+|the\s+)?", re.I)
_NAME_NOT_PERSON_WORDS = {
    "sponsor", "sponsors", "main", "team", "staff", "company", "llc",
    "inc", "co", "plumbing", "plumbers", "services", "service",
    "group", "about", "us", "me", "him", "her", "them",
}
# G03: role/department words that inside a captured NAME mark footer/CTA/panel
# text, not a person ("Ground CREW", "DENTAL ASSISTANT", "Clinical OPERATIONS",
# "Reservations Schedule", "…EMAIL General"). Title words belong in the TITLE
# group, never the name, so "manager" is included here too.
_NAME_ROLE_WORDS = {
    "assistant", "hygienist", "crew", "operations", "clinical", "email",
    "reservations", "schedule", "receptionist", "staff", "team", "dept",
    "department", "coordinator", "supervisor", "dispatcher", "technician",
    "scheduler", "office", "front", "desk", "dispatch", "manager",
}
# G03: heading/link-text shapes ("Why Choose X", "Hotels Near …") that a
# capitalized 2-4 token match happily swallows.
_NAME_HEADING_PREFIXES = {"why", "hotels", "hotel", "our", "meet", "welcome",
                          "about", "the"}
# G03: place words used in neighborhood/complex names ("Lakewood Highland
# PARK Kelli") — rejected as NON-FINAL tokens only, so real surname "Park"
# ("Jessica Park, Owner") keeps passing.
_NAME_PLACE_WORDS = {
    "park", "plaza", "center", "centre", "mall", "square", "garden",
    "gardens", "heights", "hills", "valley", "lake", "creek", "ridge",
    "village", "crossing", "point", "pointe",
}
# Names that END in a title word were forged from a split title ("…Villalobos
# Vice / PRESIDENT") or a glued CTA; reject them.
_TITLE_WORDS = {"ceo", "president", "manager", "director", "founder",
                "owner", "partner", "principal", "proprietor", "vice",
                "chief", "officer"}

_NAME_STOPWORDS = {
    "terms", "term", "service", "services", "privacy", "policy", "policies",
    "home", "about", "contact", "team", "story", "our", "founder", "street",
    "suite", "menu", "follow", "share", "tweet", "copyright", "reserved",
    "rights", "the", "and", "or", "with", "from", "for", "your", "this",
    "that", "overview", "managing", "director", "principal", "manager",
    "owner", "president", "partner", "ceo", "more", "read", "view", "see",
    "back", "top", "next", "previous", "sign", "log", "account", "click",
    "here", "learn", "book", "get", "now", "today", "news", "blog", "media",
}


def _clean_person_name(value: str) -> str:
    cleaned = normalize_text(value).strip(" ,;:-–—")
    if cleaned == "N/A":
        return cleaned
    # A CTA verb glued to the front of a name ("Email Wayne…", "Meet Hugo…") is
    # footer marketing copy, not evidence of a decision maker — reject it
    # outright rather than stripping (after stripping, a multi-word CTA like
    # "Email Wayne William A" would still look name-shaped). (F07)
    if _NAME_CTA_PREFIX_RE.search(cleaned):
        return "N/A"
    cleaned = re.sub(
        rf"^(?:{_TITLE_PATTERN})\s+", "", cleaned, flags=re.I
    )
    return cleaned.strip(" ,;:-–—") or "N/A"


def _looks_like_person(name: str) -> bool:
    """Reject boilerplate/place/nav text that merely looks name-shaped."""
    if not name or name == "N/A":
        return False
    toks = [t for t in name.split() if t]
    if not (2 <= len(toks) <= 4):
        return False
    # Reject duplicated-token names (adjacent DOM text nodes gluing a name
    # twice: "Jack Gilbert Jack Gilbert").
    lowered = [t.lower() for t in toks]
    if len(lowered) != len(set(lowered)):
        return False
    # Reject names that END in a title word (a split title forged a fake name).
    if toks[-1].lower() in _TITLE_WORDS:
        return False
    lowered = [t.lower() for t in toks]
    # G03: role/department words anywhere inside the name reject it.
    if any(w in _NAME_ROLE_WORDS for w in lowered):
        return False
    # G03: heading/link-text shapes ("Why Choose X", "Hotels Near …").
    if lowered[0] in _NAME_HEADING_PREFIXES:
        return False
    # G03: neighborhood/complex names ("Lakewood Highland Park Kelli") carry a
    # place word in a non-final position.
    if any(w in _NAME_PLACE_WORDS for w in lowered[:-1]):
        return False
    for t in toks:
        word = re.sub(r"[^\wÀ-ÖØ-öø-ÿ]", "", t)
        if not word:
            return False
        if word.lower() in _NAME_STOPWORDS:
            return False
        if word.lower() in _NAME_NOT_PERSON_WORDS:
            return False
        if not re.match(r"^[A-ZÀ-ÖØ-Ý]", t):
            return False
    return True


def extract_decision_maker(text: str) -> tuple[str, str]:
    """Return the first validated ``(name, title)`` pair, or (``""``, ``""``)."""
    cleaned = normalize_text(text)
    if cleaned == "N/A":
        return "", ""

    # Scan ALL name+title matches; accept the first with a genuine human name.
    for match in _NAME_TITLE_RE.finditer(cleaned):
        name = (match.group("name") or match.group("name_after")
                or match.group("name_spaced") or "")
        title = (match.group("title") or match.group("title_before")
                 or match.group("title_spaced") or "")
        # Reject when a CTA verb ("Email", "Meet", "Call", …) immediately
        # precedes the match — that is footer/CTA copy, not a person (F07).
        prefix = cleaned[:match.start()].strip()
        if _NAME_CTA_PREFIX_RE.search(prefix or ""):
            continue
        name = _clean_person_name(name)
        title = normalize_text(title)
        if name != "N/A" and title != "N/A" and _looks_like_person(name):
            return name, title

    # A standalone title with no adjacent validated name is not a decision
    # maker. The loose 80-char-window fallback previously fabricated people
    # from nav/footer boilerplate ("Terms Of Service. Our..."), so it is gone.
    return "", ""
