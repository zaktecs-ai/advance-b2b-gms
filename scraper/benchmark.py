"""Controlled benchmark: mock-website workload through the REAL pipeline.

Starts a set of local mock business websites (HTTP), feeds them to the
Pipeline through a stub Maps collector, and reports:

  - throughput (records/second, committed rows)
  - average / max enrichment latency
  - queue depth peak (backpressure observation)
  - worker utilization (fraction of pool time spent enriching)

Usage:
    python -m scraper.benchmark                     # 60 sites, 20 workers
    python -m scraper.benchmark --sites 120 --workers 24
    python -m scraper.benchmark --compare           # 8 vs 16 vs 24 workers

The mock sites live on distinct localhost ports (one port == one rate-limit
domain) so the per-domain gate behaves exactly as it would against real
distinct websites. No proxies, no anti-bypass: this measures PIPELINE
throughput only.
"""
from __future__ import annotations

import argparse
import shutil
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .config import (AppConfig, ConcurrencyConfig, JobConfig, SummaryConfig,
                     WebsiteConfig)
from .pipeline import Pipeline

_PAGE = """<!doctype html><html><head><title>{name}</title></head><body>
<h1>{name}</h1><p>Trusted local {trade} serving {city} since 1998.</p>
<nav>
 <a href="/site{i}/contact">Contact us</a>
 <a href="/site{i}/about">About our team</a>
 <a href="/site{i}/services">Services and pricing</a>
</nav>
<p>Call {phone} for emergency service 24/7. Licensed and insured.</p>
<div>We also serve nearby neighborhoods with same-day appointments.</div>
</body></html>"""

_CONTACT = """<!doctype html><html><head><title>Contact {name}</title></head>
<body><h1>Contact {name}</h1>
<p>Email us at info@site{i}.example or office@site{i}.example</p>
<p>Phone: {phone}</p>
<a href="https://facebook.com/site{i}">Facebook</a>
<a href="https://instagram.com/site{i}">Instagram</a>
<a href="https://linkedin.com/company/site{i}">LinkedIn</a>
<p>Address: {i} Main Street, {city}</p>
<p>Book online or call for a free quote. Emergency service available.</p>
</body></html>"""

_ABOUT = """<!doctype html><html><head><title>About {name}</title></head>
<body><h1>About {name}</h1>
<p>Our team of certified {trade} technicians has served {city} for decades.
We are licensed, insured, and established leaders with a full portfolio of
residential and commercial work. Member of the local chamber since 2001.</p>
</body></html>"""

_SERVICES = """<!doctype html><html><head><title>Services</title></head>
<body><h1>Services</h1>
<p>Pricing, financing, and membership plans available. Mobile service across
the metro area. Emergency plumber dispatch, drain cleaning, water heaters.</p>
</body></html>"""


class _MockSiteHandler(BaseHTTPRequestHandler):
    """Serves a fleet of mock business websites under /site{i}/."""

    latency: float = 0.05

    def log_message(self, *args):  # silence per-request stderr noise
        pass

    def do_GET(self):
        time.sleep(self.latency)
        parts = urlparse(self.path)
        segs = [s for s in parts.path.split("/") if s]
        if len(segs) >= 2 and segs[0] == "site":
            try:
                i = int(segs[1])
            except ValueError:
                i = 0
            page = segs[2] if len(segs) >= 3 else ""
            body = self._page(i, page)
            if body is not None:
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    @staticmethod
    def _ctx(i: int) -> dict:
        return {"i": i, "name": f"Benchmark Business {i}",
                "trade": "plumbing", "city": f"Metro City {i}",
                "phone": f"+1 555 010 {i:04d}"}

    def _page(self, i: int, page: str):
        ctx = self._ctx(i)
        if page in ("", "index.html"):
            return _PAGE.format(**ctx)
        if page == "contact":
            return _CONTACT.format(**ctx)
        if page == "about":
            return _ABOUT.format(**ctx)
        if page == "services":
            return _SERVICES.format(**ctx)
        return None


class _MockSites:
    """A fleet of mock servers. By default EACH SITE gets its own port —
    one port == one distinct rate-limit domain, exactly like real businesses
    on distinct domains. (Fewer ports would make the per-domain gate serialize
    unrelated sites and distort the measurement.)"""

    def __init__(self, sites: int, servers: int | None = None,
                 latency: float = 0.05):
        self.sites = sites
        self.servers: list[ThreadingHTTPServer] = []
        self.ports: list[int] = []
        _MockSiteHandler.latency = latency
        n = min(sites, 256) if servers is None else min(max(1, servers), sites)
        for _ in range(n):
            srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockSiteHandler)
            t = threading.Thread(target=srv.serve_forever,
                                 kwargs={"poll_interval": 0.05}, daemon=True)
            t.start()
            self.servers.append(srv)
            self.ports.append(srv.server_address[1])

    def url_for(self, i: int) -> str:
        port = self.ports[i % len(self.ports)]
        return f"http://127.0.0.1:{port}/site{i}/"

    def stop(self):
        # Shutdown all servers in PARALLEL: shutdown() waits for the serving
        # loop to wake from its poll interval, so doing it sequentially costs
        # ~0.5s per server and dominates the benchmark wall clock.
        stoppers = [threading.Thread(target=srv.shutdown, daemon=True)
                    for srv in self.servers]
        for t in stoppers:
            t.start()
        for t in stoppers:
            t.join(timeout=5.0)
        for srv in self.servers:
            srv.server_close()


class _StubCollector:
    """Maps-shaped collector: yields one listing per mock website."""

    def __init__(self, fleet: _MockSites):
        self._fleet = fleet

    def collect(self, query: str):
        for i in range(self._fleet.sites):
            yield {
                "business_name": f"Benchmark Business {i}",
                "website": self._fleet.url_for(i),
                "phone": f"+1 555 010 {i:04d}",
                "city": f"Metro City {i}",
                "category": "plumber",
                "rating": "4.5",
                "review_count": "120",
                "_position": i + 1,
                "_total": self._fleet.sites,
            }

    def close(self):
        pass


def run_benchmark(sites: int = 60, workers: int = 20,
                  latency: float = 0.05, per_domain: int = 1,
                  out_dir: str = "output") -> dict:
    """Run one benchmark pass; returns counters + throughput metrics."""
    fleet = _MockSites(sites, latency=latency)
    client = f"bench_w{workers}"
    shutil.rmtree(f"{out_dir}/{client}", ignore_errors=True)
    config = AppConfig(
        queries=["plumbers in Benchmark Metro"],
        job=JobConfig(client_name=client, output_dir=out_dir),
        website=WebsiteConfig(use_wappalyzer=False, http_retries=1,
                              enable_sitemap=True, max_pages_per_site=3,
                              overall_site_timeout_seconds=30),
        concurrency=ConcurrencyConfig(website_workers=workers,
                                      per_domain_concurrency=per_domain),
        summary=SummaryConfig(enabled=False),
    )
    pipeline = Pipeline(config, _StubCollector(fleet))
    try:
        t0 = time.monotonic()
        counters = pipeline.run()
        elapsed = time.monotonic() - t0
    finally:
        fleet.stop()
        pipeline.close()
    stats = pipeline.runtime_stats()
    stats["sites"] = sites
    stats["workers"] = workers
    stats["wall_seconds"] = round(elapsed, 2)
    stats["counters"] = dict(counters)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="benchmark",
                                 description="Mock-workload pipeline benchmark")
    ap.add_argument("--sites", type=int, default=60,
                    help="number of mock business websites")
    ap.add_argument("--workers", type=int, default=20,
                    help="website_workers for the single-pass mode")
    ap.add_argument("--latency", type=float, default=0.05,
                    help="simulated per-request latency seconds")
    ap.add_argument("--compare", action="store_true",
                    help="run worker-count sweep 8/16/24 and print a table")
    args = ap.parse_args(argv)

    if args.compare:
        runs = [run_benchmark(sites=args.sites, workers=w,
                              latency=args.latency) for w in (8, 16, 24)]
    else:
        runs = [run_benchmark(sites=args.sites, workers=args.workers,
                              latency=args.latency)]

    hdr = (f"{'workers':>7} {'sites':>6} {'wall_s':>7} {'rec/s':>7} "
           f"{'avg_lat':>8} {'max_lat':>8} {'q_peak':>7} {'util':>6} "
           f"{'committed':>10}")
    print(hdr)
    print("-" * len(hdr))
    for r in runs:
        print(f"{r['workers']:>7} {r['sites']:>6} {r['wall_seconds']:>7} "
              f"{r['records_per_second']:>7} {r['avg_enrich_seconds']:>8} "
              f"{r['max_enrich_seconds']:>8} {r['queue_depth_max']:>7} "
              f"{r['worker_utilization']:>6} "
              f"{r['counters']['committed']:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

