"""Tests for config validation and the end-to-end demo pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from scraper.config import Config, ConfigError  # noqa: E402
from scraper.pipeline import Job  # noqa: E402


def test_config_from_dict_ok():
    cfg = Config.from_dict({
        "job": {"client_name": "acme"},
        "queries": ["dentist in Dallas"],
        "reviews": {"per_business": 3},
    })
    assert cfg.client_name == "acme"
    assert cfg.reviews_per_business == 3
    assert cfg.output_dir.name == "acme"  # resolved under output/


def test_config_rejects_bad_client_name():
    with pytest.raises(ConfigError):
        Config.from_dict({"job": {"client_name": "../evil"}, "queries": ["x"]})


def test_config_requires_queries():
    with pytest.raises(ConfigError):
        Config.from_dict({"job": {"client_name": "ok"}, "queries": []})


def test_config_out_of_range():
    with pytest.raises(ConfigError):
        Config.from_dict({"job": {"client_name": "ok"}, "queries": ["x"],
                          "reviews": {"per_business": 999}})


def test_demo_pipeline_writes_records_and_summary(tmp_path):
    cfg = Config.from_dict({"job": {"client_name": "demo_test"}, "queries": ["demo"]})
    cfg.output_dir = tmp_path
    cfg = cfg.resolve()
    summary = Job(cfg, demo=True).run()
    assert summary["records"] == 3
    csv_path = tmp_path / "demo_test" / "leads.csv"
    assert csv_path.exists()
    text = csv_path.read_text()
    assert "top_review" in text  # header includes the add-on column
    assert "pitch_hook" in text
