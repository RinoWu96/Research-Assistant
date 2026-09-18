"""V0.5: thin proxy between the `find_evidence` MCP tool and the Research
Assistant Core Service's HTTP API (see `app.core_service`).

The Core Service owns the Evidence Engine, the semantic index and the
embedder; this module never constructs or imports any of them - it only
forwards the request to the Core via `app.core_service.client.CoreClient`
and translates the result (or a clear `ResearchAssistantUnavailableError`
if the Core is not running) back to the MCP tool. All validation and
report-shaping happens once, in `app.core_service.evidence_gateway` - this
module does not duplicate it. Restarting Claude Code or this MCP process
does not restart the Core, so it never reloads the embedding model.
"""

from app.core_service import client as core_client
from app.external_api.mcp_schemas import FindEvidenceResult


class EvidenceMCPError(RuntimeError):
    """Base class for structured find_evidence errors surfaced to the calling agent."""


class InvalidQueryError(EvidenceMCPError):
    """The query or top_k input was invalid."""


class InvalidEvidenceTypeError(EvidenceMCPError):
    """An evidence_types entry was not one of the known evidence classes."""


class DatabaseUnavailableError(EvidenceMCPError):
    """The local literature database or semantic index could not be used."""


class EvidenceEngineError(EvidenceMCPError):
    """The Evidence Engine raised an unexpected error while building the report."""


class ResearchAssistantUnavailableError(EvidenceMCPError):
    """The Research Assistant Core Service is not running or not reachable.

    This proxy never falls back to loading the Evidence Engine itself - see
    the module docstring and section 15 of the V0.5 brief.
    """


class ResearchAssistantLoadingError(EvidenceMCPError):
    """The Research Assistant Core Service is running but still initializing."""


_CLIENT_ERROR_MAP: dict[type[core_client.CoreClientError], type[EvidenceMCPError]] = {
    core_client.InvalidQueryError: InvalidQueryError,
    core_client.InvalidEvidenceTypeError: InvalidEvidenceTypeError,
    core_client.DatabaseUnavailableError: DatabaseUnavailableError,
    core_client.EvidenceEngineError: EvidenceEngineError,
    core_client.ResearchAssistantLoadingError: ResearchAssistantLoadingError,
    core_client.ResearchAssistantUnavailableError: ResearchAssistantUnavailableError,
}

_client = core_client.CoreClient()


def find_evidence(query: str, top_k: int = 10, evidence_types: list[str] | None = None) -> FindEvidenceResult:
    """Forward a find_evidence request to the Research Assistant Core Service.

    Performs no retrieval, ranking, classification, citation tracing, or
    input validation itself - the Core (`app.core_service.evidence_gateway`)
    is the single source of truth for all of that. This function only calls
    the Core over HTTP and translates its response, or the Core's absence,
    into the typed errors the MCP server surfaces as structured tool errors.
    """
    try:
        return _client.find_evidence(query, top_k=top_k, evidence_types=evidence_types)
    except tuple(_CLIENT_ERROR_MAP) as exc:
        raise _CLIENT_ERROR_MAP[type(exc)](str(exc)) from exc
