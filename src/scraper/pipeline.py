"""Job pipeline: config -> collect -> analyze -> export."""
from __future__ import annotations

import time

from .analysis.engine import analyze
from .config import Config
from .export.demo_data import demo_businesses
from .export.writer import ExportWriter
from .maps.collector import Collector


class Job:
    def __init__(self, cfg: Config, demo: bool = False):
        self.cfg = cfg
        self.demo = demo

    def run(self) -> dict[str, object]:
        start = time.time()
        provider = None
        if self.demo:
            provider = demo_businesses
        collector = Collector(
            queries=self.cfg.queries,
            max_results_per_query=self.cfg.max_results_per_query,
            max_total_results=self.cfg.max_total_results,
            demo_provider=provider,
        )
        export = ExportWriter(self.cfg.output_dir)
        count = 0
        for biz in collector.collect():
            if self.cfg.reviews_enabled:
                analyze(biz, biz.reviews, self.cfg.reviews_per_business)
            export.write(biz)
            count += 1
        export.close()
        elapsed = time.time() - start
        return {
            "client": self.cfg.client_name,
            "output_dir": str(self.cfg.output_dir),
            "records": count,
            "elapsed_seconds": round(elapsed, 2),
        }
