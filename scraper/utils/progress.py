"""Human-friendly, structured console progress reporter.

Shows, on a clean terminal:

  * a one-time header (job name, total queries, start time)
  * per-query header  ``━━━ [1/20] gyms in Houston, TX ━━━``
  * each collected business as a streamed line: number, local time, name
  * a self-updating footer with totals, remaining queries, elapsed time, ETA

All of it is deliberately independent of the logging system (which routes
warnings/errors to ``run.log``). The terminal stays a readable status board —
no timestamps-in-brackets, no log-levels, no module names.

Output goes to stdout. ``quiet=True`` suppresses everything.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"0:{seconds:02d}"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _truncate(text: str, width: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


class _Color:
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    cyan = "\033[36m"
    green = "\033[32m"
    yellow = "\033[33m"
    magenta = "\033[35m"

    @staticmethod
    def enabled() -> bool:
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    if _Color.enabled():
        return code + text + _Color.reset
    return text


class ProgressConsole:
    """Structured progress reporter for a single scrape run."""

    def __init__(self, total_queries: int, client_name: str = "campaign",
                 quiet: bool = False):
        self.total_queries = total_queries
        self.client_name = client_name
        self.quiet = quiet
        self._started_mono = time.monotonic()
        self._started_wall = datetime.now().strftime("%I:%M %p")

        # global counters
        self.total_collected = 0
        self.total_saved = 0
        self.total_dup = 0
        self.total_filtered = 0

        # per-query
        self.current_query_idx = 0
        self.current_query_text = ""
        self.query_collected = 0
        self.query_saved = 0

        self._last_render = 0.0
        self._render_interval = 0.25  # seconds
        self._header_done = False
        # Only a real TTY gets the clean overwrite/in-place tricks; when output
        # is piped or redirected (tmux log, nohup, cron), we emit plain lines
        # so the log stays clean instead of full of \r / escape codes.
        self._tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    # -- low-level output --------------------------------------------------
    def _print(self, text: str) -> None:
        if self.quiet:
            return
        if self._tty:
            sys.stdout.write("\r\033[2K" + text + "\n")
        else:
            sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def _print_footer(self, text: str) -> None:
        if self.quiet:
            return
        if self._tty:
            sys.stdout.write("\r\033[2K" + text)
        else:
            sys.stdout.write(text + "\n")
        sys.stdout.flush()

    # -- public API (called by pipeline) -----------------------------------
    def _print_header(self) -> None:
        if self._header_done:
            return
        self._header_done = True
        line = _c(_Color.bold, "Advance B2B GMS — Lead Scraper")
        sub = (f"job: {self.client_name}   |   queries: {self.total_queries}   "
               f"|   started {self._started_wall}")
        self._print(f"{line}")
        self._print(_c(_Color.dim, sub))
        self._print(_c(_Color.dim, "─" * 62))

    def query_started(self, index: int, query_text: str) -> None:
        self._print_header()
        self.current_query_idx = index
        self.current_query_text = query_text
        self.query_collected = 0
        self.query_saved = 0
        bar = _c(_Color.cyan, f"[{index}/{self.total_queries}] {query_text}")
        self._print("")
        self._print(_c(_Color.bold, "━━━ " + bar + " ━━━"))

    def business_collected(self, number: int, name: str) -> None:
        """A business was extracted — stream a numbered line with timestamp."""
        self.total_collected += 1
        self.query_collected += 1
        stamp = _c(_Color.dim, _now())
        idx = _c(_Color.green, f"{number:>3}.")
        self._print(f"   {idx}  {stamp}   {_truncate(name, 44)}")
        self._render_footer()

    def business_saved(self) -> None:
        self.total_saved += 1
        self.query_saved += 1
        self._render_footer()

    def business_dup(self) -> None:
        self.total_dup += 1
        self._render_footer()

    def business_filtered(self) -> None:
        self.total_filtered += 1
        self._render_footer()

    def note(self, text: str) -> None:
        self._print(_c(_Color.yellow, f"   ! {text}"))

    def query_done(self) -> None:
        # clear footer, then a per-query summary line
        self._print_footer("")
        summary = (f"   ↳ collected {self.query_collected} · saved {self.query_saved}")
        self._print(_c(_Color.green, summary))
        self._print("")

    def finish(self, failed: int = 0) -> None:
        self._print_footer("")
        self._print("")
        self._print(_c(_Color.bold, "┌─ Run complete ──────────────────────────────"))
        self._print(f"   Total collected : {self.total_collected}")
        self._print(f"   Total saved     : {_c(_Color.green, str(self.total_saved))}")
        self._print(f"   Duplicates      : {self.total_dup}")
        self._print(f"   Filtered        : {self.total_filtered}")
        if failed:
            self._print(f"   Failed          : {_c(_Color.yellow, str(failed))}")
        self._print(f"   Elapsed         : {_fmt_duration(self.elapsed())}")
        self._print(_c(_Color.bold, "└──────────────────────────────────────────────"))

    # -- footer rendering --------------------------------------------------
    def elapsed(self) -> float:
        return time.monotonic() - self._started_mono

    def _eta(self) -> str:
        if self.current_query_idx <= 0 or self.total_queries <= 0:
            return "—"
        per_query = self.elapsed() / self.current_query_idx
        remaining = (self.total_queries - self.current_query_idx) * per_query
        return _fmt_duration(remaining)

    def _remaining_queries(self) -> int:
        return max(0, self.total_queries - self.current_query_idx)

    def _render_footer(self) -> None:
        if self.quiet or not self._tty:
            # Without a real TTY, skip the status footer (business lines +
            # per-query/final summaries already carry the signal for logs).
            return
        now = time.monotonic()
        if now - self._last_render < self._render_interval:
            return
        self._last_render = now
        saved = _c(_Color.green, f"saved {self.total_saved}")
        remaining = _c(_Color.magenta, f"{self._remaining_queries()} left")
        parts = (
            f"▸ {saved}  ·  collected {self.total_collected}  ·  "
            f"{remaining} queries  ·  elapsed {_fmt_duration(self.elapsed())}  ·  "
            f"ETA ~{self._eta()}"
        )
        self._print_footer(parts)


class NullProgress:
    """No-op progress sink (used when quiet / non-TTY)."""

    def __getattr__(self, name):
        return lambda *a, **k: None


def make_progress(total_queries: int, client_name: str = "campaign", quiet: bool = False):
    return ProgressConsole(total_queries, client_name, quiet)
