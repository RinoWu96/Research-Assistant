"""Build diverse paper-grouped evidence packs from existing hybrid retrieval."""

from collections import defaultdict
from pathlib import Path
import re

from app.config import DATABASE_PATH
from app.evidence.models import EvidencePack, EvidencePassage, PaperEvidence
from app.search.hybrid_search import HybridResult, UnifiedSearchService


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.casefold()))


def _heavily_overlaps(left: HybridResult, right: HybridResult, threshold: float = 0.72) -> bool:
    if left.paper_id != right.paper_id or left.page != right.page:
        return False
    a, b = _tokens(left.text), _tokens(right.text)
    return bool(a and b and len(a & b) / min(len(a), len(b)) >= threshold)


def _deduplicate(results: list[HybridResult]) -> tuple[list[HybridResult], int]:
    kept: list[HybridResult] = []
    removed = 0
    for result in sorted(results, key=lambda item: (-item.hybrid_score, item.page, item.chunk_id or -1)):
        if any(_heavily_overlaps(result, previous) for previous in kept):
            removed += 1
        else:
            kept.append(result)
    return kept, removed


def _select_passages(results: list[HybridResult], limit: int) -> list[HybridResult]:
    """Prefer the best passage on different pages before taking a second same-page chunk."""
    ordered = sorted(results, key=lambda item: (-item.hybrid_score, item.page, item.chunk_id or -1))
    selected, seen_pages = [], set()
    for result in ordered:
        if result.page not in seen_pages:
            selected.append(result)
            seen_pages.add(result.page)
            if len(selected) == limit:
                return selected
    for result in ordered:
        if result not in selected:
            selected.append(result)
            if len(selected) == limit:
                break
    return selected


class EvidenceService:
    def __init__(self, database_path: Path = DATABASE_PATH,
                 search_service: UnifiedSearchService | None = None) -> None:
        self.search_service = search_service or UnifiedSearchService(database_path)

    def build_evidence_pack(self, query: str, *, collection: str | None = None,
                            year_min: int | None = None, year_max: int | None = None,
                            journal: str | None = None, max_papers: int = 8,
                            max_passages_per_paper: int = 3,
                            candidate_pool: int = 50) -> EvidencePack:
        if not query.strip():
            raise ValueError("Evidence query cannot be empty.")
        if (not 1 <= max_papers <= 50 or not 1 <= max_passages_per_paper <= 10
                or not 1 <= candidate_pool <= 500):
            raise ValueError("Invalid evidence pack limits.")
        candidate_limit = max(candidate_pool, max_papers * max_passages_per_paper)
        candidates = self.search_service.search(
            query, mode="hybrid", top_k=candidate_limit, collection=collection,
            year_min=year_min, year_max=year_max, journal=journal,
            max_chunks_per_paper=max(4, max_passages_per_paper * 2),
            diversify_results=False,
        )
        unique, removed = _deduplicate(candidates)
        grouped: dict[int, list[HybridResult]] = defaultdict(list)
        for result in unique:
            grouped[result.paper_id].append(result)
        ranked = []
        for paper_id, results in grouped.items():
            ordered = sorted(results, key=lambda item: -item.hybrid_score)
            score = ordered[0].hybrid_score + (0.25 * ordered[1].hybrid_score if len(ordered) > 1 else 0)
            ranked.append((score, paper_id, results))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        papers = []
        for relevance, _, results in ranked[:max_papers]:
            selected = _select_passages(results, max_passages_per_paper)
            best = selected[0]
            passages = tuple(EvidencePassage(
                result.paper_id, result.zotero_key, result.attachment_key, result.title,
                tuple(result.authors), result.year, result.journal, result.doi, result.page,
                result.text, result.hybrid_score, result.semantic_score,
                result.keyword_score, result.chunk_id, result.pdf_path,
            ) for result in selected)
            papers.append(PaperEvidence(best.paper_id, best.zotero_key, best.title,
                tuple(best.authors), best.year, best.journal, best.doi, relevance, passages))
        return EvidencePack(query, tuple(papers), len(candidates), removed)


def build_evidence_pack(query: str, **options) -> EvidencePack:
    return EvidenceService().build_evidence_pack(query, **options)
