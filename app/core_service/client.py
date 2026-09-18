"""Thin HTTP client for the Research Assistant Core Service.

This is the one seam other processes use to reach the Core - the MCP proxy
(`app.external_api.mcp_adapter`) and, since V0.6, the desktop GUI (via
`app.ui.core_search_adapter.CoreSearchAdapter`) and any future local-LLM
integration. It never imports the Evidence Engine, the semantic index, or
the embedder: if no running, healthy Core can be found, it raises
`ResearchAssistantUnavailableError` and does nothing else - it never falls
back to doing the work itself. There is exactly one HTTP client
implementation; nothing else in this codebase makes its own HTTP calls to
the Core.
"""

import httpx

from app.core_service import discovery
from app.external_api.mcp_schemas import (
    CitationVerificationResult, ClaimEvidenceResult, FindEvidenceResult, SourceResult,
)

DEFAULT_TIMEOUT_SECONDS = 120.0
# /health and /status are designed to always be fast (V0.5, section 11) - a
# short, fixed timeout keeps a caller polling Core lifecycle (the desktop
# GUI) from ever hanging on an unresponsive process, regardless of the
# longer timeout appropriate for a real search/evidence request.
STATUS_TIMEOUT_SECONDS = 3.0


class CoreClientError(RuntimeError):
    """Base class for errors raised while talking to the Core Service."""


class ResearchAssistantUnavailableError(CoreClientError):
    """No running, healthy Core Service could be found."""


class ResearchAssistantLoadingError(CoreClientError):
    """The Core Service is running but still initializing (state=LOADING)."""


class InvalidQueryError(CoreClientError):
    """The Core rejected the query or top_k as invalid."""


class InvalidEvidenceTypeError(CoreClientError):
    """The Core rejected an evidence_types value as unknown."""


class DatabaseUnavailableError(CoreClientError):
    """The Core's local literature database or semantic index is unavailable."""


class EvidenceEngineError(CoreClientError):
    """The Core's Evidence Engine raised an unexpected error."""


class InvalidSearchRequestError(CoreClientError):
    """The Core rejected the /search or /evidence_pack query, mode, or options."""


class SearchEngineError(CoreClientError):
    """The Core's retrieval backend raised an unexpected error."""


class ReportNotFoundError(CoreClientError):
    pass


class ReportExpiredError(CoreClientError):
    pass


class EvidenceNotFoundError(CoreClientError):
    pass


class SourceContextUnavailableError(CoreClientError):
    pass


class InvalidClaimError(CoreClientError):
    pass


class CitationVerificationError(CoreClientError):
    pass


_ERROR_TYPES: dict[str, type[CoreClientError]] = {
    "InvalidQuery": InvalidQueryError,
    "InvalidEvidenceType": InvalidEvidenceTypeError,
    "DatabaseUnavailable": DatabaseUnavailableError,
    "EvidenceEngineError": EvidenceEngineError,
    "InvalidSearchRequest": InvalidSearchRequestError,
    "SearchEngineError": SearchEngineError,
    "ResearchAssistantLoading": ResearchAssistantLoadingError,
    "ResearchAssistantUnavailable": ResearchAssistantUnavailableError,
    "ReportNotFound": ReportNotFoundError,
    "ReportExpired": ReportExpiredError,
    "EvidenceNotFound": EvidenceNotFoundError,
    "SourceContextUnavailable": SourceContextUnavailableError,
    "InvalidClaim": InvalidClaimError,
    "CitationVerificationError": CitationVerificationError,
}


class CoreClient:
    """Talks to one Core Service, found via `app.core_service.discovery` unless
    `base_url` is given explicitly (mainly for tests pointed at a fixture Core)."""

    def __init__(self, base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._explicit_base_url = base_url
        self.timeout = timeout

    def _base_url(self) -> str:
        if self._explicit_base_url:
            return self._explicit_base_url
        base_url = discovery.discover_running_core()
        if base_url is None:
            raise ResearchAssistantUnavailableError(
                "Research Assistant Core is not running. Start it with "
                "'.venv\\Scripts\\python.exe -m app.core_service' and try again.")
        return base_url

    def _raise_for_error_response(self, base_url: str, response: httpx.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error_name = payload.get("error", "EvidenceEngineError")
        message = payload.get("message") or f"Core at {base_url} returned HTTP {response.status_code}."
        raise _ERROR_TYPES.get(error_name, EvidenceEngineError)(message)

    def health(self) -> bool:
        """True only if the Core answered `/health` itself; never raises."""
        try:
            base_url = self._base_url()
            response = httpx.get(f"{base_url}/health", timeout=STATUS_TIMEOUT_SECONDS)
            return response.status_code == 200 and response.json().get("ok") is True
        except Exception:
            return False

    def status(self) -> dict:
        """Return the Core's `/status` payload (state, readiness flags, paper_count)."""
        base_url = self._base_url()
        try:
            response = httpx.get(f"{base_url}/status", timeout=STATUS_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ResearchAssistantUnavailableError(
                f"Could not reach the Research Assistant Core at {base_url}: {exc}") from exc
        if response.status_code != 200:
            self._raise_for_error_response(base_url, response)
        return response.json()

    def find_evidence(self, query: str, top_k: int = 10,
                       evidence_types: list[str] | None = None) -> FindEvidenceResult:
        base_url = self._base_url()
        try:
            response = httpx.post(
                f"{base_url}/find_evidence",
                json={"query": query, "top_k": top_k, "evidence_types": evidence_types},
                timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ResearchAssistantUnavailableError(
                f"Could not reach the Research Assistant Core at {base_url}: {exc}") from exc
        if response.status_code != 200:
            self._raise_for_error_response(base_url, response)
        return FindEvidenceResult.model_validate(response.json())

    def get_source(self, report_id: str, evidence_id: str,
                   context_pages: int = 1) -> SourceResult:
        return SourceResult.model_validate(self._post(
            "/source", {"report_id": report_id, "evidence_id": evidence_id,
                        "context_pages": context_pages}))

    def find_evidence_for_claim(self, claim: str, top_k: int = 10,
                                evidence_types: list[str] | None = None) -> ClaimEvidenceResult:
        return ClaimEvidenceResult.model_validate(self._post(
            "/find_evidence_for_claim",
            {"claim": claim, "top_k": top_k, "evidence_types": evidence_types}))

    def verify_citation(self, claim: str, report_id: str,
                        evidence_id: str) -> CitationVerificationResult:
        return CitationVerificationResult.model_validate(self._post(
            "/verify_citation", {"claim": claim, "report_id": report_id,
                                 "evidence_id": evidence_id}))

    def _post(self, path: str, payload: dict) -> dict:
        base_url = self._base_url()
        try:
            response = httpx.post(f"{base_url}{path}", json=payload, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ResearchAssistantUnavailableError(
                f"Could not reach the Research Assistant Core at {base_url}: {exc}") from exc
        if response.status_code != 200:
            self._raise_for_error_response(base_url, response)
        return response.json()

    def search(self, query: str, mode: str = "hybrid", top_k: int = 20, **options) -> list[dict]:
        """Run the existing Keyword/Semantic/Hybrid retrieval backend via the Core.

        `options` (collection/year_min/year_max/journal/paper_id/result_level/
        max_chunks_per_paper/diversify_results) pass straight through to the
        Core's `/search` endpoint. Returns plain dicts shaped like
        `dataclasses.asdict(HybridResult)`; the caller (e.g.
        `app.ui.core_search_adapter.CoreSearchAdapter`) is responsible for
        converting them back into `HybridResult` if it needs the dataclass.
        """
        base_url = self._base_url()
        try:
            response = httpx.post(f"{base_url}/search",
                                  json={"query": query, "mode": mode, "top_k": top_k, **options},
                                  timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ResearchAssistantUnavailableError(
                f"Could not reach the Research Assistant Core at {base_url}: {exc}") from exc
        if response.status_code != 200:
            self._raise_for_error_response(base_url, response)
        return response.json()["results"]

    def build_evidence_pack(self, query: str, **options) -> dict:
        """Run the existing V0.2 evidence pack builder via the Core.

        Returns the pack as a plain dict (`EvidencePack.to_dict()`'s shape);
        the caller converts it back into `EvidencePack`/`PaperEvidence`/
        `EvidencePassage` if it needs the dataclasses.
        """
        base_url = self._base_url()
        try:
            response = httpx.post(f"{base_url}/evidence_pack", json={"query": query, **options},
                                  timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ResearchAssistantUnavailableError(
                f"Could not reach the Research Assistant Core at {base_url}: {exc}") from exc
        if response.status_code != 200:
            self._raise_for_error_response(base_url, response)
        return response.json()["pack"]
