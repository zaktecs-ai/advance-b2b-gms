"""Optional MX and SMTP verification (both off by default).

MX: cached DNS lookup (dnspython); no network beyond DNS.
SMTP: direct socket verification with explicit, never-guaranteed statuses,
MX-preference-sorted host failover, and bounded concurrency.
"""
from __future__ import annotations

import logging
import smtplib
import time

from ..utils.dns_cache import DNSCache
from ..utils.normalize import extract_domain

try:
    import dns.resolver
    _HAS_DNS = True
except ImportError:  # pragma: no cover
    _HAS_DNS = False

log = logging.getLogger(__name__)

_MX_CACHE = DNSCache(max_size=50_000, ttl=3600)


class MXChecker:
    def __init__(self, enabled: bool = False, timeout: float = 5.0):
        self.enabled = enabled
        self.timeout = timeout

    def check(self, email: str) -> tuple[str, str]:
        """Return (status, reason). status in {Verified, Invalid, Inconclusive,
        Not Checked, Connection Failed}."""
        if not self.enabled:
            return "Not Checked", "mx_disabled"
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if not domain:
            return "Invalid", "no_domain"
        cached = _MX_CACHE.get(domain)
        if cached is not None:
            return cached
        result = self._lookup(domain)
        _MX_CACHE.set(domain, result)
        return result

    def _lookup(self, domain: str) -> tuple[str, str]:
        if not _HAS_DNS:
            return "Inconclusive", "no_dns_library"
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=self.timeout)
            if answers:
                return "Verified", f"mx_records={len(answers)}"
            return "Invalid", "no_mx_records"
        except dns.resolver.NoAnswer:
            return "Invalid", "no_mx_records"
        except dns.resolver.NXDOMAIN:
            return "Invalid", "nxdomain"
        except Exception as e:  # noqa: BLE001
            return "Inconclusive", f"dns_error:{type(e).__name__}"


class SMTPVerifier:
    """Direct SMTP verification (RCPT TO probe), explicit statuses."""

    def __init__(self, enabled: bool = False, timeout: float = 15.0,
                 from_email: str = "verify@example.com", retries: int = 1):
        self.enabled = enabled
        self.timeout = timeout
        self.from_email = from_email
        self.retries = retries

    def verify(self, email: str) -> tuple[str, str]:
        if not self.enabled:
            return "Not Checked", "smtp_disabled"
        if "@" not in email:
            return "Invalid", "no_at"
        domain = email.rsplit("@", 1)[-1]
        mx_hosts = self._mx_hosts(domain)
        if not mx_hosts:
            return "Inconclusive", "no_mx_host"
        last_reason = "no_attempt"
        for host in mx_hosts:
            for _ in range(self.retries + 1):
                try:
                    with smtplib.SMTP(host, 25, timeout=self.timeout) as smtp:
                        code, _ = smtp.ehlo()
                        code2, _ = smtp.mail(self.from_email)
                        code3, resp = smtp.rcpt(email)
                        if code3 == 250:
                            return "Verified", f"rcpt_accept:{host}"
                        if code3 in (550, 551, 553):
                            return "Invalid", f"rcpt_reject:{host}"
                        # Catch-all / inconclusive.
                        return "Catch-All", f"soft_accept:{host}"
                except (smtplib.SMTPConnectError, ConnectionRefusedError, TimeoutError) as e:
                    last_reason = f"connect_fail:{type(e).__name__}"
                    break
                except smtplib.SMTPException as e:
                    last_reason = f"smtp_error:{type(e).__name__}"
                time.sleep(0.3)
        return "Connection Failed", last_reason

    def _mx_hosts(self, domain: str) -> list[str]:
        if not _HAS_DNS:
            return []
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=self.timeout)
            # Sort by preference (lowest first).
            ordered = sorted(answers, key=lambda r: r.preference)
            return [str(r.exchange).rstrip(".") for r in ordered]
        except Exception:  # noqa: BLE001
            return []
