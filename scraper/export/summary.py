"""JSON run summary writer."""
from __future__ import annotations

import json
from pathlib import Path


def write_summary(path: str | Path, summary: dict) -> None:
    """Write a run summary dict as pretty JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
