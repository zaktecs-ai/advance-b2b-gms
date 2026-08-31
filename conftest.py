"""Pytest bootstrap: make the `src` directory importable.

The project uses a src-layout, so `scraper` lives under `src/scraper`. This
conftest inserts `src` onto `sys.path` so tests can run with a plain
`python -m pytest tests/ -q` — no PYTHONPATH needed.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
