"""Assemble a classified, source-traced Evidence Report (V0.3).

This layers on top of the existing V0.2 hybrid retrieval and its
retrieval-level deduplication (`EvidenceService.build_evidence_pack`) without
changing either: retrieval breadth/parameters are untouched here. This module
only (1) merges duplicate Zotero paper records into one PaperIdentity,
(2) classifies each already-retrieved passage, (3) traces SECONDARY passages'
citations to a local primary source and independently re-retrieves and
re-classifies passages from it, and (4) ranks everything by evidential
usefulness rather than retrieval score.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from app.config import DATABASE_PATH
from app.evidence.citation_tracer import CitationStatus, load_attachment_fulltext, trace_citations
from app.evidence.classifier import EvidenceClass, classify_passage
from app.evidence.evidence_service import EvidenceService
from app.evidence.models import ClassifiedEvidence, EvidenceReport
from app.evidence.paper_identity import PaperIdentity, load_paper_records, normalize_doi, resolve_identities
from app.evidence.ranker import rank
from app.search.database import ReadOnlyDatabase
from app.search.hybrid_search import UnifiedSearchService


def _fallback_identity(paper_id: int, zotero_key: str, title: str, authors: tuple[str, ...],
                       year: str | None, doi: str) -> PaperIdentity:
    """Used only if a retrieved paper_id has since disappeared from `papers`."""
    return PaperIdentity(f"paper:{paper_id}", normalize_doi(doi), (zotero_key,), title, authors,
                          year, (paper_id,))


@dataclass(frozen=True)
class _Candidate:
    paper_id: int
    zotero_key: str
    attachment_key: str
    title: str
    authors: tuple[str, ...]
    journal: str
    year: str | None
    doi: str
    physical_pdf_page: int
    pdf_path: str
    text: str
    retrieval_score: float


class EvidenceReportService:
    def __init__(self, database_path: Path = DATABASE_PATH,
                 search_service: UnifiedSearchService | None = None,
                 evidence_service: EvidenceService | None = None) -> None:
        self.database_path = database_path
        self.search_service = search_service or UnifiedSearchService(database_path)
        self.evidence_service = evidence_service or EvidenceService(database_path, self.search_service)

    def build_report(self, query: str, *, collection: str | None = None, year_min: int | None = None,
                      year_max: int | None = None, journal: str | None = None, max_papers: int = 12,
                      max_passages_per_paper: int = 4, candidate_pool: int = 60,
                      citation_trace_top_k: int = 5) -> EvidenceReport:
        if not query.strip():
            raise ValueError("Evidence report query cannot be empty.")
        pack = self.evidence_service.build_evidence_pack(
            query, collection=collection, year_min=year_min, year_max=year_max, journal=journal,
            max_papers=max_papers, max_passages_per_paper=max_passages_per_paper,
            candidate_pool=candidate_pool)

        candidates = [_Candidate(p.paper_id, p.zotero_key, passage.attachment_key, p.title, p.authors,
                                  p.journal, p.year, p.doi, passage.physical_pdf_page, passage.pdf_path,
                                  passage.original_text, passage.hybrid_score)
                      for p in pack.papers for passage in p.passages]

        with ReadOnlyDatabase(self.database_path) as db:
            local_records = load_paper_records(db)
            identities = resolve_identities(local_records)

            distinct_retrieved_papers = {c.paper_id for c in candidates}
            distinct_retrieved_identities = {
                identities[pid].canonical_id for pid in distinct_retrieved_papers if pid in identities}
            duplicate_papers_merged = max(0, len(distinct_retrieved_papers) - len(distinct_retrieved_identities))

            items: list[ClassifiedEvidence] = []
            counter = 0
            for candidate in candidates:
                counter += 1
                identity = identities.get(candidate.paper_id) or _fallback_identity(
                    candidate.paper_id, candidate.zotero_key, candidate.title, candidate.authors,
                    candidate.year, candidate.doi)
                classification = classify_passage(query, candidate.text, candidate.title)
                items.append(ClassifiedEvidence(
                    evidence_id=f"E{counter}", paper_identity=identity, title=candidate.title,
                    authors=candidate.authors, journal=candidate.journal, year=candidate.year,
                    doi=candidate.doi, zotero_key=candidate.zotero_key, attachment_key=candidate.attachment_key,
                    physical_pdf_page=candidate.physical_pdf_page, pdf_path=candidate.pdf_path,
                    original_text=candidate.text, retrieval_score=candidate.retrieval_score,
                    evidence_class=classification.evidence_class,
                    evidence_confidence=classification.evidence_confidence, rationale=classification.rationale,
                    claim_match=classification.claim_match, stimulus_match=classification.stimulus_match,
                    material_match=classification.material_match, phase_match=classification.phase_match,
                    classification_source_type=classification.source_type, provenance="original_retrieval"))

            traced_items: list[ClassifiedEvidence] = []
            already_shown = {(c.attachment_key, c.physical_pdf_page, c.text) for c in candidates}
            for index, item in enumerate(items):
                if item.evidence_class != EvidenceClass.SECONDARY:
                    continue
                fulltext = load_attachment_fulltext(db, item.attachment_key)
                traces = trace_citations(item.original_text, fulltext, local_records)
                if not traces:
                    continue
                items[index] = replace(item, citation_traces=tuple(traces))
                for trace in traces:
                    if trace.status != CitationStatus.RESOLVED_LOCAL or trace.resolved_paper_id is None:
                        continue
                    results = self.search_service.search(
                        query, mode="hybrid", paper_id=trace.resolved_paper_id,
                        top_k=citation_trace_top_k, diversify_results=False)
                    for result in results:
                        # A citation-traced re-search commonly rediscovers a passage
                        # already surfaced by the original retrieval; the SECONDARY
                        # item's citation_traces already records that the primary was
                        # verified, so skip re-printing the identical passage.
                        if (result.attachment_key, result.page, result.text) in already_shown:
                            continue
                        already_shown.add((result.attachment_key, result.page, result.text))
                        counter += 1
                        classification = classify_passage(query, result.text, result.title)
                        primary_identity = identities.get(trace.resolved_paper_id) or trace.resolved_identity
                        traced_items.append(ClassifiedEvidence(
                            evidence_id=f"E{counter}", paper_identity=primary_identity, title=result.title,
                            authors=result.authors, journal=result.journal, year=result.year, doi=result.doi,
                            zotero_key=result.zotero_key, attachment_key=result.attachment_key,
                            physical_pdf_page=result.page, pdf_path=result.pdf_path, original_text=result.text,
                            retrieval_score=result.hybrid_score, evidence_class=classification.evidence_class,
                            evidence_confidence=classification.evidence_confidence,
                            rationale=classification.rationale, claim_match=classification.claim_match,
                            stimulus_match=classification.stimulus_match,
                            material_match=classification.material_match, phase_match=classification.phase_match,
                            classification_source_type=classification.source_type,
                            provenance="citation_traced", parent_evidence_id=items[index].evidence_id))

        ranked = rank(items + traced_items)
        return EvidenceReport(query, tuple(ranked), pack.candidate_count, pack.duplicate_passages_removed,
                              duplicate_papers_merged)


def build_evidence_report(query: str, **options) -> EvidenceReport:
    return EvidenceReportService().build_report(query, **options)
