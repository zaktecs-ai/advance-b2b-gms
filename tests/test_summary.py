"""summary.json: per-campaign resource metrics + campaign + lead details."""
from __future__ import annotations

import json
from pathlib import Path

from scraper.config import load_config
from scraper.maps.collector import DemoCollector
from scraper.pipeline import Pipeline


def _write_config(tmp_path, client: str = "sumtest", extra: str = "") -> object:
    p = tmp_path / "config.yaml"
    p.write_text(
        f"queries: ['dentists in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: {client}\n"
        + extra,
        encoding="utf-8",
    )
    return p


def test_summary_json_contains_all_required_fields(tmp_path):
    pipeline = Pipeline(load_config(
        _write_config(tmp_path)), DemoCollector())
    counters = pipeline.run()
    assert counters["committed"] > 0

    doc = json.loads(
        (tmp_path / "out" / "sumtest" / "summary.json").read_text("utf-8"))

    # Required top-level metrics.
    assert isinstance(doc["campaign_id"], str) and doc["campaign_id"]
    assert isinstance(doc["cpu_max_usage_percent"], (int, float))
    assert doc["cpu_max_usage_percent"] >= 0
    assert isinstance(doc["ram_consumed_mb"], (int, float))
    assert doc["ram_consumed_mb"] > 0
    assert doc["execution_time_seconds"] >= 0
    assert "generated_at" in doc

    # Campaign details.
    cd = doc["campaign_details"]
    assert cd["client_name"] == "sumtest"
    assert cd["queries"] == ["dentists in Dallas"]
    assert "concurrency" in cd and "filters" in cd and "custom_signals" in cd

    # Server/environment details (single-node run -> one entry, extensible).
    assert isinstance(doc["servers"], list) and doc["servers"]
    srv = doc["servers"][0]
    assert srv["name"]
    for key in ("platform", "python_version", "cpu_count_logical",
                "total_ram_mb", "cpu_max_usage_percent", "ram_peak_mb"):
        assert key in srv["details"]

    # Leads: every committed row, keyed and populated.
    assert isinstance(doc["leads"], list)
    assert len(doc["leads"]) == counters["committed"]
    lead = doc["leads"][0]
    for key in ("record_id", "business_name", "city", "rating",
                "source_query", "lead_score"):
        assert key in lead, f"lead missing {key}"


def test_summary_disabled_writes_nothing(tmp_path):
    pipeline = Pipeline(load_config(
        _write_config(tmp_path, extra="\nsummary:\n  enabled: false\n")),
        DemoCollector())
    pipeline.run()
    assert not (tmp_path / "out" / "sumtest" / "summary.json").exists()


def test_summary_custom_signal_columns_documented(tmp_path):
    p = _write_config(
        tmp_path, client="sumtest",
        extra="\nsignals:\n  custom:\n    emergency_service:\n"
              "      column: signal_emergency_service\n      keywords: ['24/7']\n")
    pipeline = Pipeline(load_config(p), DemoCollector())
    pipeline.run()
    doc = json.loads(
        (tmp_path / "out" / "sumtest" / "summary.json").read_text("utf-8"))
    assert "emergency_service" in doc["campaign_details"]["custom_signals"]
    assert doc["campaign_details"]["custom_signals"]["emergency_service"][
        "column"] == "signal_emergency_service"