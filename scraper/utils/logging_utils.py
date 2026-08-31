"""Structured logging with a clean console + a full debug file.

Two distinct sinks keep the terminal readable:

  * **Console** — only fatal errors surface (progress is printed directly, not
    through logging), so it reads like a status board with no noise.
  * **File** — the full picture (``run.log`` under the job output dir): every
    INFO / WARNING / ERROR / DEBUG line, with timestamps and logger names, for
    post-run triage.

This split means a normal run shows clean, useful progress while every warning
and error is still captured on disk without cluttering the screen.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
           "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}


class _QuietConsoleHandler(logging.Handler):
    """Console handler that only shows ERROR+ — progress is printed directly
    (not through logging) so it stays clean and predictable."""

    def emit(self, record: logging.Record) -> None:
        if record.levelno < logging.ERROR:
            return
        try:
            msg = self.format(record)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
        except Exception:  # pragma: no cover — logging must never crash
            pass


def setup_logging(level: str = "INFO", log_file: Optional[str | Path] = None) -> logging.Logger:
    """Configure the root 'scraper' logger.

    ``log_file``: when provided, full logs (>= ``level``) are written there.
    The console only surfaces ERROR+ so ordinary progress stays clean.
    """
    logger = logging.getLogger("scraper")
    logger.setLevel(_LEVELS.get(level.upper(), logging.INFO))
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console: quiet (errors only).
    console = _QuietConsoleHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File: everything.
    if log_file:
        p = Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(p), encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
