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
    assert cfg.concurrency.website_workers == 20
    assert cfg.concurrency.playwright_workers == 2
    assert cfg.concurrency.per_domain_concurrency == 1
    assert cfg.concurrency.respect_retry_after is True


def test_website_workers_above_cap_fails_fast(tmp_path):
    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 33\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_website_workers_below_one_fails_fast(tmp_path):
    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 0\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_website_workers_25_in_range(tmp_path):
    """The documented 16-25 operating window validates cleanly."""
    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 25\n")
    assert load_config(p).concurrency.website_workers == 25


def test_per_domain_concurrency_bounds(tmp_path):
    p = _write_config(tmp_path, "\nconcurrency:\n  per_domain_concurrency: 0\n")
    with pytest.raises(ConfigError):
        load_config(p)
    p = _write_config(tmp_path, "\nconcurrency:\n  per_domain_concurrency: 9\n")
    with pytest.raises(ConfigError):
        load_config(p)
    p = _write_config(tmp_path, "\nconcurrency:\n  per_domain_concurrency: 2\n")
    assert load_config(p).concurrency.per_domain_concurrency == 2


def test_playwright_workers_cap_is_four():
    assert ConcurrencyConfig(playwright_workers=4).playwright_workers == 4
    with pytest.raises(Exception):
        ConcurrencyConfig(playwright_workers=5)


# --- website_workers drives the LONG-LIVED enrichment worker pool ------------

def test_website_workers_sizes_enrichment_worker_pool(tmp_path):
    """End-to-end proof: the pipeline's long-lived pool has exactly the
    configured worker count and the run drains cleanly."""
    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 4\n"
                                 "  max_in_flight: 8\n")
    cfg = load_config(p)
    pipeline = Pipeline(cfg, DemoCollector())
    counters = pipeline.run()

    assert counters["committed"] > 0          # enrichment actually ran
    assert pipeline._worker_count == 4        # pool sized from the config
    assert pipeline._max_in_flight == 8       # bounded queue honors config
    # After run() the pool is fully shut down (workers joined).
    assert all(not t.is_alive() for t in pipeline._worker_threads)
    assert (pipeline._committer_thread is None
            or not pipeline._committer_thread.is_alive())


def test_worker_count_one_still_uses_the_continuous_pool(tmp_path):
    """workers=1 runs through the same producer/consumer machinery with a
    single worker thread (no special-case serial path to maintain)."""
    p = _write_config(tmp_path, "\nconcurrency:\n  website_workers: 1\n")
    pipeline = Pipeline(load_config(p), DemoCollector())
    counters = pipeline.run()

    assert counters["committed"] > 0
    assert pipeline._worker_count == 1
    assert len(pipeline._worker_threads) == 1


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


def test_playwright_workers_is_wired_to_the_browser_pool(tmp_path):
    """playwright_workers is now CONSUMED: the Pipeline passes it to the
    Enricher as the persistent Chromium browser pool size (JS-required
    fallback). The Maps collector remains single-browser by design."""
    import inspect

    import scraper.pipeline as pipeline_mod
    src = inspect.getsource(pipeline_mod)
    assert "playwright_pool_size=cc.playwright_workers" in src

    p = _write_config(tmp_path, "\nconcurrency:\n  playwright_workers: 3\n")
    pipeline = Pipeline(load_config(p), DemoCollector())
    # The renderer pool must be sized from the config (no browser launched
    # until the first JS_REQUIRED site — lazy start).
    renderer = pipeline.enricher._renderer
    assert renderer is not None
    assert renderer._pool._size == 3
    assert not renderer._pool._started  # lazy: no Chromium yet
    pipeline.close()
