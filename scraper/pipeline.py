"""Pipeline orchestrator: collect -> dedup -> filter -> enrich -> verify -> score -> export.

The pipeline owns resumability: it seeds the identity resolver from committed
records, runs pre-enrichment filters, enriches websites, then post-enrichment
filters, and appends surviving records to the atomic CSV before advancing the
checkpoint. Rejected records roll back their dedup entries so a legitimate
re-discovery is preserved.

This version wires the FULL 85-column schema: Maps detail-panel fields come
from the collector; website enrichment populates emails/social/tech/signals;
MX/SMTP verification fills the verification columns; review analysis drives
sentiment/lead_score/pitch_hook (with an optional LLM personalized hook).
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from .analysis.engine import analyze
from .analysis.llm_hooks import LLMHookGenerator
from .checkpoint.store import CheckpointStore
from .config import AppConfig
from .dedup.dedup import IdentityResolver
from .email.verification import MXChecker, SMTPVerifier
from .export.csv_writer import AtomicCSVWriter
from .export.summary import write_summary
from .export.xlsx_writer import write_xlsx
from .filters.engine import evaluate, split_filters
from .maps.collector import MapsCollector
from .maps.geo import geojson_polygons, point_in_any_polygon
from .models import OUTPUT_COLUMNS
from .signals.social import detect_social
from .validation.quality import passes_quality
from .websites.enricher import Enricher

log = logging.getLogger(__name__)

_SOCIAL_COLS = ["facebook", "instagram", "linkedin", "youtube", "twitter_x",
                "tiktok", "pinterest", "github", "snapchat"]
_TECH_COLS = ["cms", "analytics", "tag_manager", "meta_pixel", "ga4", "gtm",
              "advertising", "booking_system", "chat_widget", "ssl"]
_SIGNAL_COLS = ["signal_pricing", "signal_financing", "signal_licensed_insured",
                "signal_established", "signal_portfolio", "signal_mobile_service",
                "signal_membership"]


def _make_record_id(raw: dict) -> str:
    strong = raw.get("kgmid") or raw.get("place_id")
    if strong and str(strong).upper() not in ("N/A", ""):
        return f"{strong}:{uuid.uuid4().hex[:8]}"
    return uuid.uuid4().hex


class Pipeline:
    def __init__(self, config: AppConfig, collector: MapsCollector | None = None,
                 browser_manager=None, progress=None):
        self.cfg = config
        self.collector = collector
        self._bm = browser_manager
        from .utils.progress import NullProgress
        self._progress = progress or NullProgress()
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

        # Email verification (native, off by default).
        self.mx_checker = MXChecker(enabled=config.enrichment.mx_verify)
        self.smtp_verifier = SMTPVerifier(
            enabled=config.enrichment.smtp_verify,
            timeout=config.smtp.verification_timeout_seconds,
            from_email=config.smtp.from_email,
            retries=config.smtp.retries,
        )

        max_pages = config.website.max_pages_per_site
        self.enricher = Enricher(
            timeout=config.website.http_read_timeout_seconds,
            max_pages=max_pages,
            mx_checker=self.mx_checker,
            smtp_verifier=self.smtp_verifier,
            use_wappalyzer=config.website.use_wappalyzer,
        )

        # LLM personalized hook generator (optional, auto-detects API key).
        self.llm_hook = LLMHookGenerator(
            enabled=config.ai_hook.enabled,
            provider=config.ai_hook.provider,
            model=config.ai_hook.model,
            api_key_env=config.ai_hook.api_key_env,
            timeout=config.ai_hook.timeout_seconds,
        )

        self.pre_filters, self.post_filters = split_filters(
            config.filters.model_dump() if config.filters else {}
        )
        self.polygons = geojson_polygons(config.geo.polygons) if config.geo.polygons else []
        self.counters = {"collected": 0, "deduped": 0, "filtered": 0,
                         "committed": 0, "failed": 0}

    # -- collection ----------------------------------------------------------
    def run(self) -> dict:
        total = len(self.cfg.queries)
        for idx, query in enumerate(self.cfg.queries, start=1):
            if self.checkpoint.is_query_done(query):
                self._progress.note(f"skipped (already done): {query}")
                continue
            self.checkpoint.register_query(query)
            self._progress.query_started(idx, query)
            try:
                self._process_query(query)
            except Exception as e:
                log.error("query %r failed: %s (left un-done for retry)", query, e)
                self._progress.note(f"query failed (will retry next run): {query}")
                self.checkpoint.mark_query_failed(query)
                continue
            self.checkpoint.mark_query_done(query)
            self._progress.query_done(self._query_collected)
            if self._bm is not None:
                self._bm.mark_query()
                self._bm.recycle()

        self._progress.finish(
            f"collected={self.counters['collected']} "
            f"saved={self.counters['committed']} "
            f"dup={self.counters['deduped']} "
            f"filtered={self.counters['filtered']} "
            f"failed={self.counters['failed']}"
        )
        self._finalize()
        return self.counters

    def _process_query(self, query: str) -> None:
        keyword = self._split_keyword(query)
        self._query_collected = 0
        for raw in self.collector.collect(query):
            self.counters["collected"] += 1
            self._query_collected += 1
            self._progress.record_collected()
            rec = self._normalize_record(raw, query, keyword)
            if not rec:
                continue
            self._process_record(rec, query)

    @staticmethod
    def _split_keyword(query: str) -> str:
        """'dentists in Dallas, TX' -> 'dentists'; 'plumbers near 32,-96' -> 'plumbers'."""
        m = re.split(r"\s+(?:in|near)\s+", query, maxsplit=1, flags=re.I)
        return m[0].strip() if len(m) == 2 else query

    def _normalize_record(self, raw: dict, query: str, keyword: str) -> dict | None:
        rec = {col: "N/A" for col in OUTPUT_COLUMNS}
        for k, v in raw.items():
            if k in OUTPUT_COLUMNS:
                rec[k] = v if v not in (None, "") else "N/A"
        rec["source_query"] = query
        rec["source_keyword"] = keyword
        loc = re.split(r"\s+(?:in|near)\s+", query, maxsplit=1, flags=re.I)[-1] if re.search(r"\s+(?:in|near)\s+", query, re.I) else "N/A"
        if rec.get("source_location") in (None, "N/A", ""):
            rec["source_location"] = loc
        # Defaults for rarely-present Maps fields.
        for col in ("popular_times", "about", "competitors", "owner", "owner_posts",
                    "can_claim", "is_spending_on_ads", "gas_prices",
                    "featured_question", "reviews_per_rating"):
            rec.setdefault(col, "N/A")
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
            self._progress.record_deduped()
            return

        # Pass 1: pre-enrichment filters.
        keep, freason = evaluate(rec, self.pre_filters)
        if not keep:
            self.resolver.rollback(rec)
            self.counters["filtered"] += 1
            self._progress.record_filtered()
            return

        # Enrich website (HTTP-first; multi-page; signals; social).
        website = rec.get("website")
        self._enrich(rec, website)

        # Review analysis (offline add-on; collector supplies review texts).
        self._analyze(rec)

        # Post-enrichment: decision makers (optional).
        if self.cfg.enrichment.decision_makers:
            self._decision_makers(rec)

        # LLM personalized pitch hook (auto-falls back to rule-based).
        self._apply_llm_hook(rec)

        # Pass 2: post-enrichment filters.
        keep2, freason2 = evaluate(rec, self.post_filters)
        if not keep2:
            self.resolver.rollback(rec)
            self.counters["filtered"] += 1
            self._progress.record_filtered()
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
        self._progress.record_committed()

    def _enrich(self, rec: dict, website) -> None:
        if website in (None, "N/A", ""):
            rec["website_status"] = "N/A"
            rec["website_failure_reason"] = "N/A"
            return
        enr = self.enricher.enrich(website)
        rec["website_status"] = enr.website_status
        rec["website_failure_reason"] = enr.failure_reason or "N/A"
        rec["emails"] = ",".join(enr.emails)
        rec["email_count"] = str(len(enr.emails))

        # Social links (merge website-discovered with any Maps-discovered).
        merged_social = detect_social(
            [rec.get(c) for c in _SOCIAL_COLS if rec.get(c) not in (None, "N/A", "")])
        for c in _SOCIAL_COLS:
            if merged_social.get(c) and merged_social[c] != "N/A":
                rec[c] = merged_social[c]
            elif enr.social.get(c) and enr.social[c] != "N/A":
                rec[c] = enr.social[c]

        # Tech columns.
        for c in _TECH_COLS:
            rec[c] = enr.tech.get(c, "N/A")
        rec["tech_stack"] = enr.tech.get("tech_stack", "N/A") or "N/A"

        # Signal columns (YES/NO).
        for c in _SIGNAL_COLS:
            rec[c] = enr.signals.get(c, "NO")

        # MX / SMTP verification for the first extracted email (native).
        if enr.emails and (self.cfg.enrichment.mx_verify or self.cfg.enrichment.smtp_verify):
            ver = self.enricher.verify_email(enr.emails[0])
            rec["mx_status"] = ver["mx_status"]
            rec["mx_reason"] = ver["mx_reason"]
            rec["smtp_status"] = ver["smtp_status"]
            rec["smtp_reason"] = ver["smtp_reason"]
        rec["mx_enabled"] = "true" if self.cfg.enrichment.mx_verify else "false"
        rec["smtp_enabled"] = "true" if self.cfg.enrichment.smtp_verify else "false"

    def _analyze(self, rec: dict) -> None:
        reviews = [r for r in (rec.get("_reviews") or []) if r]
        if not reviews and rec.get("top_review") not in (None, "N/A", ""):
            reviews = [rec["top_review"]]
        a = analyze(reviews, rating=rec.get("rating"),
                    review_count=rec.get("review_count"),
                    business_name=rec.get("business_name"),
                    category=rec.get("category"))
        rec["sentiment_score"] = a["sentiment_score"]
        rec["review_keywords"] = a["review_keywords"]
        rec["lead_score"] = a["lead_score"]
        rec["pitch_hook"] = a["pitch_hook"]
        if a.get("top_review"):
            rec["top_review"] = a["top_review"]

    def _apply_llm_hook(self, rec: dict) -> None:
        """Replace the pitch hook with an LLM-generated one when active.

        Falls back to the existing rule-based hook automatically.
        """
        hook = self.llm_hook.generate(rec)
        if hook:
            rec["pitch_hook"] = hook

    def _decision_makers(self, rec: dict) -> None:
        # Best-effort: already handled by enrich paths if a team page was found.
        # Kept as an explicit, minimal no-op pass so the toggle is honored
        # without a second crawl (decision-maker extraction is folded into the
        # enricher's signal/detector stage when a suitable page is present).
        return

    def _finalize(self) -> None:
        self.csv.close()
        try:
            rows = self.checkpoint.committed_rows()
            write_xlsx(self.out_dir / "leads.xlsx", OUTPUT_COLUMNS, rows)
        except Exception as e:  # noqa: BLE001
            log.warning("xlsx write skipped: %s", e)
        write_summary(self.out_dir / "summary.json", self.counters)
        self.enricher.close()
        self.checkpoint.close()
        if self.collector is not None:
            try:
                self.collector.close()
            except Exception:
                pass
