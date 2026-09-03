"""Runtime resource monitoring for the per-campaign summary.json.

Samples the scraper process (and its children — Playwright/Chromium) for CPU
usage and RAM consumption on a background daemon thread. Uses psutil when
available; falls back to stdlib (`resource.getrusage` for peak RSS,
`time.process_time` for average CPU) so a missing optional dependency can
never abort a scrape.
"""
from __future__ import annotations

import threading
import time


class ResourceMonitor:
    """Sample CPU/RAM until :meth:`stop`; returns a metrics dict."""

    def __init__(self, sample_interval_seconds: float = 2.0):
        self._interval = max(0.5, float(sample_interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cpu_max = 0.0
        self._ram_peak_mb = 0.0
        self._ram_last_mb = 0.0
        self._method = "psutil"

    # -- public API ----------------------------------------------------------
    def start(self) -> None:
        try:
            import psutil  # noqa: F401
        except ImportError:
            self._method = "stdlib-fallback"
            self._sample_stdlib()  # single sample for peak RSS
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="resource-monitor")
        self._thread.start()

    def stop(self) -> dict:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=self._interval * 2 + 1)
            self._thread = None
        if self._method == "stdlib-fallback":
            self._sample_stdlib()
        with self._lock:
            return {
                "method": self._method,
                "cpu_max_usage_percent": round(self._cpu_max, 1),
                "ram_consumed_mb": round(self._ram_peak_mb, 1),
                "ram_final_mb": round(self._ram_last_mb, 1),
            }

    # -- samplers --------------------------------------------------------------
    def _loop(self) -> None:
        import psutil

        proc = psutil.Process()
        proc.cpu_percent(interval=None)  # prime the counter
        while not self._stop.is_set():
            self._sample_psutil(proc)
            self._stop.wait(self._interval)

    def _sample_psutil(self, proc) -> None:
        try:
            cpu = proc.cpu_percent(interval=None)
            rss = proc.memory_info().rss
            for child in proc.children(recursive=True):
                try:
                    cpu += child.cpu_percent(interval=None)
                    rss += child.memory_info().rss
                except Exception:  # child may exit between listing and read
                    continue
            with self._lock:
                self._cpu_max = max(self._cpu_max, cpu)
                self._ram_last_mb = rss / (1024 * 1024)
                self._ram_peak_mb = max(self._ram_peak_mb, rss / (1024 * 1024))
        except Exception:
            pass

    def _sample_stdlib(self) -> None:
        """Fallback: peak RSS via getrusage; CPU% approximated from process time."""
        try:
            import resource

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux reports KiB, macOS reports bytes.
            peak_mb = peak / 1024 if peak > 10**7 else peak / (1024 * 1024)
            with self._lock:
                self._method = "stdlib-fallback"
                self._ram_peak_mb = max(self._ram_peak_mb, peak_mb)
                self._ram_last_mb = peak_mb
        except Exception:
            pass
        try:
            cpu_avg = time.process_time() / max(1e-9, time.monotonic()) * 100.0
            with self._lock:
                self._cpu_max = max(self._cpu_max, min(cpu_avg, 100.0 * _cpu_count()))
        except Exception:
            pass


def _cpu_count() -> int:
    try:
        import os
        return max(1, os.cpu_count() or 1)
    except Exception:
        return 1


def host_details() -> dict:
    """Static environment details for the `servers` block of summary.json."""
    import os
    import platform

    details: dict = {
        "name": platform.node() or "localhost",
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "cpu_count_logical": os.cpu_count(),
    }
    try:
        import psutil

        vm = psutil.virtual_memory()
        details["total_ram_mb"] = round(vm.total / (1024 * 1024), 1)
    except Exception:
        details["total_ram_mb"] = None
    return details
