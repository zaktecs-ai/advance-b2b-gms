"""Domain-aware rate limiting for the website enrichment stage.

High AGGREGATE throughput without hammering any individual website:

- ``DomainGate``: a per-domain concurrency slot (default: 1 request at a time
  per domain). The global cap is enforced by the worker pool size, so only the
  per-domain dimension lives here.
- ``DomainCooldowns``: a per-domain "do not touch until T" registry. When a
  site answers ``429``/``503`` with a ``Retry-After`` header (or a transient
  failure occurs) the fetcher parks the domain here; every subsequent request
  to that domain sleeps until the cooldown expires instead of building up a
  retry storm.
- ``parse_retry_after``: converts the ``Retry-After`` header (seconds or an
  HTTP-date) into seconds, clamped to a sane maximum.

Semaphores are created lazily per domain and the registry is pruned (LRU,
bounded) so a long run over tens of thousands of distinct domains cannot leak
memory.
"""
from __future__ import annotations

import email.utils
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlparse

_MAX_RETRY_AFTER_SECONDS = 300.0
_MAX_TRACKED_DOMAINS = 4096


def domain_of(url: str) -> str:
    """Return the rate-limit key for a URL: ``host[:port]`` lowercased.

    The port is kept so a localhost benchmark / staging cluster serving many
    sites on one IP counts as distinct domains per port.
    """
    try:
        parts = urlparse(url or "")
        host = (parts.hostname or "").lower()
        if not host:
            return ""
        port = parts.port
        return f"{host}:{port}" if port else host
    except ValueError:
        return ""


def parse_retry_after(value, now: float | None = None) -> float | None:
    """Return retry delay in seconds from a Retry-After header value.

    Handles the seconds form (``120``) and the HTTP-date form. Returns None
    when absent/unparseable. Result is clamped to ``_MAX_RETRY_AFTER_SECONDS``
    so a hostile or misconfigured server cannot park a domain forever.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        delay = float(raw)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(raw)
            if when is None:
                return None
            if when.tzinfo is None:
                import datetime as _dt
                when = when.replace(tzinfo=_dt.timezone.utc)
            delay = when.timestamp() - (now or time.time())
        except Exception:
            return None
    if delay <= 0:
        return 0.0
    return min(delay, _MAX_RETRY_AFTER_SECONDS)


class DomainGate:
    """Bounded per-domain request slots.

    Usage::

        with gate.slot(url):
            ... one HTTP request to url ...

    Blocks until a slot for the domain is free. With
    ``per_domain_concurrency=1`` (the default) requests to the same domain are
    strictly serialized while different domains proceed in parallel.
    """

    def __init__(self, per_domain: int = 1, max_domains: int = _MAX_TRACKED_DOMAINS):
        self._per_domain = max(1, int(per_domain))
        self._lock = threading.Lock()
        self._slots: dict[str, threading.Semaphore] = {}
        self._order: list[str] = []
        self._max_domains = max_domains

    def _sem(self, domain: str) -> threading.Semaphore:
        with self._lock:
            sem = self._slots.get(domain)
            if sem is None:
                if len(self._slots) >= self._max_domains:
                    # Drop the oldest tracked domain's slot. A live holder keeps
                    # its permit (it holds a reference); we only lose tracking.
                    oldest = self._order.pop(0)
                    self._slots.pop(oldest, None)
                sem = threading.Semaphore(self._per_domain)
                self._slots[domain] = sem
                self._order.append(domain)
            return sem

    @contextmanager
    def slot(self, url: str):
        domain = domain_of(url)
        if not domain:
            yield
            return
        sem = self._sem(domain)
        sem.acquire()
        try:
            yield
        finally:
            sem.release()

    def tracked_domains(self) -> int:
        with self._lock:
            return len(self._slots)


class DomainCooldowns:
    """Per-domain 'sleep until' registry shared across all worker threads."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def set(self, url: str, seconds: float) -> None:
        domain = domain_of(url)
        if not domain or seconds <= 0:
            return
        with self._lock:
            self._until[domain] = max(self._until.get(domain, 0.0),
                                      time.monotonic() + seconds)

    def remaining(self, url: str) -> float:
        domain = domain_of(url)
        if not domain:
            return 0.0
        with self._lock:
            until = self._until.get(domain)
        if until is None:
            return 0.0
        return max(0.0, until - time.monotonic())

    def wait(self, url: str, stop_check=None) -> float:
        """Sleep until the domain's cooldown expires.

        ``stop_check`` (optional callable) is polled every 0.5s; when it turns
        truthy the wait aborts early. Returns the number of seconds slept.
        """
        slept = 0.0
        while True:
            remaining = self.remaining(url)
            if remaining <= 0:
                break
            chunk = min(remaining, 0.5)
            if stop_check is not None and stop_check():
                break
            time.sleep(chunk)
            slept += chunk
        return slept

    def clear(self) -> None:
        with self._lock:
            self._until.clear()
