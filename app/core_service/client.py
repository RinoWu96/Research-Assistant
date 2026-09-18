"""Thin HTTP client for the Research Assistant Core Service.

This is the one seam other processes use to reach the Core - the MCP proxy
today (`app.external_api.mcp_adapter`), and, later, the desktop GUI and any
local-LLM integration (see section 8 of the V0.5 brief: the API is shaped
so that migration needs no redesign here). It never imports the Evidence
Engine, the semantic index, or the embedder: if no running, healthy Core
can be found, it raises `ResearchAssistantUnavailableError` and does
nothing else - it never falls back to doing the work itself.
"""

import httpx

from app.core_service import discovery
from app.external_api.mcp_schemas import FindEvidenceResult

DEFAULT_TIMEOUT_SECONDS = 120.0


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


_ERROR_TYPES: dict[str, type[CoreClientError]] = {
    "InvalidQuery": InvalidQueryError,
    "InvalidEvidenceType": InvalidEvidenceTypeError,
    "DatabaseUnavailable": DatabaseUnavailableError,
    "EvidenceEngineError": EvidenceEngineError,
    "ResearchAssistantLoading": ResearchAssistantLoadingError,
    "ResearchAssistantUnavailable": ResearchAssistantUnavailableError,
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

        if response.status_code == 200:
            return FindEvidenceResult.model_validate(response.json())

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error_name = payload.get("error", "EvidenceEngineError")
        message = payload.get("message") or f"Core returned HTTP {response.status_code}."
        raise _ERROR_TYPES.get(error_name, EvidenceEngineError)(message)
