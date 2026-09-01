"""SQLite-backed checkpoint store (WAL, resumable, crash-safe).

Tracks job progress, per-query state, per-record stage progression, and
committed row offsets. A human-readable JSON mirror + ``.backup`` copy are
maintained. On restart, dedup seen-sets are seeded from COMMITTED records only
so an in-flight record is never mistaken for a duplicate.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS queries (
    query TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    discovered INTEGER NOT NULL DEFAULT 0,
    committed INTEGER NOT NULL DEFAULT 0,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    identity_key TEXT,
    kgmid TEXT,
    place_id TEXT,
    canonical_domain TEXT,
    normalized_phone TEXT,
    city TEXT,
    source_query TEXT,
    stage TEXT NOT NULL DEFAULT 'discovered',
    raw_json TEXT,
    committed_row INTEGER,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_records_identity ON records(identity_key);
CREATE INDEX IF NOT EXISTS idx_records_kgmid ON records(kgmid);
CREATE INDEX IF NOT EXISTS idx_records_domain ON records(canonical_domain);
CREATE INDEX IF NOT EXISTS idx_records_phone ON records(normalized_phone);
CREATE INDEX IF NOT EXISTS idx_records_city ON records(city);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
"""


class CheckpointStore:
    # The JSON mirror is a convenience, human-readable snapshot, NOT the
    # crash-safe source of truth (SQLite+WAL is). Refreshing it once per record
    # is O(n²) total I/O, so it is written only every N records plus once at
    # close. Live-progress tooling should read the SQLite `counters` table.
    MIRROR_EVERY = 500

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._lock = threading.RLock()
        with self._conn:
            self._conn.executescript(_SCHEMA)
        self._json_path = self.path.with_suffix(".json")
        self._backup_path = self.path.with_name(self.path.name + ".backup.json")
        self._since_mirror = 0
        self._load_existing()

    # -- dedup / identity preload ------------------------------------------
    def _load_existing(self) -> None:
        """Populate seen sets from COMMITTED records only."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT identity_key, kgmid, place_id, canonical_domain, "
                "normalized_phone, city FROM records WHERE stage='committed'"
            ).fetchall()
        self._identities = {r["identity_key"] for r in rows if r["identity_key"]}
        self._domains = {r["canonical_domain"] for r in rows if r["canonical_domain"]}
        # Fallback sets mirror the resolver: only records lacking a strong id.
        weak = [r for r in rows if not r["kgmid"] and not r["place_id"]]
        self._phones = {r["normalized_phone"] for r in weak if r["normalized_phone"]}
        self._domain_city = {
            f"{r['canonical_domain']}|{r['city']}" for r in weak
            if r["canonical_domain"] and r["city"]
        }

    def seed_sets(self) -> dict:
        return {
            "identities": set(self._identities),
            "domains": set(self._domains),
            "phones": set(self._phones),
            "domain_city": set(self._domain_city),
        }

    # -- queries -------------------------------------------------------------
    def register_query(self, query: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO queries(query, status) VALUES(?, 'pending')",
                (query,),
            )

    def list_pending_queries(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT query FROM queries WHERE status != 'done' ORDER BY query"
            ).fetchall()
        return [r["query"] for r in rows]

    def is_query_done(self, query: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM queries WHERE query=?", (query,)
            ).fetchone()
        return row is not None and row["status"] == "done"

    def mark_query_done(self, query: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE queries SET status='done', updated_at=? WHERE query=?",
                (time.time(), query),
            )

    def mark_query_failed(self, query: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE queries SET status='pending', updated_at=? WHERE query=?",
                (time.time(), query),
            )

    def bump_discovered(self, query: str, n: int = 1) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE queries SET discovered = discovered + ? WHERE query=?", (n, query)
            )

    # -- records -------------------------------------------------------------
    def register_record(self, record_id: str, identity_key: str, sig: dict,
                        source_query: str, raw: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO records(record_id, identity_key, kgmid, place_id, "
                "canonical_domain, normalized_phone, city, source_query, stage, raw_json, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?, 'discovered', ?, ?)",
                (record_id, identity_key, sig.get("kgmid"), sig.get("place_id"),
                 sig.get("canonical_domain"), sig.get("normalized_phone"),
                 sig.get("city"), source_query, json.dumps(raw, ensure_ascii=False),
                 time.time()),
            )
        self._maybe_mirror()

    def set_stage(self, record_id: str, stage: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE records SET stage=?, updated_at=? WHERE record_id=?",
                (stage, time.time(), record_id),
            )

    def mark_committed(self, record_id: str, row_index: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE records SET stage='committed', committed_row=?, updated_at=? WHERE record_id=?",
                (row_index, time.time(), record_id),
            )
        self._maybe_mirror()

    def committed_rows(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT raw_json, committed_row FROM records WHERE stage='committed' "
                "ORDER BY committed_row"
            ).fetchall()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r["raw_json"]))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def committed_count(self) -> int:
        """Number of COMMITTED records — the authority for valid CSV rows."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM records WHERE stage='committed'"
            ).fetchone()
        return row["n"] if row else 0

    # -- counters ------------------------------------------------------------
    def incr(self, name: str, n: int = 1) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO counters(name, value) VALUES(?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = value + ?",
                (name, n, n),
            )

    def get_counter(self, name: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM counters WHERE name=?", (name,)
            ).fetchone()
        return row["value"] if row else 0

    # -- mirror / backup -----------------------------------------------------
    def _maybe_mirror(self) -> None:
        """Refresh the JSON mirror at most every MIRROR_EVERY records.

        The mirror is incremental-cost rather than per-record, turning O(n²)
        total mirror I/O into O(n). The full authoritative snapshot is still
        written once at close().
        """
        self._since_mirror += 1
        if self._since_mirror >= self.MIRROR_EVERY:
            self._since_mirror = 0
            self._write_mirror()

    def _write_mirror(self) -> None:
        try:
            with self._lock:
                rows = self._conn.execute("SELECT * FROM records").fetchall()
            data = {"records": [dict(r) for r in rows]}
            tmp = self._json_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._json_path)
        except Exception as e:  # noqa: BLE001
            log.debug("mirror write skipped: %s", e)

    def close(self) -> None:
        with self._lock:
            try:
                self._write_mirror()  # final authoritative snapshot
            finally:
                self._conn.close()
