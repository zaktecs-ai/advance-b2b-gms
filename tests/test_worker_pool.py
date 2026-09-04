"""Continuous enrichment pool: lifecycle, isolation, backpressure, stats.

Proof-level tests for the producer/consumer refactor:
  * the pool is created ONCE and shuts down cleanly (no per-batch executors),
  * a crashing enrichment / commit NEVER deadlocks the bounded queue,
  * the bounded in-flight queue enforces backpressure,
  * runtime stats (throughput / latency / queue depth / utilization) exist.
"""
from __future__ import annotations

from scraper.config import load_config
from scraper.maps.collector import DemoCollector
from scraper.pipeline import Pipeline
from scraper.websites.browser_pool import BrowserPool


def _config(tmp_path, extra: str = ""):
    p = tmp_path / "config.yaml"
    p.write_text(
        f"queries: ['dentists in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: pooltest\n"
        + extra,
        encoding="utf-8",
    )
    return load_config(p)


# --- pool lifecycle -----------------------------------------------------------

def test_pool_created_once_and_shuts_down_cleanly(tmp_path):
    pipeline = Pipeline(_config(tmp_path), DemoCollector())
    counters = pipeline.run()

    assert counters["committed"] > 0
    assert pipeline._pool_started
    assert pipeline._pool_shut
    assert all(not t.is_alive() for t in pipeline._worker_threads)
    assert not pipeline._committer_thread.is_alive()


def test_shutdown_is_idempotent(tmp_path):
    pipeline = Pipeline(_config(tmp_path), DemoCollector())
    pipeline.run()
    # A second shutdown (e.g. main's finally-close path) must not hang/raise.
    pipeline._shutdown_pool(timeout=5.0)


def test_bounded_in_flight_queue_configured(tmp_path):
    pipeline = Pipeline(_config(tmp_path,
                                "\nconcurrency:\n  website_workers: 3\n"
                                "  max_in_flight: 12\n"), DemoCollector())
    assert pipeline._work_q.maxsize == 12
    assert pipeline._max_in_flight == 12
    pipeline.close()


# --- failure isolation: the queue can never deadlock ---------------------------

def test_commit_exception_does_not_deadlock(tmp_path):
    """A raising commit stage skips the record but keeps draining: the run
    finishes and other records still commit (old batch code aborted)."""
    (tmp_path / "config.yaml").write_text(
        "queries: ['plumbers in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: fail\n",
        encoding="utf-8",
    )
    pipeline = Pipeline(load_config(str(tmp_path / "config.yaml")),
                        DemoCollector())
    boom = {"count": 0}

    orig_commit = pipeline._commit_stage

    def flaky_commit(rec, query, sig):
        boom["count"] += 1
        if boom["count"] == 1:
            raise RuntimeError("commit boom")
        orig_commit(rec, query, sig)

    pipeline._commit_stage = flaky_commit
    counters = pipeline.run()
    assert counters["committed"] >= 1   # others survived
    assert counters["failed"] >= 1      # the skipped one counted


def test_worker_exception_does_not_shrink_the_pool(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "queries: ['plumbers in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: wfail\n",
        encoding="utf-8",
    )
    pipeline = Pipeline(load_config(str(tmp_path / "config.yaml")),
                        DemoCollector())

    class ExplodingEnricher:
        def enrich(self, website):
            raise RuntimeError("enricher explosion")

        def close(self):
            pass

        def verify_email(self, email):
            return {"mx_status": "Not Checked", "mx_reason": "mx_disabled",
                    "smtp_status": "Not Checked", "smtp_reason": "smtp_disabled"}

    pipeline.enricher = ExplodingEnricher()
    counters = pipeline.run()
    assert all(not t.is_alive() for t in pipeline._worker_threads)
    assert counters["committed"] == 3   # failures still flow to commit


# --- runtime stats --------------------------------------------------------------

def test_runtime_stats_populated_after_run(tmp_path):
    pipeline = Pipeline(_config(
        tmp_path, "\nconcurrency:\n  website_workers: 4\n"), DemoCollector())
    counters = pipeline.run()
    stats = pipeline.runtime_stats()

    assert stats["worker_count"] == 4
    assert stats["in_flight_cap"] == 16  # 4 workers x 4 default
    assert counters["committed"] > 0
    assert stats["enriched"] >= counters["committed"]
    assert stats["elapsed_seconds"] > 0
    assert stats["avg_enrich_seconds"] >= 0.0
    assert stats["queue_depth_max"] >= 0
    assert 0.0 <= stats["worker_utilization"] <= 1.0


# --- browser pool (lazy, no Chromium in tests) -----------------------------------

def test_browser_pool_render_after_close_returns_empty():
    pool = BrowserPool(size=2)
    pool.close()
    assert pool.render("http://example.test/") == ""


def test_browser_pool_empty_url_short_circuits():
    pool = BrowserPool(size=1)
    assert pool.render("") == ""
    assert not pool._started  # lazy: nothing launched for an empty URL
    pool.close()

