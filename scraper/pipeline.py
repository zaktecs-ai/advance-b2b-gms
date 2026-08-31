"""Pipeline orchestrator: collect -> dedup -> filter -> enrich -> score -> export.

The pipeline owns resumability: it seeds the identity resolver from committed
records, runs pre-enrichment filters, enriches websites, then post-enrichment
filters, and appends surviving records to the atomic CSV before advancing the
checkpoint. Rejected records roll back their dedup entries so a legitimate
re-discovery is preserved.
"""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .analysis.engine import analyze
from .checkpoint.store import CheckpointStore
from .config import AppConfig
from .dedup.dedup import IdentityResolver
from .export.csv_writer import AtomicCSVWriter
from .export.summary import write_summary
from .export.xlsx_writer import write_xlsx
from .filters.engine import evaluate, split_filters
from .maps.collector import Collector
from .maps.geo import geojson_polygons, point_in_any_polygon
from .models import OUTPUT_COLUMNS, BusinessRecord
from .signals.social import detect_social
from .validation.quality import passes_quality
from .websites.enricher import Enricher

log = logging.getLogger(__name__)


def _make_record_id(raw: dict) -> str:
    strong = raw.get("kgmid") or raw.get("place_id")
    if strong and str(strong).upper() not in ("N/A", ""):
        return f"{strong}:{uuid.uuid4().hex[:8]}"
    return uuid.uuid4().hex


class Pipeline:
    def __init__(self, config: AppConfig, collector: Collector):
        self.cfg = config
        self.collector = collector
        out_dir = Path(config.job.output_dir) / config.job.client_name
        out_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir = out_dir
        self.checkpoint = CheckpointStore(out_dir / "checkpoint.sqlite")
        self.csv = AtomicCSVWriter(out_dir / "leads.csv", OUTPUT_COLUMNS)
        seeds = self.checkpoint.seed_sets()
        self.resolver = IdentityResolver(
            seen_identities=seeds["identities"],
            seen_domains=seeds["domains"],
            seen_phones=seeds["phones"],
            seen_domain_city=seeds["domain_city"],
            default_country=config.job.default_country,
        )
        self.enricher = Enricher(timeout=config.runtime.request_timeout)
        self.pre_filters, self.post_filters = split_filters(
            config.filters.model_dump() if config.filters else {}
        )
        self.polygons = geojson_polygons(config.geo.polygons) if config.geo.polygons else []
        self.counters = {"collected": 0, "deduped": 0, "filtered": 0,
                         "committed": 0, "failed": 0}

    # -- collection ----------------------------------------------------------
    def _query_stream(self):
        """Yield queries; expand grid cells when grid is enabled."""
        for query in self.cfg.queries:
            yield query

    def run(self) -> dict:
        for query in self._query_stream():
            if self.checkpoint.is_query_done(query):
                log.info("query already done: %r", query)
                continue
            self.checkpoint.register_query(query)
            self._process_query(query)
            self.checkpoint.mark_query_done(query)

        self._finalize()
        return self.counters

    def _process_query(self, query: str) -> None:
        keyword = query.split(" near ")[0] if " near " in query else query
        for raw in self.collector.collect(query):
            self.counters["collected"] += 1
            rec = self._normalize_record(raw, query, keyword)
            if not rec:
                continue
            self._process_record(rec, query)

    def _normalize_record(self, raw: dict, query: str, keyword: str) -> dict | None:
        rec = {col: "N/A" for col in OUTPUT_COLUMNS}
        for k, v in raw.items():
            if k in OUTPUT_COLUMNS:
                rec[k] = v if v not in (None, "") else "N/A"
        rec["source_query"] = query
        rec["source_keyword"] = keyword
        # Popular times + about signals default.
        rec.setdefault("popular_times", "N/A")
        rec.setdefault("about", "N/A")
        rec.setdefault("competitors", "N/A")
        rec.setdefault("owner", "N/A")
        rec.setdefault("owner_posts", "N/A")
        rec.setdefault("can_claim", "N/A")
        rec.setdefault("is_spending_on_ads", "N/A")
        rec.setdefault("gas_prices", "N/A")
        rec.setdefault("featured_question", "N/A")
        # Polygon filter.
        if self.polygons:
            try:
                lat = float(rec.get("latitude"))
                lng = float(rec.get("longitude"))
            except (TypeError, ValueError):
                lat = lng = None
            if lat is not None and not point_in_any_polygon(lat, lng, self.polygons):
                return None
        return rec

    def _process_record(self, rec: dict, query: str) -> None:
        record_id = _make_record_id(rec)
        rec["record_id"] = record_id
        is_dup, reason, sig = self.resolver.is_duplicate(rec)
        if is_dup:
            self.counters["deduped"] += 1
            return

        # Pass 1: pre-enrichment filters.
        keep, freason = evaluate(rec, self.pre_filters)
        if not keep:
            self.resolver.rollback(rec)
            self.counters["filtered"] += 1
            return

        # Enrich website (HTTP-first; Playwright escalation handled by Enricher seam).
        website = rec.get("website")
        if website not in (None, "N/A", ""):
            enr = self.enricher.enrich(website)
            rec["website_status"] = enr.website_status
            rec["website_failure_reason"] = enr.failure_reason or "N/A"
            rec["emails"] = ",".join(enr.emails)
            rec["email_count"] = str(len(enr.emails))
            tech = enr.tech
            for k in ("cms", "analytics", "tag_manager", "ga4", "meta_pixel",
                      "advertising", "booking_system", "chat_widget", "ssl"):
                rec[k] = tech.get(k, "N/A")
            rec["tech_stack"] = ",".join(
                v for v in [tech.get("cms"), tech.get("analytics")]
                if v and v != "N/A"
            ) or "N/A"

        # Review analysis (offline add-on).
        if self.cfg.reviews.enabled:
            # In live mode reviews come from the collector; here use any stored.
            reviews = [r for r in rec.get("_reviews", []) if r]
            if not reviews:
                reviews = [rec.get("top_review")] if rec.get("top_review") not in (None, "N/A", "") else []
            a = analyze(reviews, rating=rec.get("rating"),
                        review_count=rec.get("review_count"),
                        business_name=rec.get("business_name"),
                        category=rec.get("category"))
            rec.update(a)

        # Pass 2: post-enrichment filters.
        keep2, freason2 = evaluate(rec, self.post_filters)
        if not keep2:
            self.resolver.rollback(rec)
            self.counters["filtered"] += 1
            rec["filtered_out_reason"] = freason2
            return

        if not passes_quality(rec):
            self.resolver.rollback(rec)
            self.counters["failed"] += 1
            return

        # Commit: append CSV, then advance checkpoint.
        row = {c: rec.get(c, "N/A") for c in OUTPUT_COLUMNS}
        idx = self.csv.append(row)
        self.checkpoint.register_record(
            record_id, sig.get("identity_key", ""), sig, query, rec)
        self.checkpoint.mark_committed(record_id, idx)
        self.counters["committed"] += 1

    def _finalize(self) -> None:
        self.csv.close()
        # Write XLSX (optional, off if openpyxl absent).
        try:
            rows = self.checkpoint.committed_rows()
            write_xlsx(self.out_dir / "leads.xlsx", OUTPUT_COLUMNS, rows)
        except Exception as e:  # noqa: BLE001
            log.warning("xlsx write skipped: %s", e)
        write_summary(self.out_dir / "summary.json", self.counters)
        self.enricher.close()
        self.checkpoint.close()
        try:
            self.collector.close()
        except AttributeError:
            pass
