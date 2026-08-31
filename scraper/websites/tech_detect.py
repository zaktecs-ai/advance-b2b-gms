"""Technology-stack detection: wappalyzer (if available) + built-in regex.

The maintained ``wappalyzer-python3`` library is preferred when installed, with
a built-in regex fallback covering the most common technologies so the engine
degrades gracefully. Results aggregate into per-column tech fields plus a
single readable tech_stack string.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Lightweight fallback signatures: tech name -> list of regex patterns.
_FALLBACK_SIGNATURES: list = [
    ("WordPress",       [r"wp-content", r"wp-includes", r"wp-json"]),
    ("Shopify",         [r"cdn\.shopify\.com", r"myshopify\.com"]),
    ("Wix",             [r"static\.wixstatic\.com", r"wix\.com/"]),
    ("Squarespace",     [r"squarespace\.com", r"static1\.squarespace\.com"]),
    ("Webflow",         [r"webflow\.com", r"webflow\.js"]),
    ("Elementor",       [r"elementor"]),
    ("Joomla",          [r"joomla"]),
    ("Drupal",          [r"drupal"]),
    ("Google Tag Manager", [r"googletagmanager\.com"]),
    ("Google Analytics",  [r"google-analytics\.com", r"gtag\("]),
    ("Cloudflare",      [r"cloudflare", r"cf-ray"]),
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
    ("HubSpot",         [r"hubspot", r"js\.hs-scripts\.com"]),
    ("Tawk.to",         [r"tawk\.to"]),
    ("Intercom",        [r"intercom"]),
    ("Calendly",        [r"calendly\.com"]),
    ("Acuity Scheduling", [r"acuityscheduling\.com"]),
    ("WooCommerce",     [r"woocommerce"]),
    ("BigCommerce",     [r"bigcommerce"]),
    ("Magento",         [r"magento"]),
    ("Django",          [r"csrftoken", r"django"]),
    ("Ruby on Rails",   [r"csrf-param", r"rails"]),
]


def _fallback_detect(html: str, headers: dict[str, str] | None = None) -> list[str]:
    text = html or ""
    hdr = " ".join(f"{k}: {v}" for k, v in (headers or {}).items())
    hay = text + " " + hdr
    found: list = []
    for name, patterns in _FALLBACK_SIGNATURES:
        for pat in patterns:
            if re.search(pat, hay, re.I):
                found.append(name)
                break
    return found


def _wappalyzer_detect(url: str, html: str, headers: dict[str, str] | None = None) -> list[str]:
    try:
        from Wappalyzer import Wappalyzer, WebPage  # type: ignore
        analyzer = Wappalyzer.latest()
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


def detect_tech(html: str, scripts: list | None = None) -> dict:
    """Backward-compatible single-call detect over raw HTML + optional scripts.

    Returns detected keys from: cms, analytics, tag_manager, ga4, meta_pixel,
    gtm, advertising, booking_system, chat_widget, ssl, tech_stack. Missing
    detections are omitted so callers can apply their own missing-value policy.
    """
    detector = TechDetector(use_wappalyzer=False)
    tech_stack, _ = detector.detect("", html or "")
    blob = (html or "").lower() + "\n" + "\n".join(scripts or []).lower()

    cms = next((k for k, p in {
        "WordPress": r"wp-content|wp-includes|wordpress",
        "Wix": r"wix\.com|wixstatic",
        "Squarespace": r"squarespace",
        "Shopify": r"cdn\.shopify\.com|shopify",
        "Webflow": r"webflow",
        "Joomla": r"joomla",
        "Drupal": r"drupal",
        "Magento": r"magento",
    }.items() if re.search(p, blob)), None)

    gtm = bool(re.search(r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]+", blob))
    ga4 = bool(re.search(r"gtag\(|G-[A-Z0-9]{6,}", blob))
    meta_pixel = bool(re.search(r"connect\.facebook\.net|facebook\.com/tr|fbq\(", blob))
    advertising = meta_pixel or bool(re.search(r"doubleclick|adsbygoogle|googlesyndication", blob))

    booking = any(re.search(p, blob) for p in [
        "calendly.com", "acuityscheduling.com", "booksy.com", "mindbodyonline.com",
        "vagaro.com", "fresha.com", "setmore.com", "appointy.com",
        "youcanbook.me", "simplybook.me", "schedulicity.com", "square.site"])
    chat = any(re.search(p, blob) for p in [
        "tawk.to", "intercom", "drift.com", "livechatinc.com", "zopim",
        "crisp.chat", "hubspot.com", "freshchat", "zendesk"])

    detected: dict[str, str] = {}
    if cms:
        detected["cms"] = cms
    if "google-analytics" in blob or "gtag(" in blob:
        detected["analytics"] = "Google Analytics"
    if gtm:
        detected["tag_manager"] = "detected"
        detected["gtm"] = "detected"
    if ga4:
        detected["ga4"] = "detected"
    if meta_pixel:
        detected["meta_pixel"] = "detected"
    if advertising:
        detected["advertising"] = "yes"
    if booking:
        detected["booking_system"] = "yes"
    if chat:
        detected["chat_widget"] = "yes"
    if "https://" in (html or "")[:200]:
        detected["ssl"] = "yes"
    if tech_stack:
        detected["tech_stack"] = tech_stack
    return detected
