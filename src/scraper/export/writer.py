"""Output writer: atomic append-safe CSV + optional XLSX.

The record is flushed to disk and fsync'd before the caller advances any
checkpoint state, so a crash mid-write can never lose or corrupt a committed row.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

from ..models import MISSING, OUTPUT_COLUMNS, Business


class ExportWriter:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.base_dir / "leads.csv"
        self._fh = None
        self._writer = None
        self.row_index = 0
        self._open()

    def _open(self) -> None:
        exists = self.csv_path.exists()
        self._fh = open(self.csv_path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(
            self._fh, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore"
        )
        if not exists:
            self._writer.writeheader()
            self._fh.flush()
            os.fsync(self._fh.fileno())
        else:
            # count existing data rows so row_index stays accurate
            with open(self.csv_path, newline="", encoding="utf-8") as f:
                self.row_index = sum(1 for _ in f) - 1  # minus header
            if self.row_index < 0:
                self.row_index = 0

    def write(self, business: Business) -> int:
        """Write one record; returns its 0-based row index."""
        row = business.to_row()
        for k in row:
            if row[k] is None:
                row[k] = MISSING
        self._writer.writerow(row)  # type: ignore[union-attr]
        self._fh.flush()  # type: ignore[union-attr]
        os.fsync(self._fh.fileno())  # type: ignore[union-attr]
        idx = self.row_index
        self.row_index += 1
        return idx

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
        self._write_xlsx()

    def _write_xlsx(self) -> None:
        if not self.csv_path.exists():
            return
        try:
            import openpyxl
        except ImportError:
            return  # xlsx optional; CSV is the source of truth
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Leads"
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows:
            return
        ws.append(rows[0])
        for r in rows[1:]:
            ws.append(r)
        ws.freeze_panes = "A2"
        wb.save(self.base_dir / "leads.xlsx")
