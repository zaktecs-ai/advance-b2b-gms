"""Benchmark harness: controlled mock-website workload through the pipeline.

Verifies the benchmark measures what it claims: every site flows through
discovery -> queue -> enrichment -> commit exactly once, and the reported
metrics reflect the run.
"""
from __future__ import annotations

import scraper.benchmark as bench


def test_benchmark_runs_full_mock_workload(tmp_path):
    stats = bench.run_benchmark(sites=12, workers=4, latency=0.0,
                                out_dir=str(tmp_path / "out"))

    assert stats["counters"]["committed"] == 12   # each site exactly once
    assert stats["counters"]["collected"] == 12
    assert stats["counters"]["deduped"] == 0
    assert stats["workers"] == 4
    assert stats["wall_seconds"] > 0
    assert stats["enriched"] == 12
    assert stats["avg_enrich_seconds"] >= 0.0
    assert stats["queue_depth_max"] >= 0
    assert 0.0 < stats["worker_utilization"] <= 1.0


def test_benchmark_mock_sites_serve_distinct_domains():
    """One port == one rate-limit domain, so the per-domain gate behaves like
    it would against real distinct websites."""
    fleet = bench._MockSites(sites=6, servers=3, latency=0.0)
    try:
        urls = [fleet.url_for(i) for i in range(6)]
        assert len({u.split(":")[2].split("/")[0] for u in urls}) == 3
        assert len(fleet.ports) == 3
    finally:
        fleet.stop()
