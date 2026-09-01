"""Retry backoff + the negative-retries guard (D4)."""
import pytest

from scraper.utils.retry import backoff_delay, retry


def test_retry_succeeds_first_try():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert retry(fn, retries=2) == "ok"
    assert calls["n"] == 1


def test_retry_retries_then_raises():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        retry(fn, retries=2, base=0.0)
    assert calls["n"] == 3  # initial + 2 retries


def test_retry_recovers():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("flaky")
        return "finally"

    assert retry(fn, retries=3, base=0.0) == "finally"


def test_retry_negative_retries_raises_clear():
    # D4: a negative retries value must raise a clear error (instead of the
    # silent assert/TypeError that vanished under `python -O`).
    with pytest.raises(ValueError, match="retries must be >= 0"):
        retry(lambda: None, retries=-1)


def test_backoff_delay_bounded():
    for attempt in range(10):
        d = backoff_delay(attempt, base=1.0, cap=30.0)
        assert 0 < d <= 30.0 * 1.3 + 1e-9
