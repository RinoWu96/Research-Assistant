"""Bounded, process-local storage for V0.7 evidence handles.

The persistent Core owns this cache.  MCP and GUI processes only receive opaque
report identifiers, so restarting either client cannot invalidate a report.
"""

from collections import OrderedDict
from dataclasses import dataclass
import secrets
import threading
import time

from app.evidence.models import EvidenceReport


class ReportNotFoundError(LookupError):
    pass


class ReportExpiredError(LookupError):
    pass


class EvidenceNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CachedReport:
    report: EvidenceReport
    created_at: float
    expires_at: float


class ReportCache:
    """Thread-safe TTL/LRU cache with bounded expiration tombstones."""

    def __init__(self, *, ttl_seconds: float = 3600.0, max_reports: int = 100,
                 clock=time.monotonic) -> None:
        if ttl_seconds <= 0 or max_reports <= 0:
            raise ValueError("ttl_seconds and max_reports must be positive.")
        self.ttl_seconds = float(ttl_seconds)
        self.max_reports = int(max_reports)
        self._clock = clock
        self._lock = threading.RLock()
        self._reports: OrderedDict[str, CachedReport] = OrderedDict()
        self._expired: OrderedDict[str, None] = OrderedDict()

    def _remember_expired(self, report_id: str) -> None:
        self._expired[report_id] = None
        self._expired.move_to_end(report_id)
        while len(self._expired) > self.max_reports:
            self._expired.popitem(last=False)

    def _purge(self, now: float) -> None:
        for report_id, cached in list(self._reports.items()):
            if cached.expires_at <= now:
                del self._reports[report_id]
                self._remember_expired(report_id)

    def store(self, report: EvidenceReport) -> str:
        now = self._clock()
        with self._lock:
            self._purge(now)
            report_id = "evr_" + secrets.token_urlsafe(18)
            self._reports[report_id] = CachedReport(report, now, now + self.ttl_seconds)
            while len(self._reports) > self.max_reports:
                evicted, _ = self._reports.popitem(last=False)
                self._remember_expired(evicted)
            return report_id

    def get(self, report_id: str) -> EvidenceReport:
        now = self._clock()
        with self._lock:
            self._purge(now)
            cached = self._reports.get(report_id)
            if cached is None:
                if report_id in self._expired:
                    raise ReportExpiredError(f"Report {report_id!r} has expired; run find_evidence again.")
                raise ReportNotFoundError(f"Report {report_id!r} was not found in this Core process.")
            self._reports.move_to_end(report_id)
            return cached.report

    def evidence(self, report_id: str, evidence_id: str):
        report = self.get(report_id)
        for item in report.items:
            if item.evidence_id == evidence_id:
                return item
        raise EvidenceNotFoundError(
            f"Evidence {evidence_id!r} was not found in report {report_id!r}.")
