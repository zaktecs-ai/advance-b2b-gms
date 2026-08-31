#!/usr/bin/env python3
"""Entry shim so `python main.py` works from the repo root.

This matches the Fiverr-Automation engine's command style: activate the venv,
then run `python main.py` to start a live scrape, or `python main.py --demo`
for the offline test. It simply forwards to `scraper.main` (the real entry).
"""
from scraper.main import main

if __name__ == "__main__":
    raise SystemExit(main())
