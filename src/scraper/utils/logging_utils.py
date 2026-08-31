"""Structured, human-readable logging setup."""
from __future__ import annotations

import logging
import sys

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
           "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure the root 'scraper' logger with a simple, readable handler."""
    logger = logging.getLogger("scraper")
    logger.setLevel(_LEVELS.get(level.upper(), logging.INFO))
    if not logger.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(h)
    return logger
