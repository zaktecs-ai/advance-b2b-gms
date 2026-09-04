"""Domain-aware rate limiting + fetcher retry policy (unit tests).

Covers: per-domain serialization, cooldown parking, Retry-After parsing,
transient-failure retry with backoff, and permanent-failure short-circuits.
"""
from __future__ import annotations

import threading
import time

import httpx
import pytest

from scraper.websites.fetcher import Fetcher, FetchResult
from scraper.websites.rate_limiter import (DomainCooldowns, DomainGate,
                                           domain_of, parse_retry_after)


# --- domain_of / parse_retry_after -------------------------------------------

def test_domain_of_keeps_port_and_lowercases():
    assert domain_of("https://Example.COM/page") == "example.com"
    assert domain_of("http://127.0.0.1:8901/x") == "127.0.0.1:8901"
    assert domain_of("not a url") == ""
    assert domain_of("") == ""


def test_parse_retry_after_seconds_and_date():
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("junk") is None
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("999999") == 300.0  # clamped
    from email.utils import format_datetime
    import datetime as dt
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)
    got = parse_retry_after(format_datetime(future))
    assert got is not None and 25 <= got <= 30.5


# --- DomainGate: per-domain serialization ------------------------------------

def test_domain_gate_serializes_same_domain_in_parallel_threads():
    gate = DomainGate(per_domain=1)
    state = {"active": 0, "max": 0}
    lock = threading.Lock()

    def hammer(url: str):
        for _ in range(5):
            with gate.slot(url):
                with lock:
                    state["active"] += 1
                    state["max"] = max(state["max"], state["active"])
                time.sleep(0.005)
                with lock:
                    state["active"] -= 1

    threads = [threading.Thread(target=hammer, args=("http://same.test/x",))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 1  # strictly serialized per domain


def test_domain_gate_allows_different_domains_concurrently():
    gate = DomainGate(per_domain=1)

    def hold(url):
        with gate.slot(url):
            barrier.wait()  # all three must hold their slots simultaneously

    barrier = threading.Barrier(3, timeout=5)
    threads = [threading.Thread(target=hold, args=(f"http://d{i}.test/",))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert all(not t.is_alive() for t in threads)


# --- DomainCooldowns ---------------------------------------------------------

def test_domain_cooldown_set_remaining_wait():
    cd = DomainCooldowns()
    assert cd.remaining("http://a.test/") == 0.0
    cd.set("http://a.test/", 0.2)
    assert 0.0 < cd.remaining("http://a.test/") <= 0.2
    t0 = time.monotonic()
    cd.wait("http://a.test/")
    assert 0.15 <= time.monotonic() - t0 < 2.0
    assert cd.remaining("http://a.test/") == 0.0
    assert cd.remaining("http://b.test/") == 0.0  # other domains unaffected


def test_domain_cooldown_wait_aborts_on_stop_check():
    cd = DomainCooldowns()
    cd.set("http://a.test/", 60.0)
    t0 = time.monotonic()
    cd.wait("http://a.test/", stop_check=lambda: True)
    assert time.monotonic() - t0 < 2.0  # did not sleep the full minute


# --- Fetcher retry policy ----------------------------------------------------

class _Route(httpx.BaseTransport):
    """Scripted status-code responses; records every attempt."""

    def __init__(self, script: list[int]):
        self.script = list(script)
        self.requests: list[str] = []
        self.retry_after = 0

    def handle_request(self, request):
        self.requests.append(str(request.url))
        status = self.script.pop(0) if self.script else 200
        body = b"x" * 600 if status == 200 else b"busy"
        headers = {}
        if status == 429 and self.retry_after:
            headers["Retry-After"] = str(self.retry_after)
        return httpx.Response(status, content=body, headers=headers)


def test_fetcher_no_retry_on_permanent_404():
    route = _Route([404])
    f = Fetcher(retries=3, backoff_base=0.0, transport=route)
    r = f.fetch("http://dead.test/")
    assert r.status == 404
    assert route.requests == ["http://dead.test/"]  # exactly one attempt
    f.close()


def test_fetcher_retries_429_with_retry_after():
    route = _Route([429, 429, 200])
    f = Fetcher(retries=3, backoff_base=0.0, transport=route)
    r = f.fetch("http://busy.test/")
    assert r.status == 200
    assert len(route.requests) == 3
    f.close()


def test_fetcher_exhausts_429_returns_last_status():
    route = _Route([429, 429, 429, 429])
    f = Fetcher(retries=2, backoff_base=0.0, transport=route)
    r = f.fetch("http://always-busy.test/")
    assert r.status == 429
    assert len(route.requests) == 3  # 1 initial + 2 retries
    f.close()


def test_fetcher_503_is_transient_but_502_is_not():
    route = _Route([503, 200])
    f = Fetcher(retries=1, backoff_base=0.0, transport=route)
    r = f.fetch("http://flaky.test/")
    assert r.status == 200 and len(route.requests) == 2
    f.close()

    route = _Route([502, 200])
    f = Fetcher(retries=1, backoff_base=0.0, transport=route)
    r = f.fetch("http://gone.test/")
    assert r.status == 502 and len(route.requests) == 1  # no retry storm
    f.close()


def test_fetcher_dns_failure_not_retried():
    def handler(request):
        raise httpx.ConnectError("name or service not known")

    f = Fetcher(retries=3, backoff_base=0.0,
                transport=httpx.MockTransport(handler))
    r = f.fetch("http://no-such-host.test/")
    assert r.status is None and r.reason == "DNS_FAILURE"


def test_fetcher_parks_domain_in_cooldown_on_retry_after():
    """A 429 + Retry-After parks the domain and returns immediately — the
    worker is never blocked for the full window; the NEXT request to the
    domain bears the cooldown wait."""
    cooldowns = DomainCooldowns()
    route = _Route([429, 200])
    route.retry_after = 120
    f = Fetcher(retries=1, backoff_base=0.0, cooldowns=cooldowns,
                transport=route)
    t0 = time.monotonic()
    r = f.fetch("http://parked.test/")
    assert r.status == 429
    assert time.monotonic() - t0 < 2.0      # no inline 120s sleep
    assert len(route.requests) == 1         # no immediate retry either
    assert cooldowns.remaining("http://parked.test/") > 0
    f.close()


def test_fetcher_next_request_after_park_waits_out_cooldown():
    cooldowns = DomainCooldowns()
    route = _Route([429, 200])
    route.retry_after = 0.3
    f = Fetcher(retries=1, backoff_base=0.0, cooldowns=cooldowns,
                transport=route)
    r1 = f.fetch("http://slow-retry.test/")
    assert r1.status == 429
    t0 = time.monotonic()
    r2 = f.fetch("http://slow-retry.test/")  # waits out the 0.3s cooldown
    assert r2.status == 200
    assert time.monotonic() - t0 >= 0.25
    assert len(route.requests) == 2
    f.close()
