"""Smoke tests for the non-programmer server controller."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(command: str, *args: str, **env_overrides: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [str(ROOT / "server.sh"), command, *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_server_help_and_status():
    help_result = _run("help")
    assert "./server.sh update" in help_result.stdout
    assert "./server.sh run" in help_result.stdout

    status_result = _run("status")
    assert "Scraper is STOPPED" in status_result.stdout


def test_server_demo_uses_custom_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "queries: ['dentists in Dallas']\n"
        f"job:\n  output_dir: '{tmp_path}/out'\n  client_name: controller-test\n",
        encoding="utf-8",
    )
    result = _run("demo", ABGMS_CONFIG=str(config), ABGMS_OUTPUT_DIR=str(tmp_path / "out"))
    assert "Output:" in result.stdout
    assert (tmp_path / "out" / "controller-test" / "leads.csv").exists()
