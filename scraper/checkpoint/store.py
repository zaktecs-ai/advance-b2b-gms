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
    name_key TEXT,
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
    # The JSON mirror is a convenience, human-readable NDJSON event log, NOT the
    # crash-safe source of truth (SQLite+WAL is). Each register/commit appends
    # one line (F30). Live-progress tooling should read the SQLite `counters`
    # table.

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
        self._migrate()
        self._load_existing()

    def _migrate(self) -> None:
        """Additive schema migration for pre-existing databases.

        ``CREATE TABLE IF NOT EXISTS`` does not add columns to an already-created
        table, so the ``name_key`` column (added for F06 dedup guards) is applied
        here with a guarded ``ALTER TABLE``.
        """
        try:
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(records)")}
        except Exception:  # pragma: no cover - defensive for a broken DB
            return
        if "name_key" not in cols:
            try:
                with self._conn:
                    self._conn.execute("ALTER TABLE records ADD COLUMN name_key TEXT")
            except Exception as e:  # noqa: BLE001
                log.debug("name_key migration skipped: %s", e)

    # -- dedup / identity preload ------------------------------------------
    def _load_existing(self) -> None:
        """Populate seen sets from COMMITTED records only.

        Bounded cold-start (F32): only the most recent ``_PRELOAD_LIMIT``
        committed rows are loaded into memory; the DB covers older history via
        ``identity_exists`` / ``domain_name_seen`` / ``phone_name_seen``.
        """
        _PRELOAD_LIMIT = 50_000
        with self._lock:
            rows = self._conn.execute(
                "SELECT identity_key, kgmid, place_id, canonical_domain, "
                "normalized_phone, name_key, city, committed_row FROM records "
                "WHERE stage='committed' ORDER BY committed_row DESC "
                "LIMIT ?",
                (_PRELOAD_LIMIT,),
            ).fetchall()
        self._identities = {r["identity_key"] for r in rows if r["identity_key"]}
        self._domains = {r["canonical_domain"] for r in rows if r["canonical_domain"]}
        # Name-key guards (F06): first name per domain / phone, applied to ALL
        # committed records (not just weak-id ones). First-write wins so a
        # chain's first location is the canonical name for its domain.
        self._domain_first_name: dict[str, str] = {}
        self._phone_first_name: dict[str, str] = {}
        for r in rows:
            d = r["canonical_domain"]
            p = r["normalized_phone"]
            nk = r["name_key"]
            if d and nk:
                self._domain_first_name.setdefault(d, nk)
            if p and nk:
                self._phone_first_name.setdefault(p, nk)
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
            "domain_first_name": dict(self._domain_first_name),
            "phone_first_name": dict(self._phone_first_name),
        }

    # -- indexed lookups (F32): DB covers history beyond the in-memory preload -
    def identity_exists(self, identity_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM records WHERE identity_key=? AND stage='committed' "
                "LIMIT 1", (identity_key,),
            ).fetchone()
        return row is not None

    def domain_name_seen(self, domain: str, name_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM records WHERE canonical_domain=? AND name_key=? "
                "AND stage='committed' LIMIT 1", (domain, name_key),
            ).fetchone()
        return row is not None

    def phone_name_seen(self, phone: str, name_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM records WHERE normalized_phone=? AND name_key=? "
                "AND stage='committed' LIMIT 1", (phone, name_key),
            ).fetchone()
        return row is not None

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
                "canonical_domain, normalized_phone, name_key, city, source_query, stage, raw_json, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?, 'discovered', ?, ?)",
                (record_id, identity_key, sig.get("kgmid"), sig.get("place_id"),
                 sig.get("canonical_domain"), sig.get("normalized_phone"),
                 sig.get("name_key"), sig.get("city"), source_query,
                 json.dumps(raw, ensure_ascii=False), time.time()),
            )
        self._append_mirror({"record_id": record_id, "stage": "discovered"})

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
        self._append_mirror({"record_id": record_id, "stage": "committed",
                             "committed_row": row_index})

    def committed_rows(self) -> list[dict]:
        # Materializes every committed row into memory. Kept for tests / small
        # datasets; the streaming `iter_committed_rows` is the production path
        # (F31).
        return list(self.iter_committed_rows())

    def iter_committed_rows(self, batch: int = 1000):
        """Stream committed rows as decoded dicts without loading them all.

        The finalize step previously materialized the whole dataset into RAM
        and OOM'd at ~100k records (F31). This cursor yields batches.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT raw_json FROM records WHERE stage='committed' "
                "ORDER BY committed_row")
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    break
                for r in rows:
                    try:
                        yield json.loads(r["raw_json"])
                    except (json.JSONDecodeError, TypeError):
                        continue

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
    def _append_mirror(self, row: dict) -> None:
        """Append one NDJSON line to the human-readable mirror.

        Incremental append (O(1) per event) replaces the previous full-table
        rewrite — at 500k records that meant ~1000 full SELECT+serialize+write
        passes (F30). SQLite remains the source of truth; the mirror is a
        best-effort convenience only.
        """
        try:
            with open(self._json_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as e:  # noqa: BLE001
            log.debug("mirror append skipped: %s", e)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception as e:  # noqa: BLE001
                log.debug("checkpoint close: %s", e)
