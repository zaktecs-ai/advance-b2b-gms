"""Website enricher: orchestrate fetch -> extract (emails/social/tech)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..email.extract import extract_emails, clean_emails
from ..models import FailureReason, resolve_website_status
from .fetcher import Fetcher
from .tech_detect import detect_tech

log = logging.getLogger(__name__)


@dataclass
class Enrichment:
    website_status: str
    failure_reason: str
    emails: list[str]
    tech: dict


class Enricher:
    def __init__(self, timeout: float = 20.0):
        self._fetcher = Fetcher(timeout=timeout)

    def enrich(self, website: str, rendered_text: str = "") -> Enrichment:
        """Fetch + extract emails/tech for a website URL."""
        if not website or website.strip().upper() == "N/A":
            return Enrichment("N/A", "", [], {})

        result = self._fetcher.fetch(website)
        reason = result.reason or ""
        status = resolve_website_status(reason) if reason else "LIVE"

        emails: list[str] = []
        tech: dict = {}
        if result.html:
            raw = extract_emails(result.html, rendered_text, website)
            emails = clean_emails(raw, website_url=website)
            tech = detect_tech(result.html)

        return Enrichment(status, reason, emails, tech)

    def close(self):
        self._fetcher.close()
