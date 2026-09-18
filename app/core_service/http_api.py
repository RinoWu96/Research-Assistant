"""Minimal localhost HTTP API for the Research Assistant Core Service.

Endpoints (see docs/v0.5-core-service.md, docs/v0.6-gui-core-migration.md):
  GET  /health         - always fast; never touches the database, index or model.
  GET  /status         - lifecycle state and readiness detail.
  POST /find_evidence  - the existing Evidence Engine, via app.core_service.evidence_gateway.
  POST /search          - the existing unified Keyword/Semantic/Hybrid retrieval
                           backend, via app.core_service.search_gateway. Added in
                           V0.6 for the desktop GUI; MCP does not use it.
  POST /evidence_pack    - the existing V0.2 evidence pack builder, via
                           app.core_service.search_gateway. Added in V0.6 for the
                           desktop GUI's Evidence Pack mode; MCP does not use it.

This module owns HTTP framing only - request parsing, status-code mapping,
and JSON encoding. All retrieval/evidence shaping lives in
`app.core_service.evidence_gateway` and `app.core_service.search_gateway`;
this module never reimplements either. Uses only the standard library so
the Core Service adds no new dependency.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging

from app.core_service import evidence_gateway, search_gateway
from app.core_service.resource_manager import ResourceManager
from app.core_service.state import CoreState

logger = logging.getLogger(__name__)


class CoreHTTPServer(ThreadingHTTPServer):
    """A `ThreadingHTTPServer` carrying the one `ResourceManager` every request shares."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, server_address: tuple[str, int], resource_manager: ResourceManager) -> None:
        self.resource_manager = resource_manager
        super().__init__(server_address, CoreRequestHandler)


def _status_payload(resource_manager: ResourceManager) -> dict:
    status = resource_manager.status()
    payload = {
        "state": status.state.value,
        "database_ready": status.database_ready,
        "semantic_index_ready": status.semantic_index_ready,
        "embedding_model_ready": status.embedding_model_ready,
        "paper_count": status.paper_count,
    }
    if status.error:
        payload["error"] = status.error
    return payload


class CoreRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: CoreHTTPServer

    def log_message(self, format: str, *args) -> None:  # match BaseHTTPRequestHandler's signature
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True})
        elif self.path == "/status":
            self._send_json(200, _status_payload(self.server.resource_manager))
        else:
            self._send_json(404, {"error": "NotFound", "message": f"Unknown path {self.path!r}."})

    def do_POST(self) -> None:
        if self.path == "/find_evidence":
            self._handle_find_evidence()
        elif self.path == "/search":
            self._handle_search()
        elif self.path == "/evidence_pack":
            self._handle_evidence_pack()
        else:
            self._send_json(404, {"error": "NotFound", "message": f"Unknown path {self.path!r}."})

    def _read_json_body(self) -> dict | None:
        """Return the parsed request body, or None after already sending a 400."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw_body or b"{}")
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": "InvalidRequest", "message": f"Malformed JSON body: {exc}"})
            return None

    def _not_ready_response(self, resource_manager: ResourceManager) -> dict | None:
        """Return a 503 body if the Core is not READY, after already sending it; else None."""
        status = resource_manager.status()
        if status.state == CoreState.READY:
            return None
        loading = status.state == CoreState.LOADING
        payload = {
            "error": "ResearchAssistantLoading" if loading else "ResearchAssistantUnavailable",
            "state": status.state.value,
            "message": (
                "The Research Assistant Core is still initializing (database, semantic "
                "index and embedding model); retry shortly." if loading else
                f"The Research Assistant Core failed to initialize: {status.error}"),
        }
        self._send_json(503, payload)
        return payload

    def _handle_find_evidence(self) -> None:
        resource_manager = self.server.resource_manager
        body = self._read_json_body()
        if body is None:
            return
        if self._not_ready_response(resource_manager) is not None:
            return

        try:
            result = evidence_gateway.find_evidence(
                resource_manager.search_service,
                query=body.get("query", ""),
                top_k=body.get("top_k", 10),
                evidence_types=body.get("evidence_types"),
                database_path=resource_manager.database_path,
            )
        except evidence_gateway.InvalidQueryError as exc:
            self._send_json(400, {"error": "InvalidQuery", "message": str(exc)})
        except evidence_gateway.InvalidEvidenceTypeError as exc:
            self._send_json(400, {"error": "InvalidEvidenceType", "message": str(exc)})
        except evidence_gateway.DatabaseUnavailableError as exc:
            self._send_json(503, {"error": "DatabaseUnavailable", "message": str(exc)})
        except evidence_gateway.EvidenceEngineError as exc:
            self._send_json(500, {"error": "EvidenceEngineError", "message": str(exc)})
        else:
            self._send_json(200, result.model_dump())

    _SEARCH_OPTION_KEYS = ("collection", "year_min", "year_max", "journal", "paper_id",
                          "result_level", "max_chunks_per_paper", "diversify_results")

    def _handle_search(self) -> None:
        resource_manager = self.server.resource_manager
        body = self._read_json_body()
        if body is None:
            return
        if self._not_ready_response(resource_manager) is not None:
            return

        options = {key: body[key] for key in self._SEARCH_OPTION_KEYS if key in body}
        try:
            results = search_gateway.search(
                resource_manager.search_service, query=body.get("query", ""),
                mode=body.get("mode", "hybrid"), top_k=body.get("top_k", 20), **options)
        except search_gateway.InvalidSearchRequestError as exc:
            self._send_json(400, {"error": "InvalidSearchRequest", "message": str(exc)})
        except search_gateway.SearchEngineError as exc:
            self._send_json(500, {"error": "SearchEngineError", "message": str(exc)})
        else:
            self._send_json(200, {"results": results})

    _EVIDENCE_PACK_OPTION_KEYS = ("collection", "year_min", "year_max", "journal",
                                 "max_papers", "max_passages_per_paper", "candidate_pool")

    def _handle_evidence_pack(self) -> None:
        resource_manager = self.server.resource_manager
        body = self._read_json_body()
        if body is None:
            return
        if self._not_ready_response(resource_manager) is not None:
            return

        options = {key: body[key] for key in self._EVIDENCE_PACK_OPTION_KEYS if key in body}
        try:
            pack = search_gateway.build_evidence_pack(
                resource_manager.search_service, query=body.get("query", ""),
                database_path=resource_manager.database_path, **options)
        except search_gateway.InvalidSearchRequestError as exc:
            self._send_json(400, {"error": "InvalidSearchRequest", "message": str(exc)})
        except search_gateway.SearchEngineError as exc:
            self._send_json(500, {"error": "SearchEngineError", "message": str(exc)})
        else:
            self._send_json(200, {"pack": pack})
