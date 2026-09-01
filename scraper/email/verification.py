"""Optional MX and SMTP verification.

MX:           DNS lookup cached per-domain (dnspython). No SMTP dependency.
SMTP:         Direct socket verification (EHLO -> MAIL FROM -> RCPT TO),
              explicit statuses, never converts uncertainty into certainty.
              A control RCPT to a guaranteed-nonexistent address distinguishes
              a genuinely deliverable mailbox from a catch-all domain.

Both are OFF by default and the engine runs normally without them.
"""
from __future__ import annotations

import logging
import smtplib
import threading
import uuid

from ..utils.dns_cache import DNSCache

try:
    import dns.resolver
    _HAS_DNS = True
except ImportError:  # pragma: no cover
    _HAS_DNS = False

log = logging.getLogger(__name__)

_MX_CACHE = DNSCache(max_size=50_000, ttl=3600)


class MXChecker:
    """Cached MX lookup using dnspython (no safe fallback without it)."""

    def __init__(self, enabled: bool = False, timeout: float = 5.0):
        self.enabled = enabled
        self.timeout = timeout

    def check(self, email: str) -> tuple[str, str]:
        """Return (status, reason). status in {PASS, FAIL, INCONCLUSIVE,
        NOT_CHECKED}."""
        if not self.enabled:
            return "NOT_CHECKED", "mx_disabled"
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if not domain:
            return "FAIL", "no_domain"
        cached = _MX_CACHE.get(domain)
        if cached is not None:
            return cached
        result = self._lookup(domain)
        _MX_CACHE.set(domain, result)
        return result

    def _lookup(self, domain: str) -> tuple[str, str]:
        if _HAS_DNS:
            try:
                answers = dns.resolver.resolve(domain, "MX", lifetime=self.timeout)
                if answers:
                    return "PASS", f"mx_records={len(answers)}"
                return "FAIL", "no_mx_records"
            except dns.resolver.NoAnswer:
                return "FAIL", "no_mx_records"
            except dns.resolver.NXDOMAIN:
                return "FAIL", "nxdomain"
            except Exception as e:
                return "INCONCLUSIVE", f"dns_error:{type(e).__name__}"
        return "INCONCLUSIVE", "no_dns_library"


class SMTPVerifier:
    """Direct SMTP verification with explicit, non-guaranteed statuses."""

    def __init__(self, enabled: bool = False, timeout: float = 15.0,
                 from_email: str = "verify@example.com", retries: int = 1,
                 max_workers: int = 3):
        self.enabled = enabled
        self.timeout = timeout
        self.from_email = from_email
        self.retries = retries
        self._mx_cache: dict = {}
        self._mx_cache_lock = threading.Lock()
        self.max_workers = max(1, max_workers)
        # Cap simultaneous outbound port-25 connections so verifying many
        # domains cannot open a flood of sockets and get the host IP
        # blacklisted. Sized from config.smtp.workers (B5 / D1).
        self._gate = threading.Semaphore(self.max_workers)

    def verify(self, email: str, mx: tuple | None = None) -> tuple[str, str]:
        """Return (status, reason). See models.SMTP_STATUSES for the status set."""
        if not self.enabled:
            return "Not Checked", "smtp_disabled"
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if not domain:
            return "Invalid", "no_domain"

        mx_status, mx_reason = self._mx_status(domain, mx)
        if mx_status != "PASS":
            return "Invalid", f"mx_{mx_reason}"

        hosts = self._mx_hosts(domain)
        if not hosts:
            return "Invalid", "no_mx_hosts"

        last_status, last_reason = "Inconclusive", "unreachable"
        for attempt in range(self.retries + 1):
            status, reason = self._try_hosts(hosts, email)
            if status in ("Verified", "Invalid"):
                return status, reason
            last_status, last_reason = status, reason
            if attempt < self.retries:
                log.debug("smtp %s inconclusive (%s), retry %d/%d",
                          email, status, attempt + 1, self.retries)
        return last_status, last_reason

    def _try_hosts(self, hosts: list, email: str) -> tuple[str, str]:
        last_status, last_reason = "Inconclusive", "unreachable"
        for host in hosts:
            try:
                status, reason = self._smtp_transaction(host, email)
                if status in ("Verified", "Invalid"):
                    return status, reason
                last_status, last_reason = status, reason
            except Exception as e:
                last_status, last_reason = "Connection Failed", type(e).__name__
        return last_status, last_reason

    def _mx_status(self, domain: str, mx: tuple | None) -> tuple[str, str]:
        if mx is not None:
            return mx
        with self._mx_cache_lock:
            if domain in self._mx_cache:
                return self._mx_cache[domain]
        checker = MXChecker(enabled=True, timeout=5.0)
        result = checker.check("x@" + domain)
        with self._mx_cache_lock:
            self._mx_cache[domain] = result
        return result

    def _mx_hosts(self, domain: str) -> list:
        if _HAS_DNS:
            try:
                answers = dns.resolver.resolve(domain, "MX", lifetime=5.0)
                return [str(r.exchange).rstrip(".") for r in
                        sorted(answers, key=lambda r: (r.preference, str(r.exchange)))]
            except Exception:
                return []
        return []

    def _smtp_transaction(self, host: str, email: str) -> tuple[str, str]:
        """Run one SMTP transaction, distinguishing catch-all from deliverable.

        For a 250 (accepted) response we send a second RCPT to a guaranteed-
        nonexistent random address at the same domain. If that is also accepted
        the domain is a catch-all and the result is downgraded to Catch-All
        rather than falsely reported Verified.
        """
        with self._gate:
            with smtplib.SMTP(timeout=self.timeout) as smtp:
                code, _ = smtp.connect(host, 25)
                if code != 220:
                    return "Connection Failed", f"greeting_{code}"
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls()
                    smtp.ehlo()
                smtp.mail(self.from_email)
                code, _resp = smtp.rcpt(email)
                if code == 250:
                    return self._catch_all_probe(smtp, email)
                if code == 550:
                    return "Invalid", "rcpt_550"
                if code == 452:
                    return "Inconclusive", "greylisted_452"
                return "Inconclusive", f"rcpt_{code}"

    @staticmethod
    def _catch_all_probe(smtp, email: str) -> tuple[str, str]:
        """Probe a nonexistent address to detect a catch-all domain."""
        domain = email.rsplit("@", 1)[-1]
        probe = f"nonexistent-{uuid.uuid4().hex[:12]}@{domain}"
        try:
            pcode, _ = smtp.rcpt(probe)
        except Exception:  # some servers RST after the probe address
            pcode = None
        if pcode == 250:
            return "Catch-All", "catch_all_accepts_any"
        if pcode == 550:
            return "Verified", "rcpt_250"
        return "Inconclusive", "catchall_probe_inconclusive"
