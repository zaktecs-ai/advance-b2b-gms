"""Config validation: env resolution, ranges, queries required."""
import pytest

from scraper.config import load_config, ConfigError
from scraper.maps.collector import DemoCollector


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_minimal_config(tmp_path):
    p = _write(tmp_path, "queries:\n  - 'dentists in Dallas'\n")
    cfg = load_config(p)
    assert cfg.queries == ["dentists in Dallas"]
    assert cfg.maps.zoom == 16


def test_missing_queries(tmp_path):
    p = _write(tmp_path, "maps:\n  headless: true\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_zoom_out_of_range(tmp_path):
    p = _write(tmp_path, "queries: ['x']\nmaps:\n  zoom: 99\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_env_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_CLIENT", "acme")
    p = _write(tmp_path, "queries: ['x']\njob:\n  client_name: '${MY_CLIENT}'\n")
    cfg = load_config(p)
    assert cfg.job.client_name == "acme"


def test_missing_env(tmp_path):
    p = _write(tmp_path, "queries: ['x']\njob:\n  client_name: '${NOT_SET_VAR}'\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_nonexistent_file():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.yaml")


def test_demo_collector_yields(tmp_path):
    c = DemoCollector()
    recs = list(c.collect("dentists in Dallas"))
    assert len(recs) == 3
    assert all("business_name" in r for r in recs)
