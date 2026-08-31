"""Thread-safe, size-capped, TTL DNS cache."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict


class DNSCache:
    """A bounded LRU cache with per-entry TTL used to avoid re-resolving the
    same domain's MX/records repeatedly across a large record set."""

    def __init__(self, max_size: int = 50_000, ttl: float = 3600.0):
        self.max_size = max_size
        self.ttl = ttl
        self._data: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires, value = entry
            if expires < time.monotonic():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
