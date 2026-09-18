"""Unified keyword, semantic and reciprocal-rank-fused retrieval backend."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import DATABASE_PATH
from app.embedding.semantic_index import SemanticIndexError
from app.embedding.semantic_index import SemanticIndex
from app.search.search_service import SearchService
from app.search.semantic_search import SemanticResult, SemanticSearchService, diversify


@dataclass(frozen=True)
class HybridResult:
    paper_id: int
    zotero_key: str
    attachment_key: str
    title: str
    authors: tuple[str, ...]
    year: str | None
    journal: str
    doi: str
    page_start: int
    page_end: int
    text: str
    pdf_path: str
    keyword_rank: int | None
    semantic_rank: int | None
    keyword_score: float | None
    semantic_score: float | None
    hybrid_score: float
    chunk_id: int | None = None

    @property
    def page(self) -> int:
        """Compatibility name used by the existing page-oriented desktop UI."""
        return self.page_start

    @property
    def snippet(self) -> str:
        """Always return an unmodified slice of the authoritative PDF text."""
        return self.text


@dataclass(frozen=True)
class PaperResult:
    paper_id: int
    zotero_key: str
    title: str
    year: str | None
    journal: str
    doi: str
    best_page: int
    best_chunk: str
    paper_score: float
    keyword_evidence: bool
    semantic_evidence: bool


def _diversify_hybrid(results: list[HybridResult], top_k: int, cap: int) -> list[HybridResult]:
    groups, order = {}, []
    for result in results:
        if result.paper_id not in groups:
            groups[result.paper_id] = []
            order.append(result.paper_id)
        if len(groups[result.paper_id]) < cap:
            groups[result.paper_id].append(result)
    output = []
    for depth in range(cap):
        for paper_id in order:
            if depth < len(groups[paper_id]):
                output.append(groups[paper_id][depth])
                if len(output) == top_k:
                    return output
    return output


def aggregate_papers(results: list[HybridResult], top_k: int) -> list[PaperResult]:
    """Interpretably combine a paper's best result plus 25% of its second-best."""
    groups: dict[int, list[HybridResult]] = {}
    for result in results:
        groups.setdefault(result.paper_id, []).append(result)
    papers = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item.hybrid_score, reverse=True)
        score = ordered[0].hybrid_score + (0.25 * ordered[1].hybrid_score if len(ordered) > 1 else 0)
        best = ordered[0]
        papers.append(PaperResult(best.paper_id, best.zotero_key, best.title, best.year,
            best.journal, best.doi, best.page_start, best.text, score,
            any(r.keyword_rank is not None for r in group),
            any(r.semantic_rank is not None for r in group)))
    return sorted(papers, key=lambda item: (-item.paper_score, item.paper_id))[:top_k]


class UnifiedSearchService:
    def __init__(self, database_path: Path = DATABASE_PATH, semantic_dir: Path | None = None,
                 *, rrf_constant: int = 60, semantic_index: SemanticIndex | None = None) -> None:
        if rrf_constant < 1:
            raise ValueError("RRF constant must be positive.")
        self.keyword = SearchService(database_path)
        self.semantic = SemanticSearchService(database_path, semantic_dir, semantic_index)
        self.rrf_constant = rrf_constant

    def search(self, query: str, mode: Literal["keyword", "semantic", "hybrid"] = "hybrid",
               top_k: int = 20, collection: str | None = None, year_min: int | None = None,
               year_max: int | None = None, journal: str | None = None, paper_id: int | None = None,
               result_level: Literal["chunk", "paper"] = "chunk",
               max_chunks_per_paper: int = 2, diversify_results: bool = True):
        if mode not in {"keyword", "semantic", "hybrid"} or result_level not in {"chunk", "paper"}:
            raise ValueError("Unsupported search mode or result level.")
        candidate_limit = top_k if mode == "keyword" else min(500, max(top_k * 8, top_k))
        keyword_results = []
        semantic_results: list[SemanticResult] = []
        semantic_failed = False
        if mode in {"keyword", "hybrid"}:
            keyword_results = self.keyword.search(query, collection=collection, year_min=year_min,
                year_max=year_max, journal=journal, paper_id=paper_id, limit=candidate_limit)
        if mode in {"semantic", "hybrid"}:
            try:
                semantic_results = self.semantic.search(query, top_k=candidate_limit,
                    collection=collection, year_min=year_min, year_max=year_max, journal=journal,
                    paper_id=paper_id, max_chunks_per_paper=max(2, max_chunks_per_paper),
                    diversify_results=diversify_results)
            except SemanticIndexError:
                if mode == "semantic":
                    raise
                # Hybrid remains useful and deterministic as keyword-only fallback.
                semantic_results = []
                semantic_failed = True
        keyword_by_page = {(result.paper_id, result.page): (rank, result)
                           for rank, result in enumerate(keyword_results, 1)}
        semantic_by_page = {}
        for rank, result in enumerate(semantic_results, 1):
            semantic_by_page.setdefault((result.paper_id, result.page_start), (rank, result))
        keys = set(keyword_by_page) | set(semantic_by_page)
        fused: list[HybridResult] = []
        for key in keys:
            keyword = keyword_by_page.get(key)
            semantic = semantic_by_page.get(key)
            kr, kw = keyword if keyword else (None, None)
            sr, sem = semantic if semantic else (None, None)
            base = sem or kw
            score = ((1 / (self.rrf_constant + kr)) if kr else 0) + ((1 / (self.rrf_constant + sr)) if sr else 0)
            fused.append(HybridResult(base.paper_id, base.zotero_key, base.attachment_key,
                base.title, base.authors, base.year, base.journal, base.doi,
                sem.page_start if sem else kw.page, sem.page_end if sem else kw.page,
                sem.text if sem else kw.text, base.pdf_path, kr, sr,
                kw.score if kw else None, sem.semantic_score if sem else None, score,
                sem.chunk_id if sem else None))
        fused.sort(key=lambda item: (-item.hybrid_score, item.paper_id, item.page_start))
        if mode == "semantic":
            # Preserve cosine order while using the unified result shape.
            fused.sort(key=lambda item: (item.semantic_rank or 10**9))
        elif mode == "keyword":
            fused.sort(key=lambda item: (item.keyword_rank or 10**9))
        if mode == "keyword" or semantic_failed:
            # Preserve the legacy FTS ordering and page behavior exactly.
            return aggregate_papers(fused, top_k) if result_level == "paper" else fused[:top_k]
        if not diversify_results:
            return aggregate_papers(fused, top_k) if result_level == "paper" else fused[:top_k]
        diverse = _diversify_hybrid(fused, candidate_limit if result_level == "paper" else top_k,
                                    max_chunks_per_paper)
        return aggregate_papers(diverse, top_k) if result_level == "paper" else diverse[:top_k]
