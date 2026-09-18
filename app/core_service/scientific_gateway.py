"""V0.7 scientific inspection operations built on cached Evidence Reports."""

from contextlib import closing
from pathlib import Path
import sqlite3

from app.evidence.classifier import EvidenceClass, classify_passage
from app.external_api.mcp_schemas import (
    CitationVerificationResult, ClaimEvidenceResult, EvidenceItem, ProvenanceInfo,
    RetrievalSummary, SourceInfo, SourcePage, SourcePaperInfo, SourceResult,
    VerificationMatches,
)
from app.core_service import evidence_gateway
from app.core_service.report_cache import ReportCache


class SourceContextUnavailableError(RuntimeError):
    pass


class InvalidClaimError(ValueError):
    pass


class CitationVerificationError(RuntimeError):
    pass


def _validate_claim(claim: str) -> str:
    if not isinstance(claim, str) or not claim.strip():
        raise InvalidClaimError("claim must be a non-empty string.")
    return claim.strip()


def get_source(cache: ReportCache, database_path: Path, report_id: str,
               evidence_id: str, context_pages: int = 1) -> SourceResult:
    if not isinstance(context_pages, int) or isinstance(context_pages, bool) or context_pages not in (0, 1, 2):
        raise ValueError("context_pages must be 0, 1, or 2.")
    item = cache.evidence(report_id, evidence_id)
    try:
        database_uri = Path(database_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(database_uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            bounds = connection.execute(
                """SELECT MIN(g.page_number),MAX(g.page_number) FROM pages g
                   JOIN attachments a ON a.id=g.attachment_id
                   JOIN papers p ON p.id=a.paper_id
                   WHERE a.zotero_key=? AND p.zotero_key=?""",
                (item.attachment_key, item.zotero_key)).fetchone()
            if not bounds or bounds[0] is None:
                raise SourceContextUnavailableError(
                    "No stored pages are available for this evidence attachment.")
            wanted_start = max(bounds[0], item.physical_pdf_page - context_pages)
            wanted_end = min(bounds[1], item.physical_pdf_page + context_pages)
            rows = connection.execute(
                """SELECT g.page_number,g.text FROM pages g
                   JOIN attachments a ON a.id=g.attachment_id
                   JOIN papers p ON p.id=a.paper_id
                   WHERE a.zotero_key=? AND p.zotero_key=?
                     AND g.page_number BETWEEN ? AND ?
                   ORDER BY g.page_number""",
                (item.attachment_key, item.zotero_key, wanted_start, wanted_end)).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise SourceContextUnavailableError(f"Stored source context is unavailable: {exc}") from exc
    if not rows or item.physical_pdf_page not in {row["page_number"] for row in rows}:
        raise SourceContextUnavailableError(
            "The stored page for this evidence item is unavailable or no longer matches its provenance.")
    pages = [SourcePage(pdf_page=row["page_number"], text=row["text"]) for row in rows]
    actual_start, actual_end = pages[0].pdf_page, pages[-1].pdf_page
    expected_pages = set(range(wanted_start, wanted_end + 1))
    status = "COMPLETE" if {page.pdf_page for page in pages} == expected_pages else "PARTIAL"
    text = "\n\n".join(f"--- PDF page {page.pdf_page} ---\n{page.text}" for page in pages)
    origin = "citation_tracing" if item.provenance == "citation_traced" else "original_retrieval"
    return SourceResult(
        report_id=report_id, evidence_id=evidence_id,
        paper=SourcePaperInfo(
            title=item.title, authors=list(item.authors), journal=item.journal or None,
            year=item.year, doi=item.doi or None, zotero_item_key=item.zotero_key),
        source=SourceInfo(
            attachment_key=item.attachment_key, evidence_page=item.physical_pdf_page,
            start_page=actual_start, end_page=actual_end, text=text, pages=pages,
            evidence_passage=item.original_text, status=status),
        provenance=ProvenanceInfo(
            attachment_key=item.attachment_key, origin=origin,
            parent_evidence_id=item.parent_evidence_id),
    )


def find_evidence_for_claim(search_service, cache: ReportCache, claim: str, top_k: int = 10,
                            evidence_types: list[str] | None = None, *,
                            database_path: Path) -> ClaimEvidenceResult:
    claim = _validate_claim(claim)
    result = evidence_gateway.find_evidence(
        search_service, claim, top_k=top_k, evidence_types=evidence_types,
        database_path=database_path, report_cache=cache)
    groups = {name: [] for name in ("DIRECT", "LIMITING", "SECONDARY", "CONTEXTUAL", "UNCERTAIN")}
    for item in result.evidence:
        groups[item.evidence_class].append(item)
    return ClaimEvidenceResult(
        claim=claim, report_id=result.report_id, direct_support=groups["DIRECT"],
        limiting=groups["LIMITING"], secondary=groups["SECONDARY"],
        contextual=groups["CONTEXTUAL"], uncertain=groups["UNCERTAIN"],
        returned_count=result.returned_count,
        total_classified_count=result.total_classified_count,
        retrieval_summary=result.retrieval_summary)


def verify_citation(cache: ReportCache, claim: str, report_id: str,
                    evidence_id: str) -> CitationVerificationResult:
    claim = _validate_claim(claim)
    item = cache.evidence(report_id, evidence_id)
    try:
        classification = classify_passage(claim, item.original_text, item.title)
    except Exception as exc:
        raise CitationVerificationError(f"Citation verification failed: {exc}") from exc

    if classification.evidence_class == EvidenceClass.DIRECT:
        status = "SUPPORTED"
    elif classification.evidence_class == EvidenceClass.SECONDARY:
        status = "SECONDARY_SUPPORT_ONLY"
    elif classification.evidence_class == EvidenceClass.LIMITING:
        status = "LIMITS_OR_CONTRADICTS"
    elif classification.evidence_class == EvidenceClass.CONTEXTUAL:
        status = "NOT_SUPPORTED"
    else:
        status = "INSUFFICIENT_CONTEXT"
    return CitationVerificationResult(
        claim=claim, report_id=report_id, evidence_id=evidence_id, status=status,
        reason=classification.rationale,
        evidence_class=classification.evidence_class.value,
        source_type=classification.source_type,
        matches=VerificationMatches(
            claim=classification.claim_match, material=classification.material_match,
            stimulus=classification.stimulus_match, phase=classification.phase_match))
