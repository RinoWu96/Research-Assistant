"""Structured, source-traceable candidate evidence packages."""

from app.evidence.citation_tracer import CitationStatus, CitationTrace, trace_citations
from app.evidence.classifier import EvidenceClass, classify_passage
from app.evidence.evidence_service import EvidenceService, build_evidence_pack
from app.evidence.models import (
    ClassifiedEvidence,
    EvidencePack,
    EvidencePassage,
    EvidenceReport,
    PaperEvidence,
)
from app.evidence.paper_identity import PaperIdentity, resolve_identities
from app.evidence.report import EvidenceReportService, build_evidence_report

__all__ = ["EvidenceService", "EvidencePack", "EvidencePassage", "PaperEvidence",
           "build_evidence_pack", "EvidenceClass", "classify_passage", "CitationStatus",
           "CitationTrace", "trace_citations", "PaperIdentity", "resolve_identities",
           "ClassifiedEvidence", "EvidenceReport", "EvidenceReportService", "build_evidence_report"]
