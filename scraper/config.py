"""Configuration loading, env interpolation, and validation.

One human-editable YAML file drives the engine. Secrets live in ``.env`` and
are referenced as ``${VAR}``. Validation runs *before* any scraping; invalid
values produce a clear error naming the key, the bad value, the allowed range,
and a recommendation for the target VPS.

``pydantic`` enforces types/ranges so mistakes fail fast rather than silently
misbehaving mid-run.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    """User-facing configuration error."""


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class JobConfig(BaseModel):
    client_name: str = "campaign"
    output_dir: str = "output"
    default_country: str = "US"


class MapsConfig(BaseModel):
    headless: bool = True
    hl: str = "en"
    gl: str = "us"
    zoom: int = Field(default=16, ge=0, le=21)
    max_results_per_query: int = Field(default=0, ge=0)
    max_total_results: int = Field(default=0, ge=0)
    scroll_pause_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    max_scrolls: int = Field(default=0, ge=0)  # 0 = scroll until no new results


class ReviewsConfig(BaseModel):
    enabled: bool = True
    per_business: int = Field(default=5, ge=1, le=50)
    min_len: int = Field(default=0, ge=0)
    max_len: int = Field(default=1000, ge=10)


class EnrichmentConfig(BaseModel):
    emails: bool = True
    social: bool = True
    tech_detect: bool = True
    decision_makers: bool = False  # off by default (extra pass)
    mx_verify: bool = False        # off by default
    smtp_verify: bool = False      # off by default
    require_website: bool = False


class RuntimeConfig(BaseModel):
    website_workers: int = Field(default=4, ge=1, le=64)
    playwright_workers: int = Field(default=2, ge=1, le=16)
    request_timeout: float = Field(default=20.0, ge=1.0, le=120.0)
    idle_exit_seconds: int = Field(default=0, ge=0)
    pacing: float = Field(default=1.0, ge=0.0, le=30.0)  # request pacing clock


class GridConfig(BaseModel):
    enabled: bool = False
    cell_size_km: float = Field(default=3.0, ge=0.1, le=50.0)


class GeoConfig(BaseModel):
    polygons: list[Any] = Field(default_factory=list)


class ProxyConfig(BaseModel):
    enabled: bool = False
    urls: list[str] = Field(default_factory=list)
    file: str = ""
    rotation: Literal["round_robin", "random"] = "round_robin"


class AnalysisConfig(BaseModel):
    enabled: bool = True
    lexicon_hint: str = ""  # optional path to a custom sentiment lexicon


class FilterConfig(BaseModel):
    include_all: list[Any] = Field(default_factory=list)
    include_any: list[Any] = Field(default_factory=list)
    exclude_all: list[Any] = Field(default_factory=list)
    exclude_any: list[Any] = Field(default_factory=list)


class AppConfig(BaseModel):
    job: JobConfig = Field(default_factory=JobConfig)
    queries: list[str] = Field(default_factory=list)
    maps: MapsConfig = Field(default_factory=MapsConfig)
    reviews: ReviewsConfig = Field(default_factory=ReviewsConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    grid: GridConfig = Field(default_factory=GridConfig)
    geo: GeoConfig = Field(default_factory=GeoConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)

    @model_validator(mode="after")
    def _check_queries(self):
        if not self.queries:
            raise ValueError("config must define at least one query under `queries:`")
        return self


# ---------------------------------------------------------------------------
# Env resolution + loader
# ---------------------------------------------------------------------------

class _EnvResolver:
    def __init__(self) -> None:
        self._missing: list[str] = []

    def resolve(self, value):
        if isinstance(value, str):
            def _sub(m):
                name = m.group(1)
                val = os.environ.get(name)
                if val is None:
                    self._missing.append(name)
                    return m.group(0)
                return val
            return ENV_VAR_RE.sub(_sub, value)
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        return value


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load, env-resolve, and validate config; returns an AppConfig."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}.")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e
    if raw is None:
        raise ConfigError(f"{path} is empty.")

    if Path(".env").exists():
        load_dotenv(".env")

    resolver = _EnvResolver()
    resolved = resolver.resolve(raw)
    if resolver._missing:
        listed = ", ".join(sorted(set(resolver._missing)))
        raise ConfigError(
            f"Missing environment variable(s): {listed}. Define them in `.env`."
        )

    try:
        cfg = AppConfig.model_validate(resolved)
    except Exception as e:  # noqa: BLE001 — pydantic ValidationError
        raise ConfigError(f"Config validation failed:\n{e}") from e
    return cfg
