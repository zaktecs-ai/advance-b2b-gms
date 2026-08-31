"""HTTP-first website fetcher with a Playwright escalation seam.

Cheap httpx GET by default; escalate to a browser only when the site is
JS-required, blocked, or returns an incomplete page. Produces a rich failure
taxonomy so 'blocked' is never conflated with 'dead'.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..models import FailureReason

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class FetchResult:
    url: str
    status: int | None
    html: str
    reason: str | None
    final_url: str
    headers: dict = None  # populated when response received


def _classify(exc: Exception) -> str:
    name = type(exc).__name__
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout)):
        return FailureReason.TIMEOUT
    if isinstance(exc, httpx.ConnectError):
        msg = str(exc)
        if "Name or service not known" in msg or "getaddrinfo" in msg:
            return FailureReason.DNS_FAILURE
        return FailureReason.CONNECTION_REFUSED
    if isinstance(exc, httpx.SSLError):
        return FailureReason.TLS_ERROR
    if "too many redirects" in name.lower():
        return FailureReason.UNKNOWN
    return FailureReason.UNKNOWN


def _status_reason(status: int) -> str | None:
    if status in (404, 410):
        return FailureReason.NOT_FOUND
    if status in (403, 429, 401, 407):
        return FailureReason.HTTP_BLOCKED
    if status >= 500:
        return FailureReason.UNKNOWN
    return None


class Fetcher:
    def __init__(self, timeout: float = 20.0, follow_redirects: bool = True,
                 proxy: str | None = None):
        self.timeout = timeout
        kw = {}
        if proxy:
            kw["proxy"] = proxy
        self._client = httpx.Client(
            headers=_HEADERS, timeout=timeout,
            follow_redirects=follow_redirects, **kw,
        )

    def fetch(self, url: str) -> FetchResult:
        """Fetch a URL; returns a FetchResult with a classified reason."""
        if not url or url.strip().upper() == "N/A":
            return FetchResult(url or "", None, "", "N/A_reason", url, {})
        try:
            resp = self._client.get(url)
            reason = _status_reason(resp.status_code)
            if resp.status_code == 200 and len(resp.content) < 500:
                reason = FailureReason.JS_REQUIRED
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return FetchResult(url, resp.status_code, resp.text, reason,
                               str(resp.url), headers)
        except httpx.HTTPError as e:
            return FetchResult(url, None, "", _classify(e), url, {})

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
