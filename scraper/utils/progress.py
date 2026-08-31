"""Human-friendly console progress reporter.

Prints a single, self-updating status line (carriage-return) plus one-line
event messages for major milestones. It is deliberately independent of the
logging system so the terminal stays clean: no timestamps, no levels, no module
names — just what the operator cares about (which query, how many results,
elapsed time, ETA).

All output goes to stdout; errors still go to stderr via logging.
"""
from __future__ import annotations

import sys
import time
from datetime import timedelta


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    return str(timedelta(seconds=int(seconds)))


class ProgressConsole:
    """Minimal, clean progress reporter for a single scrape run."""

    def __init__(self, total_queries: int, quiet: bool = False):
        self.total_queries = total_queries
        self.quiet = quiet
        self._started = time.monotonic()
        # Counters driven by the pipeline.
        self.current_query = 0
        self.current_query_text = ""
        self.collected = 0
        self.committed = 0
        self.deduped = 0
        self.filtered = 0
        self._last_line_len = 0
        self._last_render = 0.0
        self._render_min_interval = 0.2  # seconds — throttle the status line

    def _write(self, text: str, overwrite: bool = False) -> None:
        if self.quiet:
            return
        if overwrite:
            # Erase the previous line, then rewrite.
            pad = " " * max(0, self._last_line_len - len(text))
            sys.stdout.write("\r" + text + pad)
            sys.stdout.flush()
            self._last_line_len = len(text)
        else:
            # A persistent event line.
            self._clear_line()
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
            self._last_line_len = 0

    def _clear_line(self) -> None:
        if self._last_line_len:
            sys.stdout.write("\r" + " " * self._last_line_len + "\r")
            self._last_line_len = 0

    def _elapsed(self) -> str:
        return _fmt_duration(time.monotonic() - self._started)

    def _eta(self) -> str:
        if self.current_query <= 0 or self.total_queries <= 0:
            return ""
        elapsed = time.monotonic() - self._started
        per_query = elapsed / self.current_query
        remaining = (self.total_queries - self.current_query) * per_query
        return _fmt_duration(remaining)

    # -- public API (called by the pipeline) --------------------------------
    def query_started(self, index: int, query_text: str) -> None:
        self.current_query = index
        self.current_query_text = query_text
        self._write(
            f"[{index}/{self.total_queries}] Query: {query_text}", overwrite=False)

    def query_done(self, results: int) -> None:
        self._write(
            f"  -> done (collected {results} results) in {self._elapsed()}", overwrite=False)

    def record_committed(self) -> None:
        self.committed += 1
        self._render_status()

    def record_collected(self) -> None:
        self.collected += 1
        self._render_status()

    def record_deduped(self) -> None:
        self.deduped += 1
        self._render_status()

    def record_filtered(self) -> None:
        self.filtered += 1
        self._render_status()

    def note(self, text: str) -> None:
        """A one-off informative line (e.g. 'bot challenge — cooling down')."""
        self._write(f"  ! {text}", overwrite=False)

    def _render_status(self) -> None:
        now = time.monotonic()
        if now - self._last_render < self._render_min_interval:
            return
        self._last_render = now
        eta = self._eta()
        eta_part = f" | ETA ~{eta}" if eta else ""
        line = (
            f"  progress: {self.collected} collected, {self.committed} saved, "
            f"{self.deduped} dup, {self.filtered} filtered | "
            f"elapsed {self._elapsed()}{eta_part}"
        )
        self._write(line, overwrite=True)

    def finish(self, summary: str) -> None:
        self._clear_line()
        self._write(f"Done in {self._elapsed()}. {summary}", overwrite=False)


class NullProgress:
    """No-op progress sink (used when quiet / non-TTY)."""

    def __getattr__(self, name):
        return lambda *a, **k: None
