"""Checkpoint store: register/commit, in-flight-not-dup, CSV recovery."""
from scraper.checkpoint.store import CheckpointStore
from scraper.export.csv_writer import AtomicCSVWriter


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


def test_csv_schema_mismatch_fails_closed(tmp_path):
    p = tmp_path / "leads.csv"
    p.write_text("old_header\nold_value\n", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="active output schema"):
        AtomicCSVWriter(p, ["new_header"])


def test_atomic_csv_append(tmp_path):
    p = tmp_path / "leads.csv"
    w = AtomicCSVWriter(p, ["x", "y"])
    w.append({"x": "1", "y": "2"})
    w.close()
    with open(p, encoding="utf-8", newline="") as fh:
        lines = fh.read().strip().splitlines()
    assert lines[0] == "x,y"
    assert lines[1] == "1,2"


def test_mirror_is_throttled_no_rewrite_per_record(tmp_path, monkeypatch):
    # C1: the JSON mirror must NOT be rewritten on every commit. Count calls to
    # _write_mirror across many commits and assert it is O(n/MIRROR_EVERY).
    store = CheckpointStore(tmp_path / "ck.sqlite")
    calls = {"n": 0}
    orig = store._write_mirror
    def counting():
        calls["n"] += 1
        return orig()
    monkeypatch.setattr(store, "_write_mirror", counting)
    for i in range(1200):
        store.register_record(f"r{i}", f"k{i}", _sig(), "q", {"business_name": "A"})
        store.mark_committed(f"r{i}", i)
    # 1200 commits with MIRROR_EVERY=500 -> only 2 or 3 mirror writes, not 2400.
    assert calls["n"] <= 4, calls["n"]
    store.close()


def test_close_writes_final_mirror(tmp_path):
    store = CheckpointStore(tmp_path / "ck.sqlite")
    store.register_record("r1", "k1", _sig(), "q", {"business_name": "A"})
    store.mark_committed("r1", 0)
    store.close()
    assert (tmp_path / "ck.json").exists()


def test_committed_count_is_authority(tmp_path):
    store = CheckpointStore(tmp_path / "ck.sqlite")
    assert store.committed_count() == 0
    store.register_record("r1", "k1", _sig(), "q", {"business_name": "A"})
    assert store.committed_count() == 0  # discovered, not committed
    store.mark_committed("r1", 0)
    assert store.committed_count() == 1
    store.close()


def test_csv_truncate_to_drops_tail_rows(tmp_path):
    cols = ["a", "b", "c"]
    p = tmp_path / "leads.csv"
    w = AtomicCSVWriter(p, cols)
    w.append({"a": "1", "b": "2", "c": "3"})
    w.append({"a": "4", "b": "5", "c": "6"})
    w.append({"a": "7", "b": "8", "c": "9"})
    w.truncate_to(1)  # keep header + 1 row
    with open(p, encoding="utf-8", newline="") as fh:
        lines = fh.read().strip().splitlines()
    assert lines == ["a,b,c", "1,2,3"]
    w.close()
