"""Neutral evidence data models and deterministic export formats."""

from dataclasses import asdict, dataclass, field
import json
from typing import Literal

from app.evidence.citation_tracer import CitationTrace
from app.evidence.classifier import EvidenceClass
from app.evidence.paper_identity import PaperIdentity


@dataclass(frozen=True)
class EvidencePassage:
    paper_id: int
    zotero_key: str
    attachment_key: str
    title: str
    authors: tuple[str, ...]
    year: str | None
    journal: str
    doi: str
    physical_pdf_page: int
    original_text: str
    hybrid_score: float
    semantic_score: float | None
    keyword_score: float | None
    chunk_id: int | None
    pdf_path: str
    supplementary: bool = False


@dataclass(frozen=True)
class PaperEvidence:
    paper_id: int
    zotero_key: str
    title: str
    authors: tuple[str, ...]
    year: str | None
    journal: str
    doi: str
    relevance_score: float
    passages: tuple[EvidencePassage, ...]

    @property
    def best_page(self) -> int:
        return self.passages[0].physical_pdf_page

    @property
    def best_passage(self) -> str:
        return self.passages[0].original_text


@dataclass(frozen=True)
class EvidencePack:
    query: str
    papers: tuple[PaperEvidence, ...]
    candidate_count: int
    duplicate_passages_removed: int

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_markdown(self, *, compact: bool = False, for_ai: bool = False) -> str:
        lines = []
        if for_ai:
            lines.extend([
                "The following material contains original passages retrieved from my local literature library. "
                "Use only these sources when evaluating the claim. Do not invent references.", "",
            ])
        lines.extend(["# Literature Evidence Pack", "", "## Research question / claim", "", self.query, "",
                      "## Important instruction", "",
                      "The passages below are original text retrieved from my local literature library. "
                      "Evaluate them carefully and do not invent references or claims not supported by these sources.", ""])
        for number, paper in enumerate(self.papers, 1):
            lines.extend([
                f"## Paper {number}", "", f"**Title:** {paper.title}",
                f"**Authors:** {'; '.join(paper.authors) or 'Not provided'}",
                f"**Journal:** {paper.journal or 'Not provided'}",
                f"**Year:** {paper.year or 'Not provided'}",
                f"**DOI:** {paper.doi or 'Not provided'}",
                f"**Zotero key:** {paper.zotero_key}", "",
            ])
            passages = paper.passages[:1] if compact else paper.passages
            for passage in passages:
                lines.extend([
                    f"### Candidate passage · PDF page {passage.physical_pdf_page}", "",
                    f"PDF attachment key: {passage.attachment_key}"
                    + (" (supporting information)" if passage.supplementary else ""), "",
                    *[f"> {line}" if line else ">" for line in passage.original_text.splitlines()], "",
                ])
        return "\n".join(lines).rstrip() + "\n"

    def to_ai_markdown(self) -> str:
        prompt = """You are assisting with scientific manuscript writing.

Below is a literature evidence pack containing original passages from my local Zotero library.

Task:
Evaluate the research question or claim using only the supplied literature evidence.

Requirements:
- Do not invent references.
- Distinguish direct evidence from indirect/contextual evidence.
- Preserve scientific uncertainty.
- When citing evidence, identify the paper and PDF page.
- If the supplied evidence is insufficient, explicitly say so.

Research question / claim:
{query}

Evidence:
""".format(query=self.query)
        return prompt + self.to_markdown(compact=False)


@dataclass(frozen=True)
class ClassifiedEvidence:
    """One classified, source-traced passage. Task 8/9: original text and generated
    rationale are kept in separate fields, and every provenance field needed for an
    audit trail is preserved even when the passage came from citation tracing."""

    evidence_id: str
    paper_identity: PaperIdentity
    title: str
    authors: tuple[str, ...]
    journal: str
    year: str | None
    doi: str
    zotero_key: str
    attachment_key: str
    physical_pdf_page: int
    pdf_path: str
    original_text: str
    retrieval_score: float
    evidence_class: EvidenceClass
    evidence_confidence: float
    rationale: str
    claim_match: bool
    stimulus_match: bool
    material_match: bool
    phase_match: bool
    classification_source_type: str
    provenance: Literal["original_retrieval", "citation_traced"] = "original_retrieval"
    parent_evidence_id: str | None = None
    citation_traces: tuple[CitationTrace, ...] = field(default_factory=tuple)
    supplementary: bool = False


@dataclass(frozen=True)
class EvidenceReport:
    """A ranked, classified, source-traced evidence report for one claim/query."""

    query: str
    items: tuple[ClassifiedEvidence, ...]
    candidate_count: int
    duplicate_passages_removed: int
    duplicate_papers_merged: int

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def to_markdown(self) -> str:
        """Task 7 report layout: evidence grouped by scientific relationship, not score.

        Items belonging to the same deduplicated PaperIdentity (Task 1) - e.g. two
        Zotero records of the same paper, or a passage found once by original
        retrieval and again through citation tracing - are rendered under one
        heading per identity per section, so a duplicate library record never
        shows up as if it were a second, different paper.
        """
        from app.evidence.ranker import TIER_ORDER, EvidenceTier, tier_for

        by_tier: dict[EvidenceTier, list[ClassifiedEvidence]] = {tier: [] for tier in TIER_ORDER}
        for item in self.items:
            by_tier[tier_for(item)].append(item)

        def group_by_identity(items: list[ClassifiedEvidence]) -> list[list[ClassifiedEvidence]]:
            groups: dict[str, list[ClassifiedEvidence]] = {}
            order: list[str] = []
            for item in items:
                key = item.paper_identity.canonical_id
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(item)
            return [groups[key] for key in order]

        lines = ["# Evidence Report", "", "## Claim", "", self.query, ""]
        direct = by_tier[EvidenceTier.DIRECT_PRIMARY] + by_tier[EvidenceTier.DIRECT_UNVERIFIED]
        if not direct:
            lines.extend(["> **Local literature appears insufficient**: no passage in the retrieved "
                          "candidates was classified as DIRECT evidence for this claim.", ""])

        def render_group(group: list[ClassifiedEvidence], *, show_class: bool = False) -> list[str]:
            identity = group[0].paper_identity
            block = [
                f"### {identity.title}",
                f"- Authors: {'; '.join(identity.authors) or 'Not provided'}",
                f"- Journal: {group[0].journal or 'Not provided'} ({identity.year or 'n.d.'})",
                f"- DOI: {identity.doi or 'Not provided'}",
            ]
            if len(identity.zotero_item_keys) > 1:
                block.append("- Duplicate Zotero records merged into this one paper: "
                             + ", ".join(identity.zotero_item_keys))
            for item in group:
                provenance_note = (f" _(via citation tracing from {item.parent_evidence_id})_"
                                   if item.provenance == "citation_traced" else "")
                si_note = ", supporting information" if item.supplementary else ""
                block.append(f"- Passage (Zotero item {item.zotero_key}, attachment {item.attachment_key}{si_note}, "
                             f"PDF page {item.physical_pdf_page}){provenance_note}:")
                block.append("")
                block.extend(f"  > {line}" if line else "  >" for line in item.original_text.splitlines())
                block.append("")
                if show_class:
                    block.append(f"  - Evidence class: {item.evidence_class.value} "
                                 f"(classification confidence {item.evidence_confidence:.2f})")
                block.append(f"  - Why it matters: {item.rationale}")
                block.append("")
            return block

        lines.extend(["## Strong direct evidence", ""])
        if direct:
            for group in group_by_identity(direct):
                lines.extend(render_group(group))
        else:
            lines.extend(["_None found in the retrieved local literature._", ""])

        lines.extend(["## Secondary evidence", ""])
        secondary = by_tier[EvidenceTier.SECONDARY]
        if secondary:
            for group in group_by_identity(secondary):
                lines.extend(render_group(group, show_class=True))
                for item in group:
                    if not item.citation_traces:
                        continue
                    lines.append(f"- Primary source tracing for {item.zotero_key}:")
                    for trace in item.citation_traces:
                        if trace.status.value == "resolved_local" and trace.resolved_identity:
                            lines.append(f"  - {trace.marker}: resolved to **{trace.resolved_identity.title}** "
                                         f"(Zotero item(s): {', '.join(trace.resolved_identity.zotero_item_keys)})")
                        elif trace.status.value == "reference_identified_but_not_in_library":
                            lines.append(f"  - {trace.marker}: reference identified "
                                         f"({trace.reference_title_guess or trace.reference_doi or 'details incomplete'}) "
                                         f"but not found in the local library.")
                        elif trace.status.value == "ambiguous":
                            lines.append(f"  - {trace.marker}: matched more than one local paper; "
                                         f"left unresolved rather than guessing.")
                        else:
                            lines.append(f"  - {trace.marker}: unresolved (reference could not be located in "
                                         f"this paper's bibliography).")
                    lines.append("")
        else:
            lines.extend(["_None found._", ""])

        lines.extend(["## Limiting evidence", ""])
        limiting = by_tier[EvidenceTier.LIMITING]
        if limiting:
            for group in group_by_identity(limiting):
                lines.extend(render_group(group))
        else:
            lines.extend(["_None found. Absence of limiting evidence is not evidence of universality._", ""])

        lines.extend(["## Contextual evidence", ""])
        contextual = by_tier[EvidenceTier.CONTEXTUAL]
        if contextual:
            for group in group_by_identity(contextual):
                lines.extend(render_group(group))
        else:
            lines.extend(["_None found._", ""])

        lines.extend(["## Uncertain evidence", ""])
        uncertain = by_tier[EvidenceTier.UNCERTAIN]
        if uncertain:
            for group in group_by_identity(uncertain):
                lines.extend(render_group(group))
        else:
            lines.extend(["_None found._", ""])

        lines.extend(["## Unresolved citations", ""])
        unresolved_lines = []
        for item in secondary:
            for trace in item.citation_traces:
                if trace.status.value != "resolved_local":
                    unresolved_lines.append(
                        f"- {trace.marker} in \"{item.title}\": status = {trace.status.value}"
                        + (f", guessed title: {trace.reference_title_guess}" if trace.reference_title_guess else ""))
        lines.extend(unresolved_lines or ["_No unresolved citations among the traced secondary evidence._"])
        lines.append("")

        lines.extend([
            "## Retrieval summary", "",
            f"- Candidate passages considered: {self.candidate_count}",
            f"- Duplicate overlapping passages removed: {self.duplicate_passages_removed}",
            f"- Duplicate Zotero paper records merged by identity: {self.duplicate_papers_merged}",
            "",
        ])
        return "\n".join(lines).rstrip() + "\n"
