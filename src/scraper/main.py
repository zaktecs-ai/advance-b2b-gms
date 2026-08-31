"""CLI entrypoint for the standalone B2B lead scraper.

Usage:
    python -m scraper.main --config config.yaml [--demo]
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Config, ConfigError
from .pipeline import Job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="advance-b2b-gms", description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--demo", action="store_true",
                        help="Run with built-in demo data (no Google Maps fetch)")
    args = parser.parse_args(argv)

    try:
        cfg = Config.from_file(args.config)
    except ConfigError as e:
        print(f"[config error] {e}", file=sys.stderr)
        return 2

    job = Job(cfg, demo=args.demo)
    try:
        summary = job.run()
    except Exception as e:  # noqa: BLE001 - surface and exit non-zero
        print(f"[run error] {e}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
