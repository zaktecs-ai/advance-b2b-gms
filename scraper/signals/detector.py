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


def detect_signals(ctx: PageContext, custom: dict | None = None) -> dict:
    """Return {signal_name: (detected, evidence)} for built-in + custom."""
    blob = "\n".join([ctx.text or "", ctx.html or "",
                      "\n".join(ctx.urls or []), ctx.structured or ""]).lower()
    results: dict = {}
    for name, keywords in _SIGNAL_DEFS.items():
        for kw in keywords:
            if kw in blob:
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


def _meta_pixel(ctx):
    hit, ev = _any_src(ctx.scripts, r"connect\.facebook\.net|facebook\.com/tr|fbq\(")
    if hit:
        return True, ev or "facebook pixel script detected"
    if re.search(r"fbq\(", ctx.html, re.I):
        return True, "facebook pixel script detected"
    return False, None


def _ga4(ctx):
    if re.search(r"gtag\(|googletagmanager\.com/gtag|G-[A-Z0-9]{6,}", ctx.html, re.I):
        return True, "GA4/gtag measurement script detected"
    hit, ev = _any_src(ctx.scripts, r"googletagmanager\.com/gtag|google-analytics\.com")
    return (True, ev) if hit else (False, None)


def _gtm(ctx):
    hit, ev = _any_src(ctx.scripts, r"googletagmanager\.com/gtm\.js")
    if hit:
        return True, ev or "Google Tag Manager script detected"
    if re.search(r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]+", ctx.html, re.I):
        return True, "Google Tag Manager detected"
    return False, None


def _booking(ctx):
    booking_domains = re.compile(
        r"calendly\.com|acuityscheduling\.com|booksy\.com|mindbodyonline\.com|"
        r"vagaro\.com|fresha\.com|setmore\.com|appointy\.com|square\.up/site|"
        r"youcanbook\.me|simplybook\.me|schedulicity\.com", re.I)
    hay = ctx.text + "\n" + "\n".join(ctx.urls) + "\n" + ctx.html
    m = booking_domains.search(hay)
    return (True, m.group(0)) if m else (False, None)


def _chat_widget(ctx):
    if re.search(r"tawk\.to|intercom|drift\.com|livechatinc\.com|zopim|crisp\.chat|"
                 r"hubspot\.com/forms|chat-widget|freshchat|zendesk", ctx.html, re.I):
        return True, "chat widget detected"
    hit, ev = _any_src(ctx.scripts, r"tawk\.to|intercom|drift|livechat|zopim|crisp\.chat|hubspot")
    return (True, ev) if hit else (False, None)


def _analytics(ctx):
    if re.search(r"google-analytics|analytics\.js|segment\.com|mixpanel|hotjar|matomo|"
                 r"clarity\.ms", ctx.html, re.I):
        return True, "analytics script detected"
    return False, None


def _advertising(ctx):
    """Ad-spend intent: Meta Pixel, Google Ads, or display/ad tags."""
    if re.search(r"fbq\(|facebook\.com/tr|connect\.facebook\.net", ctx.html, re.I):
        return True, "meta pixel (ad) detected"
    if re.search(r"doubleclick|adsbygoogle|googlesyndication|googleadservices|"
                 r"taboola|outbrain", ctx.html, re.I):
        return True, "ad network tag detected"
    hit, ev = _any_src(ctx.scripts, r"doubleclick|adsbygoogle|googletagmanager|facebook\.net")
    return (True, ev) if hit else (False, None)


def _kw_signal(ctx, keywords):
    blob = (ctx.text or "").lower() + " " + (ctx.html or "").lower()
    for kw in keywords:
        if kw in blob:
            return True, kw
    return False, None


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
            detected, ev = _kw_signal(ctx, kws)
            outcome[f"signal_{name}"] = "YES" if detected else "NO"
            if detected and ev:
                evidence[name] = ev
        return outcome, evidence


# -- Decision-maker extraction ----------------------------------------------

_TITLE_PATTERN = (
    r"CEO|Chief Executive Officer|Founder|Co-Founder|Owner|President|"
    r"Managing Director|Principal|Partner|Manager|Director|Proprietor"
)
_TITLE_RE = re.compile(rf"\b({_TITLE_PATTERN})\b", re.I)
_NAME_RE = re.compile(
    r"\b(?:[A-ZÀ-ÖØ-Ý][\w'’.-]*)(?:\s+[A-ZÀ-ÖØ-Ý][\w'’.-]*){1,3}\b"
)
_NAME_TITLE_RE = re.compile(
    rf"(?P<name>{_NAME_RE.pattern})\s*[,;:\-–—]\s*"
    rf"(?P<title>{_TITLE_PATTERN})\b|"
    rf"(?P<title_before>{_TITLE_PATTERN})\s*[,;:\-–—:]\s*(?P<name_after>{_NAME_RE.pattern})",
    re.I,
)


def _clean_person_name(value: str) -> str:
    cleaned = normalize_text(value).strip(" ,;:-–—")
    if cleaned == "N/A":
        return cleaned
    cleaned = re.sub(
        rf"^(?:{_TITLE_PATTERN})\s+", "", cleaned, flags=re.I
    )
    return cleaned.strip(" ,;:-–—") or "N/A"


def extract_decision_maker(text: str) -> tuple[str, str]:
    """Return the first high-confidence ``(name, title)`` pair."""
    cleaned = normalize_text(text)
    if cleaned == "N/A":
        return "", ""

    match = _NAME_TITLE_RE.search(cleaned)
    if match:
        name = match.group("name") or match.group("name_after") or ""
        title = match.group("title") or match.group("title_before") or ""
        name = _clean_person_name(name)
        title = normalize_text(title)
        if name != "N/A" and title != "N/A":
            return name, title

    title_match = _TITLE_RE.search(cleaned)
    if not title_match:
        return "", ""
    title = normalize_text(title_match.group(1))
    before = cleaned[max(0, title_match.start() - 80): title_match.start()]
    after = cleaned[title_match.end(): title_match.end() + 80]
    for candidate in (after, before):
        name_match = _NAME_RE.search(candidate)
        if name_match:
            name = _clean_person_name(name_match.group(0))
            if name != "N/A":
                return name, title
    return "", ""
