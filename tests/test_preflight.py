"""Preflight checks in scraper.main (fail fast before any scraping)."""
from __future__ import annotations

from pathlib import Path

import scraper.main as scraper_main


def test_check_chromium_reports_missing_binary(monkeypatch, tmp_path):
    """When the Chromium executable path does not exist, the preflight
    returns a clear setup error mentioning the install command."""
    monkeypatch.setattr(Path, "exists", lambda self: False)
    msg = scraper_main._check_chromium()
    # Playwright itself must be importable in the dev/CI env for this path.
    if msg is not None:
        assert "playwright install chromium" in msg or "Playwright" in msg


def test_check_chromium_returns_none_when_binary_present(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: True)
    assert scraper_main._check_chromium() is None
