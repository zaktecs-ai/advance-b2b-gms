"""Config wiring audit: EVERY leaf key in config.yaml must be operational.

Constraint: "no variable should be merely decorative". This test parses the
shipped config.yaml and requires each leaf key name to appear in the scraper
source OUTSIDE config.py (where keys are only declared). If you add a config
key, you must also wire it — or this test fails.
"""
from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Key names too generic for a source-grep to be meaningful (they are covered
# by behavior tests elsewhere, e.g. tests/test_concurrency.py).
_GENERIC_KEYS = {"enabled", "level", "custom", "keywords", "column", "match",
                 "params"}


def _leaf_keys(d, prefix=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _leaf_keys(v, prefix + k + ".")
        else:
            yield prefix + k


def test_every_config_key_is_operational():
    template = yaml.safe_load(
        (ROOT / "config.yaml").read_text(encoding="utf-8"))
    source = ""
    for p in (ROOT / "scraper").rglob("*.py"):
        if p.name == "config.py":
            continue  # declarations only — usage must live elsewhere
        source += p.read_text(encoding="utf-8")
    dead = []
    for key in _leaf_keys(template):
        name = key.split(".")[-1]
        if name in _GENERIC_KEYS:
            continue
        if not re.search(rf"\b{re.escape(name)}\b", source):
            dead.append(key)
    assert not dead, f"decorative (unread) config keys: {dead}"


def test_every_config_section_maps_to_model():
    # F25 guard, extended to the new sections.
    import sys
    import scraper.config as C
    template = yaml.safe_load(
        (ROOT / "config.yaml").read_text(encoding="utf-8"))
    model_fields = set(C.AppConfig.model_fields)
    for k in template:
        assert k in model_fields, f"dead config section: {k}"
    assert "signals" in model_fields and "summary" in model_fields
    assert sys.version_info >= (3, 11)  # sanity: guard from scraper/__init__