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
import re
from dataclasses import dataclass, field
from typing import Any

from ..email.extract import clean_emails, extract_emails
from ..email.verification import MXChecker, SMTPVerifier
from ..models import resolve_website_status
from ..signals.detector import PageContext, SignalDetector, extract_decision_maker
from ..signals.social import detect_social, social_urls_from_html
from ..utils.normalize import normalize_text, normalize_url
from .crawler import crawl_priority
from .fetcher import Fetcher
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
                 decision_makers: bool = False):
        self._fetcher = Fetcher(timeout=timeout, proxy=proxies)
        self._max_pages = max_pages
        self._mx = mx_checker
        self._smtp = smtp_verifier
        self._signals = signal_detector or SignalDetector()
        self._tech = TechDetector(use_wappalyzer=use_wappalyzer)
        self._decision_makers = decision_makers

    def enrich(self, website: str) -> Enrichment:
        if not website or website.strip().upper() == "N/A":
            return Enrichment()

        # 1. Fetch homepage.
        result = self._fetcher.fetch(website)
        reason = result.reason or ""
        status = resolve_website_status(reason) if reason else "LIVE"

        # 2. Crawl a bounded set of internal pages (aggregating HTML).
        htmls: list[str] = []
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
        combined_text = normalize_text(self._strip_text(combined_html))
        if combined_text == "N/A":
            combined_text = ""

        # 3. Extract emails + social + tech.
        emails_raw: list[str] = []
        for h in htmls:
            emails_raw.extend(extract_emails(h, rendered_text="", url=website))
        emails = clean_emails(emails_raw, website_url=website)

        social_urls: list[str] = []
        for h in htmls:
            social_urls.extend(social_urls_from_html(h, website))
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
            decision_maker_name, decision_maker_title = extract_decision_maker(combined_text)
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

    @staticmethod
    def _strip_text(html: str) -> str:
        from bs4 import BeautifulSoup
        try:
            return BeautifulSoup(html, "lxml").get_text(" ")
        except Exception:
            return html or ""

    @staticmethod
    def _extract_scripts(html: str) -> list[str]:
        return re.findall(
            r"<script\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]",
            html or "",
            flags=re.I,
        )
