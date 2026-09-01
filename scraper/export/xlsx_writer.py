"""XLSX export (optional, when openpyxl is installed)."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def write_xlsx(path: str | Path, columns: list[str], rows) -> None:
    """Write rows (list or iterable) to an XLSX file in write-only mode.

    Write-only mode streams rows instead of building a full Workbook in RAM, so
    a 100k+ record finalize no longer OOMs (F31). Accepts either a list of
    dicts or an iterator of dicts.
    """
    try:
        from openpyxl import Workbook
    except ImportError:
        log.warning("openpyxl not installed; skipping XLSX export")
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("leads")
    ws.append(list(columns))
    for row in rows:
        ws.append([row.get(c, "") for c in columns])
    wb.save(str(path))
