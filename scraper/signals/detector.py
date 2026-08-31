"""Config-driven business signal detectors + decision-maker extraction.

Signals run over a page's collected context (normalized text, URLs, scripts,
structured data, tech). Custom signals can be added in YAML without editing
code. Decision-maker extraction (off by default) pulls person + title from
about/team/LinkedIn pages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PageContext:
    text: str = ""
    html: str = ""
    url: str = ""
    urls: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    structured: str = ""


# Built-in lead-gen signals.
_SIGNAL_DEFS: dict[str, tuple[str, ...]] = {
    "pricing": ("pricing", "price per", "starting at", "rate card", "quote online"),
    "financing": ("financing", "payment plan", "0% interest", "monthly installments"),
    "licensed_insured": ("licensed", "insured", "bonded", "certified"),
    "established": ("family owned", "established in", "since 19", "since 20", "years of experience"),
    "portfolio": ("portfolio", "case study", "our work", "projects", "gallery"),
    "mobile_service": ("mobile service", "we come to you", "on-site", "house calls"),
    "membership": ("membership", "subscribe", "join our", "loyalty program"),
}


def detect_signals(ctx: PageContext, custom: dict | None = None) -> dict[str, tuple[bool, str | None]]:
    """Return {signal_name: (detected, evidence)} for built-in + custom."""
    blob = "\n".join([ctx.text or "", ctx.html or "",
                      "\n".join(ctx.urls or []), ctx.structured or ""]).lower()
    results: dict[str, tuple[bool, str | None]] = {}
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


# -- Decision-maker extraction ----------------------------------------------

_TITLE_PATTERNS = [
    r"\b(CEO|Chief Executive Officer|Founder|Co-Founder|Owner|President|"
    r"Managing Director|Principal|Partner|Manager|Director|Proprietor)\b",
]

_NAME_TITLE_RE = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*[,\-–—]\s*"
    r"(CEO|Chief Executive Officer|Founder|Co-Founder|Owner|President|"
    r"Managing Director|Principal|Partner|Director|Proprietor|Manager)",
    re.I,
)


def extract_decision_maker(text: str) -> tuple[str, str]:
    """Return (name, title) from an about/team page, or ('', '')"""
    if not text:
        return "", ""
    m = _NAME_TITLE_RE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # Fallback: a title line then a nearby capitalized name.
    tm = re.search(_TITLE_PATTERNS[0], text, re.I)
    if tm:
        title = tm.group(1)
        # Look for a capitalized name before the title.
        prefix = text[: tm.start()]
        names = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", prefix)
        if names:
            return names[-1].strip(), title
    return "", ""
