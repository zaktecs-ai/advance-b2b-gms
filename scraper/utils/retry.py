"""Retry helpers: random-jitter exponential backoff."""
from __future__ import annotations

import random
import time
from functools import wraps
from typing import Callable, TypeVar

T = TypeVar("T")


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 30.0, jitter: float = 0.3) -> float:
    """Compute a base * 2^attempt delay with uniform random jitter, capped."""
    raw = base * (2 ** max(attempt, 0))
    raw = min(raw, cap)
    return raw * (1.0 + random.uniform(-jitter, jitter))


def retry(
    fn: Callable[..., T],
    retries: int = 3,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.3,
    exceptions: tuple = (Exception,),
) -> T:
    """Call fn with exponential backoff; re-raise after exhausting retries."""
    if retries < 0:
        raise ValueError("retries must be >= 0")
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except exceptions as exc:  # noqa: PERF203
            last = exc
            if attempt >= retries:
                break
            time.sleep(backoff_delay(attempt, base, cap, jitter))
    if last is None:
        raise RuntimeError("retry(): no attempt was made")
    raise last


def retryable(retries: int = 3, base: float = 1.0, cap: float = 30.0, jitter: float = 0.3, exceptions: tuple = (Exception,)):
    """Decorator form of retry()."""
    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            return retry(lambda: fn(*args, **kwargs), retries, base, cap, jitter, exceptions)
        return wrapper
    return deco
