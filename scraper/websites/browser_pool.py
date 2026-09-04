"""Persistent Playwright browser-worker pool for JS-required websites.

The enrichment path is HTTP-first; this pool only serves pages classified
``JS_REQUIRED``. Key properties:

- SMALL by design: ``concurrency.playwright_workers`` (2-4) dedicated browser
  workers, each owning ONE persistent Chromium instance that is reused across
  sites (no per-site browser launch, which dominated fallback latency).
- Lazy start: no Chromium exists until the first JS_REQUIRED site appears.
- Crash isolation: if a render crashes the browser, the worker discards it and
  relaunches on the next request; the failed site simply returns ''.
- Queue-based: renders are submitted as futures to a shared work queue so a
  slow site never blocks an HTTP worker's other work — the HTTP worker just
  awaits its future.
"""
from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future

log = logging.getLogger(__name__)


class BrowserPool:
    def __init__(self, size: int = 2, navigation_timeout_seconds: float = 30.0,
                 settle_ms: int = 500):
        self._size = max(1, min(int(size), 8))
        self._nav_timeout_ms = int(max(1.0, navigation_timeout_seconds) * 1000)
        self._settle_ms = settle_ms
        self._jobs: "queue.Queue[tuple[str, Future] | None]" = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._start_lock = threading.Lock()
        self._started = False
        self._closed = False

    # -- public API ----------------------------------------------------------
    def render(self, url: str) -> str:
        """Render ``url`` in a pooled browser; returns HTML ('' on failure)."""
        if self._closed or not url:
            return ""
        self._ensure_started()
        fut: Future = Future()
        self._jobs.put((url, fut))
        try:
            # Generous ceiling beyond the page navigation timeout so a hung
            # render can't block an HTTP worker forever.
            return fut.result(timeout=(self._nav_timeout_ms / 1000.0) + 60.0)
        except Exception as e:  # noqa: BLE001 — render is best-effort
            log.debug("browser pool render failed for %s: %s", url, e)
            return ""

    def close(self) -> None:
        """Stop all workers (idempotent); each worker closes its browser."""
        if self._closed:
            return
        self._closed = True
        for _ in self._threads:
            self._jobs.put(None)
        for t in self._threads:
            t.join(timeout=30.0)
        self._threads.clear()

    # -- internals -----------------------------------------------------------
    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if self._started or self._closed:
                return
            for i in range(self._size):
                t = threading.Thread(target=self._worker_loop,
                                     name=f"browser-pool-{i}", daemon=True)
                t.start()
                self._threads.append(t)
            self._started = True
            log.info("browser pool started with %d worker(s)", self._size)

    def _worker_loop(self) -> None:
        pw = None
        browser = None
        while True:
            job = self._jobs.get()
            if job is None:
                break
            url, fut = job
            html = ""
            try:
                if pw is None:
                    from playwright.sync_api import sync_playwright
                    pw = sync_playwright().start()
                if browser is None or not browser.is_connected():
                    browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                try:
                    page.goto(url, timeout=self._nav_timeout_ms,
                              wait_until="domcontentloaded")
                    page.wait_for_timeout(self._settle_ms)
                    html = page.content()
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
                fut.set_result(html)
            except Exception as e:  # noqa: BLE001 — render is best-effort
                log.debug("playwright render failed for %s: %s", url, e)
                # A crashed browser must not poison later renders: discard it
                # and relaunch on the next request.
                try:
                    if browser is not None:
                        browser.close()
                except Exception:
                    pass
                browser = None
                if not fut.done():
                    fut.set_result("")
        # Shutdown: close our persistent browser + playwright driver.
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass
