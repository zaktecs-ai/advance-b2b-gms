"""Per-record quality gate.

Checks a record for completeness, contradictions, and encoding issues. Returns
a list of issue strings; an empty list means the record passes.
"""
from __future__ import annotations

import re

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def quality_issues(record: dict) -> list[str]:
    issues: list[str] = []
    name = (record.get("business_name") or "").strip().upper()
    if not name or name in ("N/A", ""):
        issues.append("missing_name")

    rating = record.get("rating")
    if rating not in (None, "N/A", ""):
        try:
            r = float(rating)
            if not (0.0 <= r <= 5.0):
                issues.append(f"rating_out_of_range:{rating}")
        except (TypeError, ValueError):
            issues.append("rating_non_numeric")

    # Encoding: reject control chars in any string value.
    for k, v in record.items():
        if isinstance(v, str) and _CONTROL_RE.search(v):
            issues.append(f"control_chars_in:{k}")

    # Contradiction: dead website but a valid email (rare, flag only).
    return issues


def passes_quality(record: dict) -> bool:
    return len(quality_issues(record)) == 0
