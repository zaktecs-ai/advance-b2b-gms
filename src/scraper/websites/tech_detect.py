"""Lightweight technology-stack detection from raw HTML / scripts."""
from __future__ import annotations

import re

_CMS_PATTERNS = {
    "WordPress": r"wp-content|wp-includes|wordpress",
    "Wix": r"wix\.com|wixstatic",
    "Squarespace": r"squarespace",
    "Shopify": r"cdn\.shopify\.com|shopify",
    "Webflow": r"webflow",
    "Joomla": r"joomla",
    "Drupal": r"drupal",
    "Magento": r"magento",
}

_ANALYTICS_PATTERNS = {
    "Google Analytics": r"google-analytics\.com|analytics\.js|gtag\(",
    "Matomo": r"matomo",
    "Plausible": r"plausible\.io",
    "Fathom": r"fathom",
    "Cloudflare": r"cloudflare",
}

_BOOKING_PATTERNS = [
    "calendly.com", "acuityscheduling.com", "booksy.com", "mindbodyonline.com",
    "vagaro.com", "fresha.com", "setmore.com", "appointy.com",
    "youcanbook.me", "simplybook.me", "schedulicity.com", "square.site",
]

_CHAT_PATTERNS = [
    "tawk.to", "intercom", "drift.com", "livechatinc.com", "zopim",
    "crisp.chat", "hubspot.com", "freshchat", "zendesk",
]


def detect_tech(html: str, scripts: list[str] | None = None) -> dict:
    """Return a dict of detected technologies/signals."""
    h = (html or "").lower()
    scripts_blob = "\n".join(scripts or [])
    blob = h + "\n" + scripts_blob.lower()

    cms = next((k for k, p in _CMS_PATTERNS.items() if re.search(p, blob)), "")
    analytics = [k for k, p in _ANALYTICS_PATTERNS.items() if re.search(p, blob)]
    booking = any(re.search(p, blob) for p in _BOOKING_PATTERNS)
    chat = any(re.search(p, blob) for p in _CHAT_PATTERNS)

    ga4 = bool(re.search(r"gtag\(|G-[A-Z0-9]{6,}", blob))
    gtm = bool(re.search(r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]+", blob))
    meta_pixel = bool(re.search(r"connect\.facebook\.net|facebook\.com/tr|fbq\(", blob))
    advertising = meta_pixel or bool(re.search(r"doubleclick|adsbygoogle|googlesyndication", blob))

    return {
        "cms": cms or "N/A",
        "analytics": ",".join(analytics) if analytics else "N/A",
        "tag_manager": "detected" if gtm else "N/A",
        "ga4": "detected" if ga4 else "N/A",
        "meta_pixel": "detected" if meta_pixel else "N/A",
        "advertising": "yes" if advertising else "N/A",
        "booking_system": "yes" if booking else "N/A",
        "chat_widget": "yes" if chat else "N/A",
        "ssl": "yes" if (html or "").startswith("https") or "https://" in (html or "")[:200] else "N/A",
    }
