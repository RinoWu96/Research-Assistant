"""Gateway between the Core Service's `/search` and `/evidence_pack` HTTP
handlers and the existing unified retrieval backend
(`app.search.hybrid_search.UnifiedSearchService`) and the existing V0.2
evidence pack builder (`app.evidence.evidence_service.EvidenceService`).

No retrieval or evidence-pack logic lives here - only input validation and
shaping dataclass results into plain JSON-safe dicts for the HTTP layer.
This is what the V0.6 desktop GUI's Keyword/Semantic/Hybrid/Evidence Pack
modes call through (via app.core_service.client.CoreClient), reusing the
Core's one shared, already-loaded `UnifiedSearchService` instead of each
mode constructing (and, for the old GUI, reloading per call) its own.

`find_evidence`'s V0.3+ Evidence Report pipeline (classification, citation
tracing, ranking) lives in `app.core_service.evidence_gateway` and is
untouched by this module.
"""

from dataclasses import asdict
from pathlib import Path

from app.config import DATABASE_PATH
from app.evidence.evidence_service import EvidenceService
from app.search.hybrid_search import UnifiedSearchService

MIN_TOP_K = 1
MAX_TOP_K = 500
VALID_MODES = frozenset({"keyword", "semantic", "hybrid"})


class SearchGatewayError(RuntimeError):
    """Base class for structured /search and /evidence_pack errors."""


class InvalidSearchRequestError(SearchGatewayError):
    """The query, mode, or top_k input was invalid."""


class SearchEngineError(SearchGatewayError):
    """The retrieval backend raised an unexpected error."""


def search(search_service: UnifiedSearchService, query: str, mode: str = "hybrid", top_k: int = 20,
           **options) -> list[dict]:
    """Run the existing unified retrieval backend and return plain JSON-safe dicts.

    `search_service` is the Core's one process-wide `UnifiedSearchService`;
    this function never constructs its own. `options` passes straight
    through to `UnifiedSearchService.search` (collection/year_min/year_max/
    journal/paper_id/result_level/max_chunks_per_paper/diversify_results) -
    its own validation surfaces as `InvalidSearchRequestError` below.
    """
    if not isinstance(query, str) or not query.strip():
        raise InvalidSearchRequestError("query must be a non-empty string.")
    if mode not in VALID_MODES:
        raise InvalidSearchRequestError(f"mode must be one of {sorted(VALID_MODES)}.")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not MIN_TOP_K <= top_k <= MAX_TOP_K:
        raise InvalidSearchRequestError(f"top_k must be an integer between {MIN_TOP_K} and {MAX_TOP_K}.")
    try:
        results = search_service.search(query, mode=mode, top_k=top_k, **options)
    except ValueError as exc:
        raise InvalidSearchRequestError(str(exc)) from exc
    except Exception as exc:
        raise SearchEngineError(f"Search backend failed: {exc}") from exc
    return [asdict(result) for result in results]


def build_evidence_pack(search_service: UnifiedSearchService, query: str, *,
                         database_path: Path = DATABASE_PATH, **options) -> dict:
    """Run the existing V0.2 evidence pack builder and return its `.to_dict()`.

    Reuses the Core's shared `search_service` (the same object `/search`
    uses) rather than constructing a new one - `EvidenceService` itself
    only wraps it, it is not a second heavy resource.
    """
    if not isinstance(query, str) or not query.strip():
        raise InvalidSearchRequestError("query must be a non-empty string.")
    try:
        pack = EvidenceService(database_path, search_service).build_evidence_pack(query, **options)
    except ValueError as exc:
        raise InvalidSearchRequestError(str(exc)) from exc
    except Exception as exc:
        raise SearchEngineError(f"Evidence pack backend failed: {exc}") from exc
    return pack.to_dict()
