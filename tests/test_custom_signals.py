"""Config-driven custom signals: validation, detection, export, filtering."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scraper.config import ConfigError, load_config
from scraper.maps.collector import DemoCollector
from scraper.pipeline import Pipeline
from scraper.signals.detector import PageContext, SignalDetector

ROOT = Path(__file__).resolve().parents[1]


def _write_config(tmp_path, extra: str) -> object:
    p = tmp_path / "config.yaml"
    p.write_text(
        f"queries: ['dentists in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: sigtest\n"
        + extra,
        encoding="utf-8",
    )
    return p


# --- Config validation --------------------------------------------------------

def test_custom_signal_spec_loads_from_config(tmp_path):
    p = _write_config(
        tmp_path,
        "\nsignals:\n"
        "  custom:\n"
        "    emergency_service:\n"
        "      column: signal_emergency_service\n"
        "      match: any\n"
        "      keywords: ['24/7', 'emergency plumber']\n",
    )
    cfg = load_config(p)
    assert cfg.signals.custom["emergency_service"]["keywords"] == [
        "24/7", "emergency plumber"]


def test_custom_signal_bad_column_rejected(tmp_path):
    p = _write_config(
        tmp_path,
        "\nsignals:\n  custom:\n    s1:\n      column: 'bad column!'\n"
        "      keywords: ['x']\n",
    )
    with pytest.raises(ConfigError):
        load_config(p)


def test_custom_signal_empty_keywords_rejected(tmp_path):
    p = _write_config(
        tmp_path, "\nsignals:\n  custom:\n    s1:\n      column: c1\n"
                  "      keywords: []\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_custom_signal_duplicate_columns_rejected(tmp_path):
    p = _write_config(
        tmp_path,
        "\nsignals:\n  custom:\n"
        "    s1:\n      column: same_col\n      keywords: ['a']\n"
        "    s2:\n      column: same_col\n      keywords: ['b']\n",
    )
    with pytest.raises(ConfigError):
        load_config(p)


def test_custom_signal_column_colliding_with_schema_rejected(tmp_path):
    p = _write_config(
        tmp_path,
        "\nsignals:\n  custom:\n    s1:\n      column: business_name\n"
        "      keywords: ['a']\n",
    )
    with pytest.raises(ValueError, match="collide"):
        Pipeline(load_config(p), DemoCollector())


def test_custom_signal_bad_match_rejected(tmp_path):
    p = _write_config(
        tmp_path,
        "\nsignals:\n  custom:\n    s1:\n      column: c1\n"
        "      match: sometimes\n      keywords: ['x']\n",
    )
    with pytest.raises(ConfigError):
        load_config(p)


# --- Detection semantics (any = OR, all = AND, custom column name) ------------

def _ctx(text: str) -> PageContext:
    return PageContext(text=text, html="", urls=[], scripts=[])


def test_custom_signal_any_match_and_column_name():
    det = SignalDetector({"emergency": {
        "column": "signal_emergency_service",
        "match": "any", "keywords": ["24/7", "emergency plumber"]}})
    outcome, _ = det.run(_ctx("We are an emergency plumber in Houston."))
    assert outcome["signal_emergency_service"] == "YES"
    outcome2, _ = det.run(_ctx("Ordinary plumbing site."))
    assert outcome2["signal_emergency_service"] == "NO"


def test_custom_signal_all_match_requires_every_keyword():
    det = SignalDetector({"modern": {
        "column": "uses_online_booking", "match": "all",
        "keywords": ["chat", "book online"]}})
    one, _ = det.run(_ctx("We have a chat widget."))
    assert one["uses_online_booking"] == "NO"       # only 1 of 2 keywords
    both, _ = det.run(_ctx("Chat with us and book online today."))
    assert both["uses_online_booking"] == "YES"


def test_disabled_custom_signal_emits_nothing():
    det = SignalDetector({"off": {
        "column": "c_off", "keywords": ["x"], "enabled": False}})
    outcome, _ = det.run(_ctx("x x x"))
    assert "c_off" not in outcome  # disabled spec emits no column
    assert outcome.get("meta_pixel") == "NO"  # built-ins still present


# --- End-to-end: the column really reaches leads.csv --------------------------

def test_custom_signal_column_exported(tmp_path):
    p = _write_config(
        tmp_path,
        "\nsignals:\n  custom:\n    emergency_service:\n"
        "      column: signal_emergency_service\n"
        "      keywords: ['24/7']\n",
    )
    pipeline = Pipeline(load_config(p), DemoCollector())
    counters = pipeline.run()
    assert counters["committed"] > 0

    rows = list(csv.DictReader(
        (tmp_path / "out" / "sigtest" / "leads.csv").open(encoding="utf-8")))
    assert rows, "expected committed rows"
    assert "signal_emergency_service" in rows[0]
    assert all(r["signal_emergency_service"] in ("YES", "NO") for r in rows)


def test_removing_custom_signal_removes_the_column(tmp_path):
    """A variable removed from config.yaml must disappear from the export —
    no static schema, no decorative keys."""
    base = ("queries: ['dentists in Dallas']\n"
            f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: {{name}}\n")
    with_sig = base.format(name="withsig") + (
        "\nsignals:\n  custom:\n    emergency_service:\n"
        "      column: signal_emergency_service\n      keywords: ['24/7']\n")
    without_sig = base.format(name="nosig")
    for text in (with_sig, without_sig):
        cfgp = tmp_path / f"cfg_{abs(hash(text))}.yaml"
        cfgp.write_text(text, encoding="utf-8")
        Pipeline(load_config(cfgp), DemoCollector()).run()

    with_cols = next(csv.reader(
        (tmp_path / "out" / "withsig" / "leads.csv").open(encoding="utf-8")))
    without_cols = next(csv.reader(
        (tmp_path / "out" / "nosig" / "leads.csv").open(encoding="utf-8")))
    assert "signal_emergency_service" in with_cols
    assert "signal_emergency_service" not in without_cols


def test_filter_on_custom_signal_column(tmp_path):
    """filters: conditions on a custom signal column run in the POST pass and
    really keep/drop rows."""
    p = _write_config(
        tmp_path,
        "\nsignals:\n  custom:\n    emergency_service:\n"
        "      column: signal_emergency_service\n      keywords: ['24/7']\n"
        "filters:\n"
        "  include_all:\n"
        "    - field: signal_emergency_service\n"
        "      op: '='\n      value: 'yes'\n",
    )
    pipeline = Pipeline(load_config(p), DemoCollector())
    counters = pipeline.run()
    # Demo websites are unreachable offline -> signal is NO everywhere ->
    # the include filter drops every row (proving the filter fires on the
    # custom column rather than ignoring it).
    assert counters["committed"] == 0
    assert counters["filtered"] > 0


def test_template_ships_empty_custom_signals(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.signals.custom == {}
    assert cfg.summary.enabled is True
