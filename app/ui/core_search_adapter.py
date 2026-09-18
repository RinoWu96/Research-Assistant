"""Thin GUI-specific adapter around `app.core_service.client.CoreClient`.

Gives the desktop GUI the same call shape `app.search.desktop_search.
DesktopSearchService` used (`.search()`, `.build_evidence_pack()`,
`.close()`), plus `.find_evidence()` for the new Evidence Report mode, so
`app.ui.main_window` does not need to change its rendering code - only
where results come from changes (V0.6 brief, section 3). No HTTP calls
happen directly from GUI widgets; every one of them goes through
`CoreClient` here, and this is the only place that translates the Core's
plain JSON dicts back into the dataclasses the rest of the GUI expects.
"""

from app.core_service.client import CoreClient
from app.evidence.models import EvidencePack, EvidencePassage, PaperEvidence
from app.external_api.mcp_schemas import FindEvidenceResult
from app.search.hybrid_search import HybridResult


class CoreSearchAdapter:
    def __init__(self, base_url: str) -> None:
        self._client = CoreClient(base_url=base_url)

    def search(self, query: str, **options) -> list[HybridResult]:
        results = []
        for item in self._client.search(query, **options):
            item = dict(item)
            item["authors"] = tuple(item["authors"])
            results.append(HybridResult(**item))
        return results

    def build_evidence_pack(self, query: str, **options) -> EvidencePack:
        payload = self._client.build_evidence_pack(query, **options)
        papers = []
        for paper in payload["papers"]:
            paper = dict(paper)
            paper["authors"] = tuple(paper["authors"])
            passages = []
            for passage in paper.pop("passages"):
                passage = dict(passage)
                passage["authors"] = tuple(passage["authors"])
                passages.append(EvidencePassage(**passage))
            papers.append(PaperEvidence(**paper, passages=tuple(passages)))
        return EvidencePack(payload["query"], tuple(papers), payload["candidate_count"],
                            payload["duplicate_passages_removed"])

    def find_evidence(self, query: str, **options) -> FindEvidenceResult:
        return self._client.find_evidence(query, **options)

    def close(self) -> None:
        """No persistent connection to close; kept for interface parity with
        the retired `DesktopSearchService` (see app.search.desktop_search)."""
