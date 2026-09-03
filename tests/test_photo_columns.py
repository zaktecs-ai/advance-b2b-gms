"""Photos / owner-activity columns: parsing, schema order, e2e export, migration."""
from __future__ import annotations

import csv

from scraper.checkpoint.store import CheckpointStore
from scraper.config import load_config
from scraper.maps.collector import DemoCollector, parse_latest_upload_label
from scraper.models import OUTPUT_COLUMNS
from scraper.pipeline import Pipeline


# --- Pure parsing -------------------------------------------------------------

def test_parse_latest_upload_label():
    assert parse_latest_upload_label("Latest \u00b7 11 days ago") == "11 days ago"
    assert parse_latest_upload_label("Latest \u00b7 a month ago") == "a month ago"
    assert parse_latest_upload_label("Latest") == "N/A"
    assert parse_latest_upload_label("") == "N/A"
    assert parse_latest_upload_label(None) == "N/A"


# --- Schema: order right after business_hours, 72 columns ---------------------

def test_photo_columns_position_and_count():
    assert len(OUTPUT_COLUMNS) == 72
    idx = OUTPUT_COLUMNS.index("business_hours")
    assert OUTPUT_COLUMNS[idx + 1: idx + 6] == [
        "cover_image_url", "latest_image_upload", "by_owner_photos",
        "has_recent_post", "latest_post_date"]
    assert "business_description" not in OUTPUT_COLUMNS


# --- End-to-end: demo run exports the columns in the right order --------------

def _write_config(tmp_path, client="phototest"):
    p = tmp_path / "config.yaml"
    lines = [
        "queries: ['dentists in Dallas']",
        "job:",
        f"  output_dir: '{tmp_path}/out'",
        f"  client_name: {client}",
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_photo_columns_exported_in_order(tmp_path):
    pipeline = Pipeline(load_config(_write_config(tmp_path)), DemoCollector())
    counters = pipeline.run()
    assert counters["committed"] > 0

    with (tmp_path / "out" / "phototest" / "leads.csv").open(
            encoding="utf-8") as fh:
        table = list(csv.reader(fh))
    header, data = table[0], table[1:]
    idx = header.index("business_hours")
    assert header[idx + 1: idx + 6] == [
        "cover_image_url", "latest_image_upload", "by_owner_photos",
        "has_recent_post", "latest_post_date"]
    assert "business_description" not in header
    row = dict(zip(header, data[0]))
    assert row["cover_image_url"].startswith("https://")
    assert row["latest_image_upload"] == "11 days ago"
    assert row["by_owner_photos"] == "YES"
    assert row["has_recent_post"] == "YES"
    assert row["latest_post_date"] == "3 days ago"


# --- Schema migration: old CSV rebuilt from checkpoint ------------------------

def test_old_csv_with_removed_column_is_migrated(tmp_path):
    out = tmp_path / "out" / "mig"
    out.mkdir(parents=True)
    ck = CheckpointStore(out / "checkpoint.sqlite")
    ck.register_record(
        "r1", "id1", {"kgmid": "/g/x", "place_id": "0x1:0x2"},
        "q", {"place_id": "0x1:0x2", "business_name": "Old Biz",
              "business_description": "old desc", "record_id": "r1"})
    ck.mark_committed("r1", 0)
    ck.close()
    (out / "leads.csv").write_text(
        "place_id,business_name,business_description\n"
        "0x1:0x2,Old Biz,old desc\n", encoding="utf-8")

    pipeline = Pipeline(load_config(_write_config(tmp_path, "mig")),
                        DemoCollector())
    pipeline.run()

    with (out / "leads.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    assert "business_description" not in header
    assert "cover_image_url" in header
    old_row = next(r for r in rows if r["business_name"] == "Old Biz")
    assert old_row["cover_image_url"] == "N/A"   # padded for old rows