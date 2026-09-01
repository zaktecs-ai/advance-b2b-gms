"""Fetcher failure classification (B7): DNS failure is detected portably."""
import socket

import httpx

from scraper.websites.fetcher import _classify
from scraper.models import FailureReason


def _connect_error_with_cause(cause):
    exc = httpx.ConnectError("connection failed")
    exc.__cause__ = cause
    return exc


def test_dns_failure_via_gaierror_cause():
    cause = socket.gaierror(-2, "Name or service not known")
    assert _classify(_connect_error_with_cause(cause)) == FailureReason.DNS_FAILURE


def test_dns_failure_via_text_fallback_glibc():
    exc = httpx.ConnectError("Name or service not known")
    assert _classify(exc) == FailureReason.DNS_FAILURE


def test_dns_failure_via_text_fallback_macos():
    exc = httpx.ConnectError("nodename nor servname provided")
    assert _classify(exc) == FailureReason.DNS_FAILURE


def test_dns_failure_via_text_fallback_musl():
    exc = httpx.ConnectError("Name does not resolve")
    assert _classify(exc) == FailureReason.DNS_FAILURE


def test_connection_refused_is_not_dns():
    exc = httpx.ConnectError("Connection refused")
    assert _classify(exc) == FailureReason.CONNECTION_REFUSED


def test_timeout_classified():
    assert _classify(httpx.ConnectTimeout("t")) == FailureReason.TIMEOUT
    assert _classify(httpx.ReadTimeout("t")) == FailureReason.TIMEOUT


def test_tls_error_classified_via_ssl_cause():
    import ssl
    cert_err = ssl.SSLCertVerificationError("certificate verify failed")
    exc = httpx.ConnectError("ssl error")
    exc.__cause__ = cert_err
    assert _classify(exc) == FailureReason.TLS_ERROR


def test_no_attribute_error_on_ssl_path():
    # Regression: httpx (>=0.28) has no httpx.SSLError; a bare SSL-flavoured
    # ConnectError must classify cleanly (CONNECTION_REFUSED/UNKNOWN) and never
    # raise AttributeError.
    import ssl
    # Non-SSL, non-DNS ConnectError -> CONNECTION_REFUSED
    assert _classify(httpx.ConnectError("handshake reset by peer")) == FailureReason.CONNECTION_REFUSED


def test_fetch_result_default_headers():
    # F11: mutable-dataclass-default removed; each instance gets its own dict.
    from scraper.websites.fetcher import FetchResult
    assert FetchResult("u", 200, "", None, "u").headers == {}


def test_connect_timeout_classifies_as_timeout():
    # F12: ConnectTimeout is caught by the TIMEOUT branch (not the deleted
    # unreachable branch).
    import httpx
    from scraper.websites.fetcher import _classify
    from scraper.models import FailureReason
    assert _classify(httpx.ConnectTimeout("x")) == FailureReason.TIMEOUT
