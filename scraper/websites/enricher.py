"""Website enricher: orchestrate fetch -> crawl -> extract + verify.

For a business website, this module:
  1. Fetches the homepage (HTTP-first, Playwright escalation handled upstream).
  2. Discovers + fetches a bounded set of relevant internal pages.
  3. Extracts emails, social links, and technology stack.
  4. Runs the rich signal detector (ga4/gtm/meta_pixel/booking/chat/signals).
  5. Optionally performs MX / SMTP verification of extracted emails.

Returns a single ``Enrichment`` dataclass the pipeline maps onto the schema.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..email.extract import extract_emails, clean_emails
from ..email.verification import MXChecker, SMTPVerifier
from ..models import resolve_website_status
from ..signals.detector import PageContext, SignalDetector
from ..signals.social import social_urls_from_html, detect_social
from .crawler import crawl_priority
from .fetcher import Fetcher
from .tech_detect import TechDetector

log = logging.getLogger(__name__)


@dataclass
class Enrichment:
    website_status: str
    failure_reason: str
    emails: list = field(default_factory=list)
    social: dict = field(default_factory=dict)
    tech: dict = field(default_factory=dict)
    signals: dict = field(default_factory=dict)


class Enricher:
    """Website enrichment orchestrator (HTTP-first, multi-page, verified)."""

    def __init__(self, timeout: float = 20.0, max_pages: int = 3,
                 proxies: str | None = None, mx_checker: MXChecker | None = None,
                 smtp_verifier: SMTPVerifier | None = None,
                 signal_detector: SignalDetector | None = None,
                 use_wappalyzer: bool = True):
        self._fetcher = Fetcher(timeout=timeout, proxy=proxies)
        self._max_pages = max_pages
        self._mx = mx_checker
        self._smtp = smtp_verifier
        self._signals = signal_detector or SignalDetector()
        self._tech = TechDetector(use_wappalyzer=use_wappalyzer)

    def enrich(self, website: str) -> Enrichment:
        if not website or website.strip().upper() == "N/A":
            return Enrichment("N/A", "", [], {}, {}, {})

        # 1. Fetch homepage.
        result = self._fetcher.fetch(website)
        reason = result.reason or ""
        status = resolve_website_status(reason) if reason else "LIVE"

        # 2. Crawl a bounded set of internal pages (aggregating HTML).
        htmls: list = []
        if result.html:
            htmls.append(result.html)
            try:
                extra = crawl_priority(result.html, result.final_url or website,
                                       self._max_pages)
            except Exception:
                extra = []
            for page_url in extra[: self._max_pages]:
                try:
                    pr = self._fetcher.fetch(page_url)
                    if pr.html:
                        htmls.append(pr.html)
                except Exception:
                    continue

        combined_html = "\n".join(htmls)
        combined_text = self._strip_text(combined_html)

        # 3. Extract emails + social + tech.
        emails_raw: list = []
        for h in htmls:
            emails_raw.extend(extract_emails(h, rendered_text="", url=website))
        emails = clean_emails(emails_raw, website_url=website)

        social_urls: list = []
        for h in htmls:
            social_urls.extend(social_urls_from_html(h, website))
        social = detect_social(social_urls)

        tech_stack, tech_set = self._tech.detect(website, combined_html, {})
        tech = self._tech.classify(tech_set)
        tech["tech_stack"] = tech_stack or "N/A"

        # 4. Rich signals.
        scripts = self._extract_scripts(combined_html)
        ctx = PageContext(text=combined_text, html=combined_html, url=website,
                          urls=social_urls, scripts=scripts,
                          technologies=tech_set)
        signals, _ = self._signals.run(ctx)

        return Enrichment(status, reason, emails, social, tech, signals)

    # -- verification (called by pipeline after enrich) --------------------
    def verify_email(self, email: str) -> dict:
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

    def close(self):
        self._fetcher.close()

    @staticmethod
    def _strip_text(html: str) -> str:
        from bs4 import BeautifulSoup
        try:
            return BeautifulSoup(html, "lxml").get_text(" ")
        except Exception:
            return html or ""

    @staticmethod
    def _extract_scripts(html: str) -> list:
        import re
        return re.findall(r'<script[^>]*src="([^"]+)"', html or "")
