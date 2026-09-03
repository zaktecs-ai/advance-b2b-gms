"""Deterministic filter engine (AND / OR / NOT, two-pass split).

Pass 1 runs Maps-field filters before enrichment; pass 2 runs
enrichment-dependent filters after. Operators: = != > < >= <= in notin contains.
"""
from __future__ import annotations

from typing import Any

_OPS = {"=", "!=", ">", "<", ">=", "<=", "in", "notin", "contains"}

_ALIASES = {
    "reviews": "review_count",
    "email_found": "email_found",
    "require_email": "email_found",
    "has_website": "website",
    "gtm": "tag_manager",
    "ga4": "ga4",
    "meta_pixel": "meta_pixel",
    "rating_num": "rating",
}


def _coerce(value: Any):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    low = s.lower()
    if low in ("yes", "true", "y", "1"):
        return True
    if low in ("no", "false", "n", "0", ""):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _field_value(record: dict, field: str):
    key = _ALIASES.get(field, field)
    if field == "website":
        return record.get(key) not in (None, "", "N/A", False)
    if field == "email_found":
        return record.get("emails") not in (None, "", "N/A")
    if field == "meta_pixel":
        return (record.get("meta_pixel") or "").strip().lower() in ("yes", "true", "detected")
    if field == "ga4":
        return (record.get("ga4") or "").strip().lower() in ("yes", "true", "detected")
    if field == "gtm":
        return (record.get("tag_manager") or "").strip().lower() not in ("", "no", "false", "n/a")
    return record.get(key)


def _normalize_cond(cond: Any) -> dict:
    """Normalize the accepted condition forms into {field, op, value, negate}.

    Accepted forms:
      * {"field": "reviews", "op": ">=", "value": 15}   (explicit)
      * {"reviews": 15}                                  (equality shorthand)
      * {"reviews": 15, "op": ">="}                      (field+op)
      * {"reviews": "yes", "negate": true}                (NOT)
      * "reviews >= 15"                                  (string form)
    """
    if isinstance(cond, dict):
        if "field" in cond:
            return {
                "field": cond["field"],
                "op": cond.get("op", "="),
                "value": cond.get("value"),
                "negate": bool(cond.get("negate", False)),
            }
        # Otherwise: the key that is not op/negate is the field.
        op = cond.get("op", "=")
        negate = bool(cond.get("negate", False))
        field_keys = [k for k in cond if k not in ("op", "negate")]
        if len(field_keys) != 1:
            raise ValueError(f"bad filter condition: {cond!r}")
        field = field_keys[0]
        return {"field": field, "op": op, "value": cond[field], "negate": negate}
    if isinstance(cond, str):
        parts = cond.strip().split(maxsplit=2)
        if len(parts) == 3:
            field, op, value = parts
            return {"field": field, "op": op, "value": value, "negate": False}
        raise ValueError(f"bad filter condition: {cond!r}")
    raise ValueError(f"bad filter condition: {cond!r}")


def _compare(record: dict, cond: dict) -> bool:
    field = cond["field"]
    op = cond.get("op", "=")
    value = cond.get("value")
    negate = bool(cond.get("negate", False))
    if op not in _OPS:
        raise ValueError(f"unknown filter op: {op!r}")

    actual = _coerce(_field_value(record, field))
    expected = value if isinstance(value, (list, tuple, set)) else _coerce(value)

    if op in (">", "<", ">=", "<="):
        # A missing/unparseable numeric field is "unknown", not "0". Resolve
        # the comparison to False so an unknown number can neither satisfy an
        # include nor (via NOT) an exclude — fail-closed in both directions.
        if actual is None:
            result = False
        else:
            try:
                a = float(actual)
                b = float(expected)
                result = {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
            except (TypeError, ValueError):
                result = False
    elif op == "!=":
        result = actual != expected
    elif op == "in":
        result = actual in (expected if isinstance(expected, (list, tuple, set)) else [expected])
    elif op == "notin":
        result = actual not in (expected if isinstance(expected, (list, tuple, set)) else [expected])
    elif op == "contains":
        result = str(expected) in str(actual)
    else:
        result = actual == expected

    return (not result) if negate else result


def _evaluate_all(record: dict, conds: list[Any]) -> bool | None:
    if not conds:
        return None
    norm = [_normalize_cond(c) for c in conds]
    return all(_compare(record, c) for c in norm)


def _evaluate_any(record: dict, conds: list[Any]) -> bool | None:
    if not conds:
        return None
    norm = [_normalize_cond(c) for c in conds]
    return any(_compare(record, c) for c in norm)


# Fields that depend on website enrichment (must run in pass 2).
_ENRICHMENT_FIELDS = {
    "email_found", "emails", "email_count", "website_status",
    "website_failure_reason", "facebook", "instagram", "linkedin", "youtube",
    "twitter_x", "tiktok", "pinterest", "github", "snapchat", "tech_stack",
    "cms", "analytics", "tag_manager", "meta_pixel", "ga4", "gtm",
    "advertising", "booking_system", "chat_widget", "ssl",
    "signal_pricing", "signal_financing", "signal_licensed_insured",
    "signal_established", "signal_portfolio", "signal_mobile_service",
    "signal_membership", "lead_score", "sentiment_score", "review_keywords",
    "decision_maker_name", "decision_maker_title",
    "mx_status", "smtp_status",
}


def cond_fields(conds: list[Any]) -> set[str]:
    out: set[str] = set()
    for c in conds:
        try:
            norm = _normalize_cond(c)
        except ValueError:
            continue
        out.add(norm["field"])
    return out


def split_filters(filters: dict, extra_post_fields=()) -> tuple[dict, dict]:
    """Split a filters config into (pre_enrichment, post_enrichment).

    ``extra_post_fields`` lets callers declare runtime-generated columns
    (e.g. config-driven custom signal columns) that only exist after
    enrichment, so their conditions are evaluated in pass 2.
    """
    enrichment_fields = _ENRICHMENT_FIELDS | set(extra_post_fields or ())
    pre: dict = {}
    post: dict = {}
    for key in ("include_all", "include_any", "exclude_all", "exclude_any"):
        conds = filters.get(key, [])
        post_conds = [c for c in conds if cond_fields([c]) & enrichment_fields]
        pre_conds = [c for c in conds if c not in post_conds]
        if pre_conds:
            pre[key] = pre_conds
        if post_conds:
            post[key] = post_conds
    return pre, post


def evaluate(record: dict, filters: dict) -> tuple[bool, str]:
    """Evaluate a record against a filters dict. Returns (keep, reason)."""
    include_all = filters.get("include_all", [])
    include_any = filters.get("include_any", [])
    exclude_all = filters.get("exclude_all", [])
    exclude_any = filters.get("exclude_any", [])

    if include_all and not _evaluate_all(record, include_all):
        return False, "failed_include_all"
    if include_any and not _evaluate_any(record, include_any):
        return False, "failed_include_any"
    if exclude_all and _evaluate_all(record, exclude_all):
        return False, "matched_exclude_all"
    if exclude_any and _evaluate_any(record, exclude_any):
        return False, "matched_exclude_any"
    return True, ""
