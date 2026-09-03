"""Smoke tests for the non-programmer server controller."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _usable_bash() -> str | None:
    """Return a bash that actually execs, else None.

    On Windows, `bash` on PATH may be the WSL relay stub
    (C:\\Windows\\System32\\bash.exe), which exits 1 with
    "execvpe(/bin/bash) failed" when no WSL distro is installed. The
    controller itself is POSIX-only; these tests are skipped, not failed,
    where no usable bash exists.
    """
    bash = shutil.which("bash")
    if not bash:
        return None
    try:
        probe = subprocess.run([bash, "-c", "echo ok"], text=True,
                               capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bash if (probe.returncode == 0 and "ok" in probe.stdout) else None


BASH = _usable_bash()

pytestmark = pytest.mark.skipif(BASH is None, reason="no usable POSIX bash")


def _run(command: str, *args: str, **env_overrides: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [BASH, str(ROOT / "server.sh"), command, *args],
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
