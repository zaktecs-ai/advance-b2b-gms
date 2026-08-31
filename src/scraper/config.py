"""Configuration loading + validation for the standalone scraper.

One human-editable YAML file drives a job. Every risky knob is range-checked with
a clear message, so a bad value aborts cleanly instead of crashing the run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - fails fast if deps missing
    yaml = None  # type: ignore[assignment]


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or out of range."""


@dataclass
class Config:
    client_name: str = "campaign"
    output_dir: Path = Path("output")
    queries: list[str] = field(default_factory=list)
    headless: bool = True
    hl: str = "en"
    gl: str = "us"
    max_results_per_query: int = 0
    max_total_results: int = 0
    # the free add-on feature knobs (all off-by-default safe)
    reviews_enabled: bool = True
    reviews_per_business: int = 5
    min_review_len: int = 20
    max_review_len: int = 600
    # enrichment
    enrich_emails: bool = True
    require_website: bool = False
    # runtime
    website_workers: int = 4
    playwright_workers: int = 2

    _NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

    def resolve(self) -> "Config":
        self.output_dir = self.output_dir / self.client_name
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        if yaml is None:  # pragma: no cover
            raise ConfigError("PyYAML is required: pip install PyYAML")
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"Config file not found: {p}")
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except yaml.YAMLError as e:  # type: ignore[union-attr]
            raise ConfigError(f"Invalid YAML in {p}: {e}") from e
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        job = data.get("job", {}) or {}
        maps = data.get("maps", {}) or {}
        enr = data.get("enrichment", {}) or {}
        reviews = data.get("reviews", {}) or {}
        run = data.get("runtime", {}) or {}

        # job
        if isinstance(job.get("client_name"), str) and job["client_name"].strip():
            cfg.client_name = job["client_name"].strip()
        else:
            raise ConfigError("'job.client_name' must be a non-empty string")
        if not cls._NAME_RE.match(cfg.client_name):
            raise ConfigError(
                f"'job.client_name' '{cfg.client_name}' must match {cls._NAME_RE.pattern}"
            )
        if "output_dir" in job and isinstance(job["output_dir"], str):
            cfg.output_dir = Path(str(job["output_dir"]))
        cfg.queries = _strlist(data.get("queries"))
        if not cfg.queries:
            raise ConfigError("'queries' must contain at least one search string")

        # maps
        if isinstance(maps.get("headless"), bool):
            cfg.headless = maps["headless"]
        cfg.hl = _opt_str(maps.get("hl"), "en")
        cfg.gl = _opt_str(maps.get("gl"), "us")
        cfg.max_results_per_query = _int_in(maps, "max_results_per_query", 0, 100_000, 0)
        cfg.max_total_results = _int_in(maps, "max_total_results", 0, 1_000_000, 0)

        # enrichment
        if isinstance(enr.get("emails"), bool):
            cfg.enrich_emails = enr["emails"]
        if isinstance(enr.get("require_website"), bool):
            cfg.require_website = enr["require_website"]

        # reviews (free add-on)
        if isinstance(reviews.get("enabled"), bool):
            cfg.reviews_enabled = reviews["enabled"]
        cfg.reviews_per_business = _int_in(reviews, "per_business", 1, 50, 5)
        cfg.min_review_len = _int_in(reviews, "min_len", 5, 200, 20)
        cfg.max_review_len = _int_in(reviews, "max_len", 50, 4000, 600)

        # runtime
        cfg.website_workers = _int_in(run, "website_workers", 1, 12, 4)
        cfg.playwright_workers = _int_in(run, "playwright_workers", 1, 8, 2)
        return cfg.resolve()


def _strlist(value: Any) -> list[str]:
    if not value:
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _opt_str(value: Any, default: str) -> str:
    return str(value).strip() if isinstance(value, str) and value.strip() else default


def _int_in(section: dict[str, Any], key: str, lo: int, hi: int, default: int) -> int:
    raw = section.get(key)
    if raw is None:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"'{key}' must be an integer, got {raw!r}") from None
    if not (lo <= val <= hi):
        raise ConfigError(f"'{key}'={val} out of range [{lo}, {hi}]")
    return val
