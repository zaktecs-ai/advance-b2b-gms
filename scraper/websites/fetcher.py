"""HTTP-first website fetcher with a Playwright escalation seam.

Cheap httpx GET by default; escalate to a browser only when the site is
JS-required, blocked, or returns an incomplete page. Produces a rich failure
taxonomy so 'blocked' is never conflated with 'dead'.

Reliability + politeness:
- one shared ``httpx.Client`` (connection pool reuse across all workers);
- per-domain slot via ``DomainGate`` (default 1 concurrent request/domain);
- transient failures (429/503, timeouts, connection errors) retry with
  exponential backoff + jitter, honoring ``Retry-After`` when present;
- backoff sleeps happen OUTSIDE the per-domain slot so a cooling-down site
  never blocks unrelated domains, and retry storms cannot accumulate.
"""
from __future__ import annotations

import logging
import socket
import ssl
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx

from ..models import FailureReason
from ..utils.retry import backoff_delay
from .rate_limiter import DomainCooldowns, DomainGate, parse_retry_after

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
    headers: dict = field(default_factory=dict)  # populated when response received


def _classify(exc: Exception) -> str:
    name = type(exc).__name__
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout)):
        return FailureReason.TIMEOUT
    if isinstance(exc, httpx.ConnectError):
        # Walk the cause chain first: a socket.gaierror means DNS failure, a
        # ssl.SSLError means TLS failure — both portable across platforms,
        # unlike matching OS-specific error text.
        cause = exc
        while cause is not None:
            if isinstance(cause, socket.gaierror):
                return FailureReason.DNS_FAILURE
            if isinstance(cause, ssl.SSLError):
                return FailureReason.TLS_ERROR
            cause = cause.__cause__ or cause.__context__
        # Note: `httpx.ConnectTimeout` is already caught above by the first
        # branch, so the duplicate check that used to live here was unreachable
        # (F12). Keep the cause-chain walk as the authority.
        msg = str(exc)
        if _DNS_TEXT_PATTERNS_ANY(msg):
            return FailureReason.DNS_FAILURE
        return FailureReason.CONNECTION_REFUSED
    if "too many redirects" in name.lower():
        return FailureReason.UNKNOWN
    return FailureReason.UNKNOWN


# A widened string fallback for cases where no socket.gaierror is preserved in
# the cause chain. Covers the platform-specific phrasings.
_DNS_TEXT_MARKERS = (
    "name or service not known", "getaddrinfo", "nodename nor servname",
    "name does not resolve", "temporary failure in name resolution",
    "no address associated with hostname",
)


def _DNS_TEXT_PATTERNS_ANY(msg: str) -> bool:
    low = (msg or "").lower()
    return any(m in low for m in _DNS_TEXT_MARKERS)


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
                 proxy: str | None = None, total_timeout: float = 0.0,
                 connect_timeout: float = 0.0, retries: int = 0,
                 max_connections: int = 64,
                 backoff_base: float = 1.0, backoff_cap: float = 20.0,
                 gate: DomainGate | None = None,
                 cooldowns: DomainCooldowns | None = None,
                 respect_retry_after: bool = True,
                 transport: httpx.BaseTransport | None = None):
        self.timeout = timeout
        self.retries = max(0, int(retries))
        # runtime.request_timeout caps each HTTP PHASE (httpx has no whole-
        # request 'total'); website.http_connect_timeout_seconds separately
        # caps the connect phase. 0 values defer to `timeout`.
        connect = connect_timeout if connect_timeout > 0 else timeout
        read = min(timeout, total_timeout) if total_timeout > 0 else timeout
        cap = min(connect, read)
        kw_timeout = httpx.Timeout(read, connect=cap, read=read,
                                   write=read, pool=cap)
        kw = {}
        if proxy:
            kw["proxy"] = proxy
        self._client = httpx.Client(
            headers=_HEADERS, timeout=kw_timeout,
            follow_redirects=follow_redirects,
            limits=httpx.Limits(max_connections=max(4, max_connections),
                                max_keepalive_connections=max(4, max_connections)),
            **kw,
        )
        if transport is not None:
            self._client = httpx.Client(
                headers=_HEADERS, timeout=kw_timeout,
                follow_redirects=follow_redirects, transport=transport, **kw)
        self._backoff_base = max(0.0, float(backoff_base))
        self._backoff_cap = max(1.0, float(backoff_cap))
        self._gate = gate
        self._cooldowns = cooldowns
        self._respect_retry_after = bool(respect_retry_after)

    def fetch(self, url: str) -> FetchResult:
        """Fetch a URL; returns a FetchResult with a classified reason.

        website.http_retries controls retry attempts for TRANSIENT failures:
        429/503 statuses, timeouts, and connection errors. Retries use
        exponential backoff with jitter (avoiding retry storms); a 429/503
        with a Retry-After header parks the domain and wins over the computed
        backoff. DNS failures / TLS errors / 4xx are permanent (no retry).
        """
        if not url or url.strip().upper() == "N/A":
            return FetchResult(url or "", None, "", "N/A_reason", url, {})
        attempts = self.retries + 1
        last: FetchResult | None = None
        for attempt in range(attempts):
            # Honor a parked domain cooldown BEFORE taking a per-domain slot.
            if self._cooldowns is not None:
                self._cooldowns.wait(url)
            gate_ctx = (self._gate.slot(url) if self._gate is not None
                        else _null_slot())
            with gate_ctx:
                result, transient, retry_after = self._attempt(url)
            last = result
            if not transient or attempt + 1 >= attempts:
                return result
            if retry_after is not None:
                # Respect Retry-After WITHOUT blocking this worker for minutes:
                # park the domain so FUTURE requests wait, and report this
                # attempt's result now. (Without a shared cooldowns registry we
                # fall back to an inline capped sleep + retry.)
                if self._respect_retry_after and self._cooldowns is not None:
                    self._cooldowns.set(url, retry_after)
                    return result
                if self._respect_retry_after:
                    time.sleep(min(retry_after, self._backoff_cap))
                continue
            time.sleep(backoff_delay(attempt, self._backoff_base,
                                     self._backoff_cap))
        assert last is not None
        return last

    def _attempt(self, url: str) -> tuple[FetchResult, bool, float | None]:
        """One HTTP attempt inside the per-domain slot.

        Returns ``(result, is_transient, retry_after_seconds|None)``.
        """
        try:
            resp = self._client.get(url)
            reason = _status_reason(resp.status_code)
            if resp.status_code in (429, 503):
                ra = parse_retry_after(resp.headers.get("retry-after"))
                return (FetchResult(url, resp.status_code, resp.text, reason,
                                    str(resp.url), {}),
                        True, ra)
            if resp.status_code == 200 and len(resp.content) < 500:
                reason = FailureReason.JS_REQUIRED
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return (FetchResult(url, resp.status_code, resp.text, reason,
                                str(resp.url), headers), False, None)
        except httpx.HTTPError as e:
            reason = _classify(e)
            # DNS / TLS failures are permanent; everything else is transient.
            transient = reason not in (FailureReason.DNS_FAILURE,
                                       FailureReason.TLS_ERROR)
            return (FetchResult(url, None, "", reason, url, {}),
                    transient, None)

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


@contextmanager
def _null_slot():
    yield
