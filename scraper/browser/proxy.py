"""Proxy abstraction.

Proxy support is DISABLED by default (the free tier has no proxies), but the
interface is designed so static HTTP(S), a Playwright server dict, a rotating
pool, and a provider can be added later WITHOUT touching the scraper core.

All network usage funnels through :meth:`ProxyManager.resolve`, which is a
no-op when proxies are disabled — so enabling proxies later is a config flip,
never a code change.
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field


@dataclass
class ProxyConfig:
    enabled: bool = False
    http: str | None = None          # e.g. "http://user:pass@host:port"
    https: str | None = None
    pool: list[str] = field(default_factory=list)
    rotation: str = "round_robin"    # "round_robin" | "random"

    def from_dict(self, d: dict | None) -> "ProxyConfig":
        if not d:
            return self
        self.enabled = bool(d.get("enabled", False))
        self.http = d.get("http")
        self.https = d.get("https")
        self.pool = list(d.get("pool", []) or [])
        self.rotation = d.get("rotation", "round_robin")
        return self


_FAILURE_THRESHOLD = 3


class ProxyManager:
    """Central proxy resolver shared by httpx and Playwright clients."""

    def __init__(self, config: ProxyConfig | None = None):
        self.config = config or ProxyConfig()
        self._idx = 0
        self._lock = threading.Lock()
        # Per-proxy consecutive-failure counters for eviction (A3).
        self._failures: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def resolve(self) -> str | None:
        """Return a single proxy URL for the current operation (rotation-aware).

        Healthy proxies are preferred; a member above the failure threshold is
        skipped unless every member is unhealthy (then the full pool is
        available again rather than dead-locking the run).
        """
        if not self.enabled:
            return None
        if self.config.pool:
            pool = self.config.pool
            with self._lock:
                if self.config.rotation == "random":
                    healthy = [p for p in pool
                               if self._failures.get(p, 0) < _FAILURE_THRESHOLD]
                    candidate = healthy or pool
                    return random.choice(candidate)
                # round_robin over healthy members; fall back to full pool.
                healthy = [p for p in pool
                           if self._failures.get(p, 0) < _FAILURE_THRESHOLD]
                candidate = healthy or pool
                proxy = candidate[self._idx % len(candidate)]
                self._idx += 1
                return proxy
        return self.config.https or self.config.http

    def report_failure(self, proxy: str | None) -> None:
        """Record a failed attempt so a dead proxy drops out of rotation."""
        if not proxy:
            return
        with self._lock:
            self._failures[proxy] = self._failures.get(proxy, 0) + 1

    def report_success(self, proxy: str | None) -> None:
        """Reset a proxy's failure counter after a successful use."""
        if not proxy:
            return
        with self._lock:
            self._failures[proxy] = 0

    def httpx_proxy(self) -> str | None:
        """Proxy URL suitable for httpx's ``proxy=`` argument."""
        return self.resolve()

    def playwright_proxy(self) -> dict | None:
        """Proxy settings dict suitable for Playwright launch / new_context."""
        url = self.resolve()
        if not url:
            return None
        return {"server": url}
