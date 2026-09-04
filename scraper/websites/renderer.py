"""Playwright rendering fallback for JS-only websites (pooled).

This module is a thin facade over :class:`scraper.websites.browser_pool.
BrowserPool`. The pool keeps ``playwright_workers`` persistent Chromium
instances alive for the whole run and reuses them across sites — a brand-new
Chromium is NOT launched per website any more. See browser_pool.py for the
worker implementation.
"""
from __future__ import annotations

from .browser_pool import BrowserPool


class PlaywrightRenderer:
    def __init__(self, navigation_timeout_seconds: float = 30.0,
                 pool_size: int = 2):
        self._pool = BrowserPool(size=pool_size,
                                 navigation_timeout_seconds=navigation_timeout_seconds)

    def render(self, url: str) -> str:
        """Return rendered HTML for ``url`` ('' on any failure)."""
        return self._pool.render(url)

    def close(self) -> None:
        try:
            self._pool.close()
        except Exception:
            pass

