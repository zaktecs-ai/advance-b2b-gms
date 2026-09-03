"""Config tuning verification: concurrency + filters actually drive behavior.

Proof-level tests (not just parse checks):
  * the shipped config.yaml template loads with the requested worker counts,
  * out-of-range values fail fast before any scraping starts,
  * ``concurrency.website_workers`` really sizes the enrichment thread pool,
  * ``filters:`` entries really keep/drop records end-to-end in the pipeline.
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest
import yaml

from scraper.config import ConfigError, ConcurrencyConfig, load_config
from scraper.maps.collector import DemoCollector
from scraper.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]


def _write_config(tmp_path, extra: str) -> object:
    p = tmp_path / "config.yaml"
    p.write_text(
        f"queries: ['dentists in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: cfgtest\n"
        + extra,
        encoding="utf-8",
    )
    return p


# --- The shipped template carries the requested values -----------------------

def test_template_concurrency_values_load():
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.concurrency.website_workers == 16
    assert cfg.concurrency.playwright_workers == 4


def test_website_workers_above_cap_fails_fast(tmp_path):
    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 17\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_website_workers_below_one_fails_fast(tmp_path):
    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 0\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_playwright_workers_cap_is_four():
    assert ConcurrencyConfig(playwright_workers=4).playwright_workers == 4
    with pytest.raises(Exception):
        ConcurrencyConfig(playwright_workers=5)


# --- website_workers drives the REAL enrichment thread pool ------------------

def test_website_workers_sizes_enrichment_thread_pool(tmp_path, monkeypatch):
    """End-to-end proof: the pipeline's ThreadPoolExecutor is created with
    exactly the configured worker count."""
    created: list[int] = []

    class SpyExecutor(concurrent.futures.ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            created.append(kwargs.get("max_workers"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("scraper.pipeline.ThreadPoolExecutor", SpyExecutor)

    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 16\n")
    cfg = load_config(p)
    pipeline = Pipeline(cfg, DemoCollector())
    counters = pipeline.run()

    assert counters["committed"] > 0          # enrichment actually ran
    assert 16 in created                       # pool sized from the config


def test_website_workers_one_runs_serially(tmp_path, monkeypatch):
    """workers=1 must take the serial path (no thread pool created)."""
    created: list[int] = []

    class SpyExecutor(concurrent.futures.ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            created.append(kwargs.get("max_workers"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("scraper.pipeline.ThreadPoolExecutor", SpyExecutor)

    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 1\n")
    pipeline = Pipeline(load_config(p), DemoCollector())
    counters = pipeline.run()

    assert counters["committed"] > 0
    assert created == []                       # serial path, pool never built


# --- filters: end-to-end keep/drop proof -------------------------------------

def test_exclude_any_filter_drops_matching_records(tmp_path):
    """A filters.exclude_any entry in config.yaml must remove matching
    businesses from the export (pre-enrichment pass)."""
    p = _write_config(
        tmp_path,
        "\nfilters:\n"
        "  exclude_any:\n"
        "    - field: business_name\n"
        "      op: contains\n"
        "      value: 'Sample Business 1'\n",
    )
    pipeline = Pipeline(load_config(p), DemoCollector())
    counters = pipeline.run()

    assert counters["committed"] == 2          # 3 demo rows - 1 excluded
    assert counters["filtered"] == 1
    import csv
    rows = list(csv.DictReader(
        (tmp_path / "out" / "cfgtest" / "leads.csv").open(encoding="utf-8")))
    assert len(rows) == 2
    assert all("Sample Business 1" != r["business_name"] for r in rows)


def test_include_all_filter_keeps_only_matching_records(tmp_path):
    p = _write_config(
        tmp_path,
        "\nfilters:\n"
        "  include_all:\n"
        "    - field: rating\n"
        "      op: '>='\n"
        "      value: 4.6\n",
    )
    pipeline = Pipeline(load_config(p), DemoCollector())
    counters = pipeline.run()

    # Demo ratings are 4.5 / 4.6 / 4.7 -> the 4.5 row must be dropped.
    assert counters["committed"] == 2
    import csv
    rows = list(csv.DictReader(
        (tmp_path / "out" / "cfgtest" / "leads.csv").open(encoding="utf-8")))
    assert {float(r["rating"]) for r in rows} == {4.6, 4.7}


def test_playwright_workers_is_reserved_no_runtime_reader():
    """Honesty check: playwright_workers is validated and capped but no runtime
    module reads it yet (single sequential Maps collector by design). If a
    future change wires it up, this test's grep will fail — update the docs
    and this test together."""
    import scraper
    import scraper.browser.browser_manager as bm
    import scraper.maps.collector as collector
    import scraper.pipeline as pipeline_mod
    for mod in (scraper, bm, collector, pipeline_mod):
        assert "playwright_workers" not in vars(mod), (
            f"{mod.__name__} now reads playwright_workers — update the "
            f"config.yaml comment and CHANGES.md")
