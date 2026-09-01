"""Config validation: env resolution, ranges, queries required."""
import pytest

from scraper.config import ConfigError, load_config
from scraper.maps.collector import DemoCollector


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_minimal_config(tmp_path):
    p = _write(tmp_path, "queries:\n  - 'dentists in Dallas'\n")
    cfg = load_config(p)
    assert cfg.queries == ["dentists in Dallas"]
    assert cfg.maps.zoom == 16


def test_missing_queries(tmp_path):
    p = _write(tmp_path, "maps:\n  headless: true\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_default_country_is_normalized(tmp_path):
    p = _write(tmp_path, "queries: ['x']\njob:\n  default_country: pk\n")
    assert load_config(p).job.default_country == "PK"


def test_default_country_must_be_alpha2(tmp_path):
    p = _write(tmp_path, "queries: ['x']\njob:\n  default_country: USA\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_zoom_out_of_range(tmp_path):
    p = _write(tmp_path, "queries: ['x']\nmaps:\n  zoom: 99\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_env_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_CLIENT", "acme")
    p = _write(tmp_path, "queries: ['x']\njob:\n  client_name: '${MY_CLIENT}'\n")
    cfg = load_config(p)
    assert cfg.job.client_name == "acme"


def test_missing_env(tmp_path):
    p = _write(tmp_path, "queries: ['x']\njob:\n  client_name: '${NOT_SET_VAR}'\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_nonexistent_file():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.yaml")


def test_demo_collector_yields(tmp_path):
    c = DemoCollector()
    recs = list(c.collect("dentists in Dallas"))
    assert len(recs) == 3
    assert all("business_name" in r for r in recs)


def test_config_template_has_no_dead_sections():
    # F25: every top-level config.yaml section must map to an AppConfig field.
    import pathlib
    import yaml
    import scraper.config as C
    template = yaml.safe_load(pathlib.Path("config.yaml").read_text(encoding="utf-8"))
    model_fields = set(C.AppConfig.model_fields)
    for k in template:
        assert k in model_fields, f"dead config section: {k}"


def test_smtp_from_email_must_be_real_when_enabled():
    # F23: enabling SMTP with a placeholder sender must fail fast.
    from scraper.config import SMTPConfig
    SMTPConfig(enabled=True, from_email="verify@myrealdomain.com")  # ok
    SMTPConfig(enabled=False, from_email="verify@example.com")     # ok when disabled
    with pytest.raises(Exception):
        SMTPConfig(enabled=True, from_email="verify@example.com")
