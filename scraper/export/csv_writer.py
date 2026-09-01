"""Append-safe CSV writer with atomic row commits and crash recovery.

Each row is flushed + fsync'd before the checkpoint advances, so a crash never
loses a committed row nor leaves a malformed trailing partial row. On open, the
writer trims any malformed trailing line back to the last complete row.
"""
from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class AtomicCSVWriter:
    def __init__(self, path: str | Path, columns: list[str]):
        self.path = Path(path)
        self.columns = columns
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self._writer = None
        self._row_count = 0
        self._open()

    def _open(self) -> None:
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        if is_new:
            self._fh = open(self.path, "w", encoding="utf-8", newline="")
            self._writer = csv.DictWriter(self._fh, fieldnames=self.columns)
            self._writer.writeheader()
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._row_count = 0
        else:
            self._recover()
            self._fh = open(self.path, "a", encoding="utf-8", newline="")
            self._writer = csv.DictWriter(self._fh, fieldnames=self.columns)
            self._row_count = self._count_rows()

    def _recover(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh))
            if not rows:
                return
            if rows[0] != [str(c) for c in self.columns]:
                raise ValueError(
                    "CSV header does not match the active output schema; "
                    "choose a new output path instead of mixing schemas"
                )
            expected = len(self.columns)
            if rows and len(rows[-1]) != expected:
                log.warning("trimming malformed trailing row (%d vs %d fields)",
                            len(rows[-1]), expected)
                with open(self.path, "w", encoding="utf-8", newline="") as fh:
                    csv.writer(fh).writerows(rows[:-1])
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("CSV recovery failed (will append): %s", e)

    def _count_rows(self) -> int:
        # Count CSV ROWS via the parser, not physical lines: a quoted field
        # with an embedded newline (e.g. top_review) would otherwise be counted
        # as multiple rows and make reconcilliation trim valid committed rows
        # (F16).
        try:
            with open(self.path, "r", encoding="utf-8", newline="") as fh:
                return max(0, sum(1 for _ in csv.reader(fh)) - 1)
        except Exception:
            return 0

    def truncate_to(self, n_rows: int) -> None:
        """Drop every data row beyond ``n_rows`` (keep header + first n rows).

        Used by startup reconciliation so a CSV row that was durably written but
        whose checkpoint commit didn't land is trimmed — the checkpoint is the
        single authority for how many rows are valid.
        """
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None
        try:
            with open(self.path, "r", encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh))
        except Exception as e:  # noqa: BLE001
            log.warning("truncate_to read failed: %s", e)
            self._open()
            return
        keep = rows[: n_rows + 1] if len(rows) > 1 else rows
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(keep)
            fh.flush()
            os.fsync(fh.fileno())
        self._open()

    def append(self, row: dict) -> int:
        ordered = {c: row.get(c, "") for c in self.columns}
        for k in list(ordered):
            v = ordered[k]
            if v is None:
                ordered[k] = ""
            elif not isinstance(v, str):
                ordered[k] = str(v)
        self._writer.writerow(ordered)
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._row_count += 1
        return self._row_count - 1

    @property
    def row_count(self) -> int:
        return self._row_count

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            finally:
                self._fh.close()
                self._fh = None
