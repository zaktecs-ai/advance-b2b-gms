"""Website enricher: orchestrate fetch -> crawl -> extract + verify.

For a business website, this module:
  1. Fetches the homepage (HTTP-first, Playwright escalation handled upstream).
  2. Discovers + fetches a bounded set of relevant internal pages.
  3. Extracts emails, social links, and technology stack.
  4. Runs the rich signal detector (ga4/gtm/meta_pixel/booking/chat/signals).
  5. Optionally extracts a decision maker from the fetched about/team context.
  6. Optionally performs MX / SMTP verification of extracted emails.

Returns a single ``Enrichment`` dataclass the pipeline maps onto the schema.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from ..email.extract import clean_emails, extract_emails
from ..email.verification import MXChecker, SMTPVerifier
from ..models import FailureReason, resolve_website_status
from ..signals.detector import PageContext, SignalDetector, extract_decision_maker
from ..signals.social import detect_social, social_urls_from_html
from ..utils.normalize import normalize_text, normalize_url
from .crawler import crawl_priority, crawl_sitemap_aware
from .fetcher import Fetcher, FetchResult
from .renderer import PlaywrightRenderer
from .tech_detect import TechDetector

log = logging.getLogger(__name__)


@dataclass
class Enrichment:
    """Canonical website-enrichment result consumed by the pipeline."""

    website_status: str = "N/A"
    failure_reason: str = ""
    emails: list[str] = field(default_factory=list)
    social: dict[str, str] = field(default_factory=dict)
    tech: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, str] = field(default_factory=dict)
    decision_maker_name: str = ""
    decision_maker_title: str = ""


class Enricher:
    """Website enrichment orchestrator (HTTP-first, multi-page, verified)."""

    def __init__(self, timeout: float = 20.0, max_pages: int = 3,
                 proxies: str | None = None, mx_checker: MXChecker | None = None,
                 smtp_verifier: SMTPVerifier | None = None,
                 signal_detector: SignalDetector | None = None,
                 use_wappalyzer: bool = True,
                 decision_makers: bool = False,
                 proxies_cfg=None, proxy_manager=None,
                 exclude_selectors: list | None = None,
                 max_email_length: int = 120,
                 total_request_timeout: float = 0.0,
                 overall_site_timeout_seconds: float = 0.0,
                 connect_timeout: float = 0.0, http_retries: int = 0,
                 enable_sitemap: bool = True,
                 enable_playwright_fallback: bool = True,
                 page_navigation_timeout_seconds: float = 30.0,
                 site_delay: tuple = (0.0, 0.0),
                 gate=None, cooldowns=None,
                 backoff_base: float = 1.0, backoff_cap: float = 20.0,
                 respect_retry_after: bool = True,
                 playwright_pool_size: int = 2,
                 early_stop: bool = True):
        self._fetcher = Fetcher(timeout=timeout, proxy=proxies,
                                total_timeout=total_request_timeout,
                                connect_timeout=connect_timeout,
                                retries=http_retries,
                                backoff_base=backoff_base,
                                backoff_cap=backoff_cap,
                                gate=gate, cooldowns=cooldowns,
                                respect_retry_after=respect_retry_after)
        self._max_pages = max_pages
        # website.overall_site_timeout_seconds: wall-clock budget for the WHOLE
        # site crawl (homepage + priority pages); 0 = unlimited.
        self._overall_timeout = float(overall_site_timeout_seconds or 0.0)
        self._enable_sitemap = bool(enable_sitemap)
        self._site_delay = site_delay
        # Early-stop: once the required enrichment signals (emails + social)
        # are already found, stop fetching further pages from the site.
        self._early_stop = bool(early_stop)
        self._renderer = (PlaywrightRenderer(page_navigation_timeout_seconds,
                                             pool_size=playwright_pool_size)
                          if enable_playwright_fallback else None)
        self._mx = mx_checker
        self._smtp = smtp_verifier
        self._signals = signal_detector or SignalDetector()
        self._tech = TechDetector(use_wappalyzer=use_wappalyzer)
        self._decision_makers = decision_makers
        self._proxy_manager = proxy_manager
        self._exclude_selectors = exclude_selectors
        self._max_email_length = max_email_length

    def enrich(self, website: str) -> Enrichment:
        if not website or website.strip().upper() == "N/A":
            return Enrichment()

        # 1. Fetch homepage. When the site is JS-only (JS_REQUIRED) and
        #    website.enable_playwright_fallback is on, render with Chromium.
        result = self._fetcher.fetch(website)
        if (result.reason == FailureReason.JS_REQUIRED
                and self._renderer is not None):
            rendered = self._renderer.render(result.final_url or website)
            if rendered:
                result = FetchResult(result.url, 200, rendered,
                                     result.reason, result.final_url, {})
        reason = result.reason or ""
        status = resolve_website_status(reason) if reason else "LIVE"

        # 2. Crawl a bounded set of internal pages (aggregating HTML),
        #    respecting the overall site deadline when configured. Emails and
        #    social links are extracted INCREMENTALLY per page so crawling can
        #    early-stop once the required signals are already in hand — no
        #    pointless fetches once the business fields are satisfied.
        deadline = (time.monotonic() + self._overall_timeout
                    if self._overall_timeout > 0 else None)
        htmls: list[str] = []
        emails_raw: list[str] = []
        social_urls: list[str] = []
        if result.html:
            htmls.append(result.html)
            emails_raw.extend(extract_emails(
                result.html, rendered_text="", url=website,
                exclude_selectors=self._exclude_selectors))
            social_urls.extend(social_urls_from_html(result.html, website))
            try:
                extra = crawl_priority(result.html, result.final_url or website,
                                       self._max_pages)
            except Exception:
                extra = []
            # website.enable_sitemap: only spend the /sitemap.xml request when
            # link discovery was WEAK (fewer candidate pages than the cap).
            # A healthy homepage link crawl already fills the page budget.
            if self._enable_sitemap and len(extra) < self._max_pages:
                try:
                    sm = self._fetcher.fetch(urljoin(website, "/sitemap.xml"))
                    if sm.html:
                        sitemap_urls = crawl_sitemap_aware(
                            sm.html, website)[: self._max_pages]
                        extra = list(dict.fromkeys(
                            list(extra) + list(sitemap_urls)))
                except Exception:
                    pass
            lo, hi = self._site_delay
            for page_url in extra[: self._max_pages]:
                if deadline is not None and time.monotonic() > deadline:
                    log.debug("overall site deadline hit for %s", website)
                    break
                if (self._early_stop and emails_raw and social_urls):
                    # Required signals already satisfied from the homepage —
                    # skip the remaining page fetches entirely.
                    break
                try:
                    pr = self._fetcher.fetch(page_url)
                    if pr.html:
                        htmls.append(pr.html)
                        emails_raw.extend(extract_emails(
                            pr.html, rendered_text="", url=website,
                            exclude_selectors=self._exclude_selectors))
                        social_urls.extend(
                            social_urls_from_html(pr.html, website))
                except Exception:
                    continue
                # delays.site_min_seconds/site_max_seconds: polite pacing
                # between same-site page fetches.
                if hi > 0:
                    time.sleep(random.uniform(lo, hi) if hi > lo else hi)

        combined_html = "\n".join(htmls)
        combined_text = normalize_text(self._strip_text(combined_html))
        if combined_text == "N/A":
            combined_text = ""

        # 3. Extract emails + social + tech (homepage/pages already scanned
        #    incrementally above; final cleaning happens here).
        emails = clean_emails(emails_raw, website_url=website)
        social = detect_social(social_urls)
        social = {
            platform: normalize_url(url) if url != "N/A" else "N/A"
            for platform, url in social.items()
        }

        tech_stack, tech_set = self._tech.detect(
            website, combined_html, result.headers or {}
        )
        tech = self._tech.classify(tech_set)
        tech["tech_stack"] = tech_stack or "N/A"
        final_url = result.final_url or website
        if final_url.lower().startswith("https://"):
            tech["ssl"] = "yes"

        # 4. Rich signals.
        scripts = self._extract_scripts(combined_html)
        ctx = PageContext(text=combined_text, html=combined_html, url=website,
                          urls=social_urls, scripts=scripts,
                          technologies=tech_set)
        signals, _ = self._signals.run(ctx)
        if self._decision_makers:
            decision_maker_name, decision_maker_title = extract_decision_maker(
                self._strip_testimonials(combined_html)
            )
        else:
            decision_maker_name, decision_maker_title = "", ""

        return Enrichment(
            website_status=status,
            failure_reason=reason,
            emails=emails,
            social=social,
            tech=tech,
            signals=signals,
            decision_maker_name=decision_maker_name,
            decision_maker_title=decision_maker_title,
        )

    # -- verification (called by pipeline after enrich) --------------------
    def verify_email(self, email: str) -> dict[str, str]:
        """Return {mx_status, mx_reason, smtp_status, smtp_reason} for one email."""
        out = {"mx_status": "Not Checked", "mx_reason": "mx_disabled",
               "smtp_status": "Not Checked", "smtp_reason": "smtp_disabled"}
        mx_result = None
        if self._mx is not None:
            mx_status, mx_reason = self._mx.check(email)
            out["mx_status"] = mx_status
            out["mx_reason"] = mx_reason
            mx_result = (mx_status, mx_reason)
        if self._smtp is not None:
            smtp_status, smtp_reason = self._smtp.verify(email, mx_result)
            out["smtp_status"] = smtp_status
            out["smtp_reason"] = smtp_reason
        return out

    def close(self) -> None:
        self._fetcher.close()
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:
                pass

    @staticmethod
    def _strip_text(html: str) -> str:
        from bs4 import BeautifulSoup
        try:
            return BeautifulSoup(html, "lxml").get_text(" ")
        except Exception:
            return html or ""

    def _strip_testimonials(self, html: str) -> str:
        """Return page text with testimonial/review nodes removed.

        Mirrors ``email/extract.py`` scoping so a testimonial author is never
        mistaken for the business's own decision maker (B3 / F33). `.author` /
        `blockquote` / `.quote` / `cite` are intentionally not stripped from the
        default (they hold real team bios too often).
        """
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return html or ""
        selectors = self._exclude_selectors or [
            ".testimonial", ".testimonials", ".review", ".reviews",
            ".review-body", ".comment", ".comments", ".wp-block-comment",
            "figcaption",
        ]
        for tag in soup.select(",".join(selectors)):
            tag.decompose()
        return soup.get_text(" ")

    @staticmethod
    def _extract_scripts(html: str) -> list[str]:
        return re.findall(
            r"<script\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]",
            html or "",
            flags=re.I,
        )
