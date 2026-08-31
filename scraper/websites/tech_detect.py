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


def _fallback_detect(html: str, headers: dict | None = None) -> list:
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


def _wappalyzer_detect(url: str, html: str, headers: dict | None = None) -> list:
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

    def detect(self, url: str, html: str, headers: dict | None = None) -> tuple:
        headers = headers or {}
        techs: list = []
        if self._use_wappalyzer:
            techs = _wappalyzer_detect(url, html, headers)
        if not techs:
            techs = _fallback_detect(html, headers)
        seen: set = set()
        ordered = []
        for t in techs:
            if t.lower() not in seen:
                seen.add(t.lower())
                ordered.append(t)
        return ", ".join(ordered), set(ordered)

    @staticmethod
    def classify(tech_set: set) -> dict:
        """Map a tech set to individual output columns (cms/analytics/etc.)."""
        low = {t.lower() for t in tech_set}

        def has(*names):
            return any(n.lower() in low for n in names)

        cms = ""
        for candidate in ("WordPress", "Shopify", "Wix", "Squarespace", "Webflow",
                          "Joomla", "Drupal", "Magento", "BigCommerce", "WooCommerce"):
            if has(candidate):
                cms = candidate
                break
        return {
            "cms": cms or "N/A",
            "analytics": "Google Analytics" if has("Google Analytics") else "N/A",
            "tag_manager": "Google Tag Manager" if has("Google Tag Manager") else "N/A",
            "meta_pixel": "detected" if has("Meta Pixel") else ("detected" if "facebook" in low else "N/A"),
            "ga4": "N/A",
            "gtm": "detected" if has("Google Tag Manager") else "N/A",
            "ssl": has("Cloudflare") and "yes" if has("Cloudflare") else "N/A",
        }


def detect_tech(html: str, scripts: list | None = None) -> dict:
    """Backward-compatible single-call detect over raw HTML + optional scripts.

    Returns keys: cms, analytics, tag_manager, ga4, meta_pixel, gtm,
    advertising, booking_system, chat_widget, ssl, tech_stack.
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
    }.items() if re.search(p, blob)), "N/A")

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

    return {
        "cms": cms or "N/A",
        "analytics": "Google Analytics" if "google-analytics" in blob or "gtag(" in blob else "N/A",
        "tag_manager": "detected" if gtm else "N/A",
        "ga4": "detected" if ga4 else "N/A",
        "meta_pixel": "detected" if meta_pixel else "N/A",
        "advertising": "yes" if advertising else "N/A",
        "booking_system": "yes" if booking else "N/A",
        "chat_widget": "yes" if chat else "N/A",
        "ssl": "yes" if "https://" in (html or "")[:200] else "N/A",
        "tech_stack": tech_stack or "N/A",
    }
