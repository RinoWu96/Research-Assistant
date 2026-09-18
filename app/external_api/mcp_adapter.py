"""Adapter boundary between the `find_evidence` MCP tool and the existing
V0.3.2 Evidence Engine (`app.evidence.report.EvidenceReportService`).

This module performs no retrieval, ranking, evidence classification, citation
tracing, paper identity resolution or reference parsing itself. It only:
  - validates the small public MCP input (query, top_k, evidence_types);
  - calls the existing `EvidenceReportService.build_report`;
  - filters the already-classified, already-ranked report by evidence_types
    (never before retrieval - see `find_evidence` below);
  - truncates to `top_k` after that filtering;
  - maps the resulting `EvidenceReport` onto the public schema in
    `app.external_api.mcp_schemas`, translating internal vocabulary
    ("citation_traced") to the external one ("citation_tracing");
  - converts underlying failures into typed errors the MCP server surfaces as
    structured tool errors instead of an empty evidence list.
"""

from app.embedding.semantic_index import SemanticIndexError
from app.evidence.citation_tracer import CitationTrace
from app.evidence.classifier import EvidenceClass
from app.evidence.models import ClassifiedEvidence, EvidenceReport
from app.evidence.report import EvidenceReportService
from app.external_api.mcp_schemas import (
    CitationTraceInfo,
    EvidenceItem,
    FindEvidenceResult,
    PaperInfo,
    ParsedReference,
    PassageInfo,
    ProvenanceInfo,
    ResolvedPaperRef,
    RetrievalSummary,
)
from app.search.database import DatabaseError

MIN_TOP_K = 1
MAX_TOP_K = 200
VALID_EVIDENCE_CLASSES = frozenset(item.value for item in EvidenceClass)


class EvidenceMCPError(RuntimeError):
    """Base class for structured find_evidence errors surfaced to the calling agent."""


class InvalidQueryError(EvidenceMCPError):
    """The query or top_k input was invalid."""


class InvalidEvidenceTypeError(EvidenceMCPError):
    """An evidence_types entry was not one of the known evidence classes."""


class DatabaseUnavailableError(EvidenceMCPError):
    """The local literature database or semantic index could not be used."""


class EvidenceEngineError(EvidenceMCPError):
    """The Evidence Engine raised an unexpected error while building the report."""


def _normalize_evidence_types(evidence_types: list[str] | None) -> frozenset[str] | None:
    if evidence_types is None:
        return None
    normalized = set()
    for value in evidence_types:
        key = str(value).strip().upper()
        if key not in VALID_EVIDENCE_CLASSES:
            raise InvalidEvidenceTypeError(
                f"Unknown evidence_types value {value!r}; must be one of "
                f"{sorted(VALID_EVIDENCE_CLASSES)}.")
        normalized.add(key)
    return frozenset(normalized)


def _origin(item: ClassifiedEvidence) -> str:
    return "citation_tracing" if item.provenance == "citation_traced" else "original_retrieval"


def _to_citation_trace(trace: CitationTrace) -> CitationTraceInfo:
    resolved = None
    if trace.resolved_identity is not None:
        resolved = ResolvedPaperRef(
            canonical_id=trace.resolved_identity.canonical_id,
            title=trace.resolved_identity.title,
            zotero_item_keys=list(trace.resolved_identity.zotero_item_keys),
        )
    return CitationTraceInfo(
        citation_marker=trace.marker,
        parsed_reference=ParsedReference(
            doi=trace.reference_doi,
            title_guess=trace.reference_title_guess,
            authors_guess=trace.reference_authors_guess,
            year=trace.reference_year,
        ),
        resolution_status=trace.status.value,
        resolved_paper=resolved,
    )


def _to_evidence_item(item: ClassifiedEvidence) -> EvidenceItem:
    identity = item.paper_identity
    return EvidenceItem(
        evidence_id=item.evidence_id,
        evidence_class=item.evidence_class.value,
        classification_confidence=item.evidence_confidence,
        rationale=item.rationale,
        retrieval_score=item.retrieval_score,
        paper=PaperInfo(
            canonical_id=identity.canonical_id,
            title=identity.title,
            authors=list(identity.authors),
            journal=item.journal or None,
            year=identity.year,
            doi=identity.doi or None,
            zotero_item_key=item.zotero_key,
            zotero_item_keys_merged=list(identity.zotero_item_keys),
        ),
        passage=PassageInfo(text=item.original_text, pdf_page=item.physical_pdf_page),
        provenance=ProvenanceInfo(
            attachment_key=item.attachment_key,
            origin=_origin(item),
            parent_evidence_id=item.parent_evidence_id,
        ),
        citation_traces=[_to_citation_trace(trace) for trace in item.citation_traces],
    )


def find_evidence(query: str, top_k: int = 10, evidence_types: list[str] | None = None) -> FindEvidenceResult:
    """Run the existing V0.3.2 Evidence Engine and shape its report for an external agent.

    Retrieval, passage/paper-identity deduplication, evidence classification,
    citation tracing and ranking are all performed by
    `EvidenceReportService.build_report` exactly as in the desktop app and CLI;
    this function only validates input, filters the resulting (already ranked)
    evidence by `evidence_types`, truncates to `top_k`, and converts the result
    to the public MCP schema. Filtering always happens after classification -
    it operates on `report.items`, which `build_report` has already classified
    and ranked - never on raw retrieval candidates.
    """
    if not isinstance(query, str) or not query.strip():
        raise InvalidQueryError("query must be a non-empty string.")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not MIN_TOP_K <= top_k <= MAX_TOP_K:
        raise InvalidQueryError(f"top_k must be an integer between {MIN_TOP_K} and {MAX_TOP_K}.")
    wanted_classes = _normalize_evidence_types(evidence_types)

    try:
        report: EvidenceReport = EvidenceReportService().build_report(query)
    except DatabaseError as exc:
        raise DatabaseUnavailableError(f"Local literature database is unavailable: {exc}") from exc
    except SemanticIndexError as exc:
        raise DatabaseUnavailableError(f"Semantic index is unavailable: {exc}") from exc
    except ValueError as exc:
        raise InvalidQueryError(str(exc)) from exc
    except Exception as exc:
        raise EvidenceEngineError(f"Evidence Engine failed: {exc}") from exc

    classified = list(report.items)
    if wanted_classes is not None:
        classified = [item for item in classified if item.evidence_class.value in wanted_classes]
    limited = classified[:top_k]

    return FindEvidenceResult(
        query=report.query,
        evidence=[_to_evidence_item(item) for item in limited],
        returned_count=len(limited),
        total_classified_count=len(classified),
        retrieval_summary=RetrievalSummary(
            candidate_count=report.candidate_count,
            duplicate_passages_removed=report.duplicate_passages_removed,
            duplicate_papers_merged=report.duplicate_papers_merged,
        ),
    )
