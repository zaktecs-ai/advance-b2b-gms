"""Checkpoint store: register/commit, in-flight-not-dup, CSV recovery."""
import json

from scraper.checkpoint.store import CheckpointStore
from scraper.export.csv_writer import AtomicCSVWriter
from scraper.models import OUTPUT_COLUMNS


def _sig(kgmid="/g/1"):
    return {"kgmid": kgmid, "place_id": None, "canonical_domain": None,
            "normalized_phone": None, "city": None, "identity_key": "abc",
            "key_type": "kgmid"}


def test_register_and_commit(tmp_path):
    store = CheckpointStore(tmp_path / "ck.sqlite")
    store.register_record("r1", "abc", _sig(), "q", {"business_name": "A"})
    store.mark_committed("r1", 0)
    assert store.get_counter("x") == 0
    committed = store.committed_rows()
    assert len(committed) == 1
    assert committed[0]["business_name"] == "A"
    store.close()


def test_inflight_not_seeded(tmp_path):
    # Register but DON'T commit -> on restart, must not appear in dedup seeds.
    path = tmp_path / "ck.sqlite"
    store = CheckpointStore(path)
    store.register_record("r1", "abc", _sig("/g/9"), "q", {"business_name": "A"})
    store.close()
    reopened = CheckpointStore(path)
    seeds = reopened.seed_sets()
    assert "abc" not in seeds["identities"]
    reopened.close()


def test_committed_seeded(tmp_path):
    # Commit a record, then REOPEN (simulating a restart) and verify the
    # committed identity is re-seeded for dedup.
    path = tmp_path / "ck.sqlite"
    store = CheckpointStore(path)
    store.register_record("r1", "xyz", _sig("/g/9"), "q", {"business_name": "A"})
    store.mark_committed("r1", 0)
    store.close()
    reopened = CheckpointStore(path)
    seeds = reopened.seed_sets()
    assert "xyz" in seeds["identities"]
    reopened.close()


def test_query_done(tmp_path):
    store = CheckpointStore(tmp_path / "ck.sqlite")
    store.register_query("q1")
    assert not store.is_query_done("q1")
    store.mark_query_done("q1")
    assert store.is_query_done("q1")
    store.close()


def test_csv_recovery_trims_trailing(tmp_path):
    p = tmp_path / "leads.csv"
    # Write a valid header + one full row + a partial row.
    cols = ["a", "b", "c"]
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write("a,b,c\n")
        fh.write("1,2,3\n")
        fh.write("4,5\n")  # malformed trailing
    w = AtomicCSVWriter(p, cols)
    assert w.row_count == 1  # only the complete row survives
    w.close()


def test_atomic_csv_append(tmp_path):
    p = tmp_path / "leads.csv"
    w = AtomicCSVWriter(p, ["x", "y"])
    w.append({"x": "1", "y": "2"})
    w.close()
    with open(p, encoding="utf-8", newline="") as fh:
        lines = fh.read().strip().splitlines()
    assert lines[0] == "x,y"
    assert lines[1] == "1,2"
