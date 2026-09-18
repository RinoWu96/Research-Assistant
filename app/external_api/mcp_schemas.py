"""Public, minimal response schema for the `find_evidence` MCP tool.

These models describe only what an external agent needs to consume structured,
auditable evidence (Task 5 of the V0.4-A brief). They intentionally do not expose
internal Evidence Engine parameters (embedding model, hybrid search weights,
candidate pool size, chunk overlap, ranking coefficients) - those stay private to
`app.evidence`.
"""

from typing import Literal

from pydantic import BaseModel

# Mirrors app.evidence.classifier.EvidenceClass's values exactly; kept as a plain
# literal here (rather than derived from the enum) so the MCP-facing JSON schema
# is stable and human-readable regardless of internal refactors. A unit test
# (tests/test_mcp_adapter.py) asserts this stays in sync with EvidenceClass.
EvidenceTypeName = Literal["DIRECT", "SECONDARY", "LIMITING", "CONTEXTUAL", "UNCERTAIN"]


class PaperInfo(BaseModel):
    canonical_id: str
    title: str
    authors: list[str]
    journal: str | None
    year: str | None
    doi: str | None
    zotero_item_key: str
    zotero_item_keys_merged: list[str]


class PassageInfo(BaseModel):
    text: str
    pdf_page: int


class ProvenanceInfo(BaseModel):
    attachment_key: str
    origin: Literal["original_retrieval", "citation_tracing"]
    parent_evidence_id: str | None


class ParsedReference(BaseModel):
    doi: str | None
    title_guess: str | None
    authors_guess: str | None
    year: str | None


class ResolvedPaperRef(BaseModel):
    canonical_id: str
    title: str
    zotero_item_keys: list[str]


class CitationTraceInfo(BaseModel):
    citation_marker: str
    parsed_reference: ParsedReference
    resolution_status: str
    resolved_paper: ResolvedPaperRef | None


class EvidenceItem(BaseModel):
    evidence_id: str
    evidence_class: EvidenceTypeName
    classification_confidence: float
    rationale: str
    retrieval_score: float
    paper: PaperInfo
    passage: PassageInfo
    provenance: ProvenanceInfo
    citation_traces: list[CitationTraceInfo]


class RetrievalSummary(BaseModel):
    candidate_count: int
    duplicate_passages_removed: int
    duplicate_papers_merged: int


class FindEvidenceResult(BaseModel):
    query: str
    evidence: list[EvidenceItem]
    returned_count: int
    total_classified_count: int
    retrieval_summary: RetrievalSummary
