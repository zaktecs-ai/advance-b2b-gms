"""Pipeline end-to-end (demo mode) + quality gate."""
from scraper.config import load_config
from scraper.maps.collector import DemoCollector
from scraper.pipeline import Pipeline
from scraper.models import OUTPUT_COLUMNS
from scraper.validation.quality import quality_issues, passes_quality


def test_pipeline_demo_end_to_end(tmp_path):
    # Build a config with output_dir pointed at tmp_path.
    (tmp_path / "config.yaml").write_text(
        "queries: ['dentists in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: demo\n",
        encoding="utf-8",
    )
    cfg = load_config(str(tmp_path / "config.yaml"))
    pipeline = Pipeline(cfg, DemoCollector())
    counters = pipeline.run()

    assert counters["committed"] > 0
    # CSV must have all columns + at least one data row.
    csv_path = tmp_path / "out" / "demo" / "leads.csv"
    assert csv_path.exists()
    lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
    header = lines[0]
    for col in OUTPUT_COLUMNS:
        assert col in header
    assert len(lines) > 1


def test_quality_gate_missing_name():
    rec = {"business_name": "", "rating": 4.5}
    issues = quality_issues(rec)
    assert "missing_name" in issues
    assert not passes_quality(rec)


def test_quality_gate_rating_range():
    rec = {"business_name": "A", "rating": 9.9}
    assert any("rating_out_of_range" in i for i in quality_issues(rec))


def test_quality_gate_control_chars():
    rec = {"business_name": "A", "address": "bad\x00char"}
    assert any("control_chars" in i for i in quality_issues(rec))


def test_quality_gate_ok():
    rec = {"business_name": "Acme", "rating": 4.5}
    assert passes_quality(rec)
