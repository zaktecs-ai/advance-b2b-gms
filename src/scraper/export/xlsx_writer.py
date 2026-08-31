"""XLSX export (optional, when openpyxl is installed)."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def write_xlsx(path: str | Path, columns: list[str], rows: list[dict]) -> None:
    """Write rows to an XLSX file. Falls back to a no-op if openpyxl is absent."""
    try:
        from openpyxl import Workbook
    except ImportError:
        log.warning("openpyxl not installed; skipping XLSX export")
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "leads"
    ws.append(list(columns))
    for row in rows:
        ws.append([row.get(c, "") for c in columns])
    wb.save(str(path))
