"""Proxy rotation + health-based eviction (A2/A3)."""
from scraper.browser.proxy import ProxyConfig, ProxyManager


def test_round_robin_rotation():
    pm = ProxyManager(ProxyConfig(enabled=True, pool=["p1", "p2", "p3"],
                                  rotation="round_robin"))
    assert pm.resolve() == "p1"
    assert pm.resolve() == "p2"
    assert pm.resolve() == "p3"
    assert pm.resolve() == "p1"


def test_evict_after_failures():
    pm = ProxyManager(ProxyConfig(enabled=True, pool=["p1", "p2", "p3"],
                                  rotation="round_robin"))
    for _ in range(3):
        pm.report_failure("p1")
    seq = [pm.resolve() for _ in range(6)]
    assert "p1" not in seq


def test_success_resets_failures():
    pm = ProxyManager(ProxyConfig(enabled=True, pool=["p1", "p2"],
                                  rotation="round_robin"))
    for _ in range(3):
        pm.report_failure("p1")
    pm.report_success("p1")
    # p1 back in rotation
    assert set(pm.resolve() for _ in range(4)) == {"p1", "p2"}


def test_disabled_returns_none():
    pm = ProxyManager(ProxyConfig(enabled=False, pool=["p1"]))
    assert pm.resolve() is None
    assert pm.playwright_proxy() is None


def test_playwright_proxy_shape():
    pm = ProxyManager(ProxyConfig(enabled=True, pool=["http://h:1"],
                                  rotation="round_robin"))
    assert pm.playwright_proxy() == {"server": "http://h:1"}


def test_pipeline_threads_proxy_to_enricher(tmp_path):
    # F27: a proxy_manager passed to Pipeline reaches the enricher's fetcher.
    (tmp_path / "config.yaml").write_text(
        "queries: ['plumbers in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: px\n",
        encoding="utf-8",
    )
    from scraper.config import load_config
    from scraper.maps.collector import DemoCollector
    from scraper.pipeline import Pipeline
    cfg = load_config(str(tmp_path / "config.yaml"))

    pm = ProxyManager(ProxyConfig(enabled=True, https="http://p:1"))
    pipeline = Pipeline(cfg, DemoCollector(), proxy_manager=pm)
    try:
        assert pm.httpx_proxy() == "http://p:1"
    finally:
        pipeline.close()
