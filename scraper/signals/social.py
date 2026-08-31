"""Social profile detection — per-platform, domain-anchored patterns.

A URL is matched to exactly one platform by its host, so a Facebook URL can
never land in the Instagram column.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

_PLATFORM_HOSTS = {
    "facebook": r"(^|\.)facebook\.com$",
    "instagram": r"(^|\.)instagram\.com$",
    "linkedin": r"(^|\.)linkedin\.com$",
    "youtube": r"(^|\.)youtube\.com$|(^|\.)youtu\.be$",
    "twitter_x": r"(^|\.)twitter\.com$|(^|\.)x\.com$",
    "tiktok": r"(^|\.)tiktok\.com$",
    "pinterest": r"(^|\.)pinterest\.com$",
    "github": r"(^|\.)github\.com$",
    "snapchat": r"(^|\.)snapchat\.com$",
}


def platform_for_url(url: str) -> str | None:
    """Return the platform key for a URL, or None if not a social profile."""
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    for platform, pattern in _PLATFORM_HOSTS.items():
        if re.search(pattern, host):
            return platform
    return None


def detect_social(urls: list[str]) -> dict[str, str]:
    """Classify a list of URLs into a per-platform profile dict."""
    result = {k: "N/A" for k in _PLATFORM_HOSTS}
    for u in urls:
        p = platform_for_url(u)
        if p and result[p] == "N/A":
            result[p] = u
    return result


# ---------------------------------------------------------------------------
# Reverse: collect social URLs found inside an HTML page.
# ---------------------------------------------------------------------------

def social_urls_from_html(html: str, base_url: str = "") -> list[str]:
    """Return social-profile URLs discovered in an HTML page's anchors."""
    from bs4 import BeautifulSoup
    if not html:
        return []
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        abs_url = urljoin(base_url, href) if base_url else href
        if platform_for_url(abs_url):
            out.append(abs_url)
    return out
