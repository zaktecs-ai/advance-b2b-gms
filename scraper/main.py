"""CLI entrypoints: run a scrape, or serve the REST API + Web UI."""
from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_config
from .maps.collector import DemoCollector, PlaywrightCollector
from .pipeline import Pipeline
from .utils.logging_utils import setup_logging


def _build_collector(config, demo: bool):
    if demo:
        return DemoCollector()
    m = config.maps
    return PlaywrightCollector(
        hl=m.hl, gl=m.gl, headless=m.headless, zoom=m.zoom,
        max_results=m.max_results_per_query, max_scrolls=m.max_scrolls,
        scroll_pause=m.scroll_pause_seconds,
    )


def run(config_path: str, demo: bool, serve: bool) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"[config error] {e}", file=sys.stderr)
        return 1

    log = setup_logging("INFO")

    if serve:
        from .server.app import create_app
        app = create_app(config)
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
        return 0

    collector = _build_collector(config, demo)
    pipeline = Pipeline(config, collector)
    try:
        counters = pipeline.run()
    finally:
        pass

    log.info("Run complete: %s", counters)
    print(f"Done. Collected={counters['collected']} "
          f"Deduped={counters['deduped']} Filtered={counters['filtered']} "
          f"Committed={counters['committed']} Failed={counters['failed']}")
    print(f"Output: {config.job.output_dir}/{config.job.client_name}/leads.csv")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scraper", description="Advance B2B GMS")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--demo", action="store_true", help="Offline demo mode (sample records)")
    parser.add_argument("--serve", action="store_true", help="Start the REST API + Web UI")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__
        print(__version__)
        return 0

    return run(args.config, args.demo, args.serve)


if __name__ == "__main__":
    raise SystemExit(main())
