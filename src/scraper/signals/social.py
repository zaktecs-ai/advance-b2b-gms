"""Social profile detection — per-platform, domain-anchored patterns.

A URL is matched to exactly one platform by its host, so a Facebook URL can
never land in the Instagram column.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Platform -> host regex (domain-anchored). Order matters little because we
# check host equality/family directly per platform.
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
    """Classify a list of URLs into per-platform profile dict."""
    result = {k: "N/A" for k in _PLATFORM_HOSTS}
    for u in urls:
        p = platform_for_url(u)
        if p and result[p] == "N/A":
            result[p] = u
    return result
