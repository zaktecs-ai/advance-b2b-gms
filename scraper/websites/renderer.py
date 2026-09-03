"""Playwright-based rendering fallback for JS-only websites.

Used ONLY when ``website.enable_playwright_fallback`` is true and the httpx
fetch classified the page as ``JS_REQUIRED``. Thread-local driver instances
keep the sync Playwright API safe under the enrichment thread pool; each
render launches a throwaway Chromium (crash-isolated) and respects
``website.page_navigation_timeout_seconds``.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


class PlaywrightRenderer:
    def __init__(self, navigation_timeout_seconds: float = 30.0):
        self._nav_timeout_ms = int(max(1.0, navigation_timeout_seconds) * 1000)
        self._local = threading.local()

    def render(self, url: str) -> str:
        """Return rendered HTML for ``url`` ('' on any failure)."""
        pw = getattr(self._local, "pw", None)
        if pw is None:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            self._local.pw = pw
        browser = None
        try:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=self._nav_timeout_ms,
                      wait_until="domcontentloaded")
            page.wait_for_timeout(500)  # let inline scripts settle
            return page.content()
        except Exception as e:  # noqa: BLE001 — render is best-effort
            log.debug("playwright render failed for %s: %s", url, e)
            return ""
        finally:
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
