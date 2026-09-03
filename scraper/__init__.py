"""Advance B2B GMS — a clean-room, most-capable Google Maps B2B lead scraper.

This package is a self-contained, standalone project. It shares only general
domain knowledge with other open-source scrapers; nothing here is copied from,
imported from, or linked to any other repository.
"""
import sys

__version__ = "1.0.0"

# G10: fail fast with an actionable message instead of a cryptic SyntaxError
# deep in a regex — the codebase relies on Python 3.11+ features (e.g. the
# scoped inline flag syntax `(?-i:…)` in signals/detector.py).
if sys.version_info < (3, 11):  # pragma: no cover - trivial guard
    raise SystemExit(
        f"advance-b2b-gms requires Python 3.11+ (found "
        f"{sys.version_info.major}.{sys.version_info.minor}). Reinstall with "
        f"`setup.sh` or point the venv at a newer interpreter.")
