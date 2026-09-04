"""Technology-stack detection: wappalyzer (if available) + built-in regex.

The maintained ``wappalyzer-python3`` library is preferred when installed, with
a built-in regex fallback covering the most common technologies so the engine
degrades gracefully. Results aggregate into per-column tech fields plus a
single readable tech_stack string.
"""
from __future__ import annotations

import logging
import re
import threading

log = logging.getLogger(__name__)

# Wappalyzer.latest() parses a ~1MB technologies.json EVERY call — the single
# most expensive repeated operation per site. One process-wide analyzer is
# shared (thread-safe: analyze() is read-only) and built lazily once.
_WAPPALYZER_LOCK = threading.Lock()
_WAPPALYZER = None

# Lightweight fallback signatures: tech name -> list of regex patterns.
_FALLBACK_SIGNATURES: list = [
    # Markup/header-anchored patterns only. Prose-only patterns (bare `gtag(`,
    # bare `joomla`/`django`/`cloudflare` brand mentions) were removed — they
    # fired on body copy and contradicted SignalDetector (F20).
    ("WordPress",       [r"wp-content", r"wp-includes", r"wp-json"]),
    ("Shopify",         [r"cdn\.shopify\.com", r"myshopify\.com"]),
    ("Wix",             [r"static\.wixstatic\.com", r"wix\.com/"]),
    ("Squarespace",     [r"squarespace\.com", r"static1\.squarespace\.com"]),
    ("Webflow",         [r"webflow\.com", r"webflow\.js"]),
    ("Elementor",       [r"elementor"]),
    ("Google Tag Manager", [r"googletagmanager\.com"]),
    ("Google Analytics",  [r"google-analytics\.com"]),
    ("Cloudflare",      [r"cf-ray"]),
    ("React",           [r"react(?:\.min)?\.js", r"__REACT_DEVTOOLS"]),
    ("Vue.js",          [r"vue(?:\.min)?\.js", r"__VUE__"]),
    ("Angular",         [r"angular(?:\.min)?\.js", r"ng-version"]),
    ("Next.js",         [r"__NEXT_DATA__", r"/_next/static"]),
    ("Bootstrap",       [r"bootstrap(?:\.min)?\.css", r"bootstrap(?:\.min)?\.js"]),
    ("jQuery",          [r"jquery(?:\.min)?\.js"]),
    ("Google Fonts",    [r"fonts\.googleapis\.com"]),
    ("Font Awesome",    [r"font-awesome", r"fontawesome"]),
    ("Stripe",          [r"js\.stripe\.com"]),
    ("PayPal",          [r"paypal\.com", r"paypalobjects\.com"]),
    ("HubSpot",         [r"js\.hs-scripts\.com"]),
    ("Tawk.to",         [r"tawk\.to"]),
    ("Intercom",        [r"intercom"]),
    ("Calendly",        [r"calendly\.com"]),
    ("Acuity Scheduling", [r"acuityscheduling\.com"]),
    ("WooCommerce",     [r"woocommerce"]),
    ("BigCommerce",     [r"bigcommerce"]),
    ("Magento",         [r"magento"]),
    ("Django",          [r"csrftoken"]),
    ("Ruby on Rails",   [r"csrf-param"]),
]


def _fallback_detect(html: str, headers: dict[str, str] | None = None) -> list[str]:
    """Detect tech from markup artifacts + headers ONLY, never body prose.

    Scanning raw HTML text made a blog sentence ("We migrated from Django and
    love Cloudflare") flip tech detections (F20). Only script src / link href /
    meta content and response headers are evidence.
    """
    from bs4 import BeautifulSoup

    hdr = " ".join(f"{k}: {v}" for k, v in (headers or {}).items())
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        soup = None
    artifacts: list[str] = []
    if soup is not None:
        artifacts += [s.get("src", "") for s in soup.find_all("script")]
        artifacts += [l.get("href", "") for l in soup.find_all("link")]
        artifacts += [m.get("content", "") for m in soup.find_all("meta")]
    hay = "\n".join(a for a in artifacts if a) + " " + hdr
    return [name for name, patterns in _FALLBACK_SIGNATURES
            if any(re.search(p, hay, re.I) for p in patterns)]


def _get_wappalyzer():
    """Return the process-wide cached Wappalyzer (built once, lazily)."""
    global _WAPPALYZER
    if _WAPPALYZER is not None:
        return _WAPPALYZER
    from Wappalyzer import Wappalyzer  # type: ignore

    with _WAPPALYZER_LOCK:
        if _WAPPALYZER is None:
            _WAPPALYZER = Wappalyzer.latest()
    return _WAPPALYZER


def _wappalyzer_detect(url: str, html: str, headers: dict[str, str] | None = None) -> list[str]:
    try:
        from Wappalyzer import WebPage  # type: ignore

        analyzer = _get_wappalyzer()
        wp = WebPage(url=url, html=html, headers=headers or {})
        return list(analyzer.analyze(wp))
    except Exception as e:  # pragma: no cover — library/env dependent
        log.debug("wappalyzer unavailable or failed: %s", e)
        return []


class TechDetector:
    """Unified tech detection: prefer wappalyzer, fall back to regex."""

    def __init__(self, use_wappalyzer: bool = True):
        self._use_wappalyzer = use_wappalyzer

    def detect(self, url: str, html: str, headers: dict[str, str] | None = None) -> tuple[str, set[str]]:
        headers = headers or {}
        techs: list = []
        if self._use_wappalyzer:
            techs = _wappalyzer_detect(url, html, headers)
        if not techs:
            techs = _fallback_detect(html, headers)
        seen: set = set()
        ordered = []
        for raw_tech in techs:
            tech = str(raw_tech).strip()
            if tech and tech.lower() not in seen:
                seen.add(tech.lower())
                ordered.append(tech)
        return ", ".join(ordered), set(ordered)

    @staticmethod
    def classify(tech_set: set[str]) -> dict[str, str]:
        """Map detected technology names to only the fields they support.

        Missing detections are omitted rather than manufactured as ``N/A``;
        the pipeline applies the output contract's missing-value policy.
        """
        low = {str(t).lower() for t in (tech_set or set())}

        def has(*names: str) -> bool:
            return any(name.lower() in low for name in names)

        classified: dict[str, str] = {}
        for candidate in (
            "WordPress", "Shopify", "Wix", "Squarespace", "Webflow", "Joomla",
            "Drupal", "Magento", "BigCommerce", "WooCommerce",
        ):
            if has(candidate):
                classified["cms"] = candidate
                break
        if has("Google Analytics", "Google Analytics 4"):
            classified["analytics"] = "Google Analytics"
        if has("Google Tag Manager"):
            classified["tag_manager"] = "Google Tag Manager"
            classified["gtm"] = "detected"
        if has("Google Analytics 4", "GA4"):
            classified["ga4"] = "detected"
        if has("Meta Pixel"):
            classified["meta_pixel"] = "detected"
        return classified
