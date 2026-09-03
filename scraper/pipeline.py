"""Pipeline orchestrator: collect -> dedup -> filter -> enrich -> verify -> score -> export.

The pipeline owns resumability: it seeds the identity resolver from committed
records, runs pre-enrichment filters, enriches websites, then post-enrichment
filters, and appends surviving records to the atomic CSV before advancing the
checkpoint. Rejected records roll back their dedup entries so a legitimate
re-discovery is preserved.

The pipeline maps a compact, producer-backed schema: Maps detail-panel fields
come from the collector and pure Maps transformations; website enrichment
populates emails/social/tech/signals/decision makers; MX/SMTP verification fills
the verification columns; and review analysis drives
sentiment/lead_score/pitch_hook (with an optional LLM personalized hook).
"""
from __future__ import annotations

import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
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
from .maps.reviews import filter_reviews
from .maps.transform import normalize_listing
from .models import OUTPUT_COLUMNS
from .signals.social import detect_social
from .utils.normalize import normalize_text
from .validation.quality import passes_quality
from .websites.enricher import Enricher

log = logging.getLogger(__name__)

_SOCIAL_COLS = ["facebook", "instagram", "linkedin", "youtube", "twitter_x",
                "tiktok", "pinterest", "github", "snapchat"]
# Columns sourced from the tech detector (string values like cms=WordPress).
_TECH_COLS = ["cms", "analytics", "tag_manager", "ssl"]
# Columns sourced from the signal detector (YES/NO toggle values).
_BOOL_TECH_COLS = ["meta_pixel", "ga4", "gtm", "advertising",
                   "booking_system", "chat_widget"]
_SIGNAL_COLS = ["signal_pricing", "signal_financing", "signal_licensed_insured",
                "signal_established", "signal_portfolio", "signal_mobile_service",
                "signal_membership"]


def _make_record_id(raw: dict) -> str:
    strong = raw.get("kgmid") or raw.get("place_id")
    if strong and str(strong).upper() not in ("N/A", ""):
        # G11: normalize the strong id so every record_id contains exactly
        # ONE colon (the uuid suffix separator). A place_id like
        # `0x864…:0x841…` previously produced a 3-colon id, breaking any
        # consumer that splits record_id on ':'.
        return f"{str(strong).replace(':', '-')}:{uuid.uuid4().hex[:8]}"
    return uuid.uuid4().hex


class SocialOwnershipRegistry:
    """G13: one social profile belongs to exactly one business.

    Production evidence: All American Plumbing (allamerican-plumbing.com)
    exported the SAME facebook/instagram handles as Houston Plumbing Expert —
    a different business row. Whatever the leak path (panel fallback scope,
    agency-built websites linking another client's socials), a social URL
    claimed by two distinct record identities in one run is contaminated.
    The first record to commit keeps it; later claimants are blanked to N/A.
    """

    def __init__(self) -> None:
        self._owner: dict[str, str] = {}

    def claim(self, url: str, record_id: str) -> bool:
        """Claim ``url`` for ``record_id``. False when already owned by another."""
        if not url or url == "N/A":
            return True
        owner = self._owner.get(url)
        if owner is None:
            self._owner[url] = record_id
            return True
        return owner == record_id


class Pipeline:
    def __init__(self, config: AppConfig, collector: MapsCollector | None = None,
                 browser_manager=None, progress=None, proxy_manager=None):
        self.cfg = config
        self.collector = collector
        self._bm = browser_manager
        self._proxy_manager = proxy_manager
        from .utils.progress import NullProgress
        self._progress = progress or NullProgress()
        out_dir = Path(config.job.output_dir) / config.job.client_name
        out_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir = out_dir
        self.checkpoint = CheckpointStore(out_dir / "checkpoint.sqlite")
        self.csv = AtomicCSVWriter(out_dir / "leads.csv", OUTPUT_COLUMNS)
        self._reconcile_csv_with_checkpoint()
        # G13: serial-pass guard against cross-business social contamination.
        self._social_registry = SocialOwnershipRegistry()
        seeds = self.checkpoint.seed_sets()
        self.resolver = IdentityResolver(
            seen_identities=seeds["identities"],
            seen_domains=seeds["domains"],
            seen_phones=seeds["phones"],
            seen_domain_city=seeds["domain_city"],
            domain_first_name=seeds.get("domain_first_name"),
            phone_first_name=seeds.get("phone_first_name"),
            default_country=config.job.default_country,
        )

        # Email verification (native, off by default).
        self.mx_checker = MXChecker(enabled=config.enrichment.mx_verify)
        self.smtp_verifier = SMTPVerifier(
            enabled=config.enrichment.smtp_verify,
            timeout=config.smtp.verification_timeout_seconds,
            from_email=config.smtp.from_email,
            retries=config.smtp.retries,
            max_workers=config.smtp.workers,
        )

        max_pages = config.website.max_pages_per_site
        # Thread the proxy through to the httpx fetcher so enrichment traffic
        # (the bulk of requests) leaves from the configured proxy, not the
        # server's real IP (F27).
        proxy_url = (proxy_manager.httpx_proxy() if proxy_manager is not None
                     else None)
        self.enricher = Enricher(
            timeout=config.website.http_read_timeout_seconds,
            max_pages=max_pages,
            proxies=proxy_url,
            mx_checker=self.mx_checker,
            smtp_verifier=self.smtp_verifier,
            use_wappalyzer=config.website.use_wappalyzer,
            decision_makers=config.enrichment.decision_makers,
            proxy_manager=proxy_manager,
            exclude_selectors=config.enrichment.exclude_selectors,
            max_email_length=config.email.max_email_length,
        )

        # LLM personalized hook generator (optional, auto-detects API key).
        self.llm_hook = LLMHookGenerator(
            enabled=config.ai_hook.enabled,
            provider=config.ai_hook.provider,
            model=config.ai_hook.model,
            api_key_env=config.ai_hook.api_key_env,
            timeout=config.ai_hook.timeout_seconds,
            max_calls=config.ai_hook.max_calls,
            retries=config.ai_hook.retries,
        )

        self.pre_filters, self.post_filters = split_filters(
            config.filters.model_dump() if config.filters else {}
        )
        self.polygons = geojson_polygons(config.geo.polygons) if config.geo.polygons else []
        self.counters = {"collected": 0, "deduped": 0, "filtered": 0,
                         "committed": 0, "failed": 0}

    def _reconcile_csv_with_checkpoint(self) -> None:
        """Trim CSV rows that were durably written but never committed.

        A crash between ``csv.append`` and ``checkpoint.mark_committed`` leaves
        a well-formed CSV row with no committed marker. On startup the
        checkpoint's committed count is the authority for how many CSV rows are
        valid; any uncommitted tail row is trimmed so the same business is
        cleanly re-collected (and dedup-seeded) on this run.
        """
        try:
            committed = self.checkpoint.committed_count()
        except Exception as e:  # noqa: BLE001 — a broken checkpoint must not abort startup
            log.warning("checkpoint count failed; skipping reconcile: %s", e)
            return
        if self.csv.row_count > committed:
            log.warning(
                "reconciling CSV: %d rows written but only %d committed — "
                "trimming %d uncommitted tail row(s)",
                self.csv.row_count, committed, self.csv.row_count - committed,
            )
            self.csv.truncate_to(committed)

    # -- collection ----------------------------------------------------------
    def run(self) -> dict:
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
            self._progress.query_done()
            if self._bm is not None:
                self._bm.mark_query()
                self._bm.recycle()

        self._progress.finish(failed=self.counters['failed'])
        self._finalize()
        return self.counters

    def _process_query(self, query: str) -> None:
        keyword = self._split_keyword(query)
        self._query_collected = 0
        workers = max(1, self.cfg.concurrency.website_workers)
        # Bound the in-flight batch so memory stays flat regardless of how many
        # listings a query yields; the batch is drained in serial order.
        max_batch = workers * 4
        batch: list[tuple[dict, dict]] = []
        for raw in self.collector.collect(query):
            self.counters["collected"] += 1
            self._query_collected += 1
            rec = self._normalize_record(raw, query, keyword)
            if not rec:
                continue
            name = rec.get("business_name") or "—"
            pos = (raw or {}).get("_position") or self._query_collected
            total = (raw or {}).get("_total") or 0
            self._progress.business_collected(pos, name, total)
            prepared = self._dedup_and_prefilter(rec, query)
            if prepared is None:
                continue
            batch.append(prepared)
            if len(batch) >= max_batch:
                self._drain_batch(batch, query, workers)
                batch = []
        if batch:
            self._drain_batch(batch, query, workers)

    def _dedup_and_prefilter(self, rec: dict, query: str) -> tuple[dict, dict] | None:
        """Serial pass: assign id, dedup, and run pre-enrichment filters.

        Returns ``(rec, sig)`` for records that proceed to enrichment, or None
        for duplicates / pre-filter rejects (already counted + rolled back).
        """
        record_id = _make_record_id(rec)
        rec["record_id"] = record_id
        is_dup, _, sig = self.resolver.is_duplicate(rec)
        if is_dup:
            self.counters["deduped"] += 1
            self._progress.business_dup()
            return None
        keep, _ = evaluate(rec, self.pre_filters)
        if not keep:
            self.resolver.rollback(rec)
            self.counters["filtered"] += 1
            self._progress.business_filtered()
            return None
        return rec, sig

    def _drain_batch(self, batch: list[tuple[dict, dict]], query: str, workers: int) -> None:
        """Enrich in parallel (I/O-bound), then filter/commit serially in order.

        The dedup resolver, checkpoint, and CSV writer are NOT thread-safe, so
        only the fetch/enrich/analyze/LLM stage is parallelized; everything that
        mutates shared state stays on this single thread.
        """
        recs = [r for r, _ in batch]
        if workers > 1 and len(recs) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(self._safe_enrich, recs))
        else:
            for rec in recs:
                self._safe_enrich(rec)
        for rec, sig in batch:
            self._commit_stage(rec, query, sig)

    def _safe_enrich(self, rec: dict) -> None:
        """Enrich one record without letting a single failure abort the batch.

        ``ex.map`` re-raises the first worker exception, which previously
        marked the whole query failed and triggered a full re-scrape (F26).
        """
        try:
            self._enrich_and_stage(rec)
        except Exception as e:  # noqa: BLE001
            log.exception("enrich failed for %s", rec.get("business_name"))
            rec["website_status"] = "UNKNOWN"
            rec["website_failure_reason"] = f"enrich_error:{type(e).__name__}"
            rec.setdefault("emails", "N/A")
            rec.setdefault("email_count", 0)

    def _enrich_and_stage(self, rec: dict) -> None:
        """Parallel-safe, record-local mutation: enrich + analyze + LLM hook.

        The shared ``Enricher`` uses an httpx.Client (thread-safe for concurrent
        requests); MX/SMTP and the LLM hook guard their own shared state, so
        concurrent calls only touch their own ``rec`` dict.
        """
        website = rec.get("website")
        self._enrich(rec, website)
        self._analyze(rec)
        self._apply_llm_hook(rec)

    def _commit_stage(self, rec: dict, query: str, sig: dict) -> None:
        """Serial pass: post-enrichment filters, quality gate, then commit."""
        keep2, freason2 = evaluate(rec, self.post_filters)
        if not keep2:
            self.resolver.rollback(rec)
            self.counters["filtered"] += 1
            self._progress.business_filtered()
            rec["filtered_out_reason"] = freason2
            return
        if not passes_quality(rec):
            self.resolver.rollback(rec)
            self.counters["failed"] += 1
            return
        # G13: a social URL already committed under a DIFFERENT record is
        # cross-business contamination — blank it here (the first claimant
        # keeps the profile).
        if self.cfg.enrichment.social:
            for c in _SOCIAL_COLS:
                u = rec.get(c)
                if u and u != "N/A" and not self._social_registry.claim(
                        u, rec["record_id"]):
                    log.warning(
                        "social %s already owned by another record — "
                        "blanking on %s", u, rec.get("business_name"))
                    rec[c] = "N/A"
        row = {c: rec.get(c, "N/A") for c in OUTPUT_COLUMNS}
        idx = self.csv.append(row)
        self.checkpoint.register_record(
            record_id=rec["record_id"], identity_key=sig.get("identity_key", ""),
            sig=sig, source_query=query, raw=rec)
        self.checkpoint.mark_committed(rec["record_id"], idx)
        self.counters["committed"] += 1
        self._progress.business_saved()

    @staticmethod
    def _split_keyword(query: str) -> str:
        """'dentists in Dallas, TX' -> 'dentists'; 'plumbers near 32,-96' -> 'plumbers'."""
        m = re.split(r"\s+(?:in|near)\s+", query, maxsplit=1, flags=re.I)
        return m[0].strip() if len(m) == 2 else query

    def _normalize_record(self, raw: dict, query: str, keyword: str) -> dict | None:
        loc = (
            re.split(r"\s+(?:in|near)\s+", query, maxsplit=1, flags=re.I)[-1]
            if re.search(r"\s+(?:in|near)\s+", query, re.I)
            else ""
        )
        rec = normalize_listing(
            raw,
            query=query,
            keyword=keyword,
            default_country=self.cfg.job.default_country,
        )
        if rec.get("source_location") in (None, "N/A", ""):
            rec["source_location"] = normalize_text(loc)

        # Polygon filter.
        if self.polygons:
            try:
                lat = float(rec.get("latitude"))
                lng = float(rec.get("longitude"))
            except (TypeError, ValueError):
                lat = lng = None
            if lat is not None and lng is not None and not point_in_any_polygon(
                lat, lng, self.polygons
            ):
                return None
        return rec

    def _enrich(self, rec: dict, website) -> None:
        if website in (None, "N/A", ""):
            rec["website_status"] = "N/A"
            rec["website_failure_reason"] = "N/A"
            return
        enr = self.enricher.enrich(website)
        rec["website_status"] = enr.website_status
        rec["website_failure_reason"] = enr.failure_reason or "N/A"
        # Feed transient failures back to the proxy so a dead endpoint drops
        # out of rotation (F27).
        if (self._proxy_manager is not None
                and enr.failure_reason in ("HTTP_BLOCKED", "TIMEOUT")):
            try:
                self._proxy_manager.report_failure(
                    self._proxy_manager.httpx_proxy())
            except Exception:
                pass
        # Honor the enrichment toggles (F25): a disabled stage leaves its
        # columns at the producer default instead of being overwritten.
        if self.cfg.enrichment.emails:
            rec["emails"] = ",".join(enr.emails) if enr.emails else "N/A"
            rec["email_count"] = len(enr.emails)
        rec["decision_maker_name"] = enr.decision_maker_name or "N/A"
        rec["decision_maker_title"] = enr.decision_maker_title or "N/A"

        # Social links (merge website-discovered with any Maps-discovered).
        if self.cfg.enrichment.social:
            merged_social = detect_social(
                [rec.get(c) for c in _SOCIAL_COLS if rec.get(c) not in (None, "N/A", "")])
            for c in _SOCIAL_COLS:
                if merged_social.get(c) and merged_social[c] != "N/A":
                    rec[c] = merged_social[c]
                elif enr.social.get(c) and enr.social[c] != "N/A":
                    rec[c] = enr.social[c]

        # Tech columns (string values from the tech detector).
        if self.cfg.enrichment.tech_detect:
            for c in _TECH_COLS:
                rec[c] = enr.tech.get(c, "N/A")
            # tag_manager (string) + gtm (YES/NO) both exist; map each correctly.
            rec["tag_manager"] = enr.tech.get("tag_manager", "N/A")
            rec["tech_stack"] = enr.tech.get("tech_stack", "N/A") or "N/A"

        # Boolean tech columns from the signal detector (YES/NO).
        for c in _BOOL_TECH_COLS:
            val = enr.signals.get(c, "NO")
            rec[c] = "yes" if val == "YES" else "NO"

        # Business signal columns (YES/NO).
        for c in _SIGNAL_COLS:
            rec[c] = enr.signals.get(c, "NO")

        # MX / SMTP verification for the first extracted email (native).
        # `mx_enabled` / `smtp_enabled` were removed from the export (schema
        # section 4): `mx_status`=Not Checked already conveys "disabled".
        if enr.emails and (self.cfg.enrichment.mx_verify or self.cfg.enrichment.smtp_verify):
            ver = self.enricher.verify_email(enr.emails[0])
            rec["mx_status"] = ver["mx_status"]
            rec["mx_reason"] = ver["mx_reason"]
            rec["smtp_status"] = ver["smtp_status"]
            rec["smtp_reason"] = ver["smtp_reason"]

    def _analyze(self, rec: dict) -> None:
        reviews = [r for r in (rec.get("_reviews") or []) if r]
        # Honor reviews.min_len / max_len (F25).
        reviews = filter_reviews(
            reviews,
            min_len=self.cfg.reviews.min_len,
            max_len=self.cfg.reviews.max_len,
        )
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

    def _finalize(self) -> None:
        # Write the XLSX + summary while the checkpoint is still open, then
        # tear everything down through the shared idempotent close().
        try:
            write_xlsx(self.out_dir / "leads.xlsx", OUTPUT_COLUMNS,
                       self.checkpoint.iter_committed_rows())
        except Exception as e:  # noqa: BLE001
            log.warning("xlsx write skipped: %s", e)
        write_summary(self.out_dir / "summary.json", self.counters)
        self.close()

    def close(self) -> None:
        """Idempotent teardown: CSV → enricher → checkpoint → collector.

        Called from ``_finalize`` AND from ``main``'s ``finally`` so a run that
        raises still releases every resource (F28).
        """
        if self.csv is not None:
            try:
                self.csv.close()
            except Exception as e:
                log.debug("csv close: %s", e)
        if getattr(self, "enricher", None) is not None:
            try:
                self.enricher.close()
            except Exception as e:
                log.debug("enricher close: %s", e)
        if getattr(self, "checkpoint", None) is not None:
            try:
                self.checkpoint.close()
            except Exception as e:
                log.debug("checkpoint close: %s", e)
        if self.collector is not None:
            try:
                self.collector.close()
            except Exception:
                pass
