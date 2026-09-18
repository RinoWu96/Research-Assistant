"""Minimal localhost HTTP API for the Research Assistant Core Service.

Three endpoints only (see docs/v0.5-core-service.md):
  GET  /health         - always fast; never touches the database, index or model.
  GET  /status          - lifecycle state and readiness detail.
  POST /find_evidence   - the existing Evidence Engine, via app.core_service.evidence_gateway.

This module owns HTTP framing only - request parsing, status-code mapping,
and JSON encoding. All evidence-report shaping lives in
`app.core_service.evidence_gateway`; this module never reimplements it.
Uses only the standard library so the Core Service adds no new dependency.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging

from app.core_service import evidence_gateway
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
        else:
            self._send_json(404, {"error": "NotFound", "message": f"Unknown path {self.path!r}."})

    def _handle_find_evidence(self) -> None:
        resource_manager = self.server.resource_manager
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw_body or b"{}")
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": "InvalidRequest", "message": f"Malformed JSON body: {exc}"})
            return

        status = resource_manager.status()
        if status.state != CoreState.READY:
            loading = status.state == CoreState.LOADING
            self._send_json(503, {
                "error": "ResearchAssistantLoading" if loading else "ResearchAssistantUnavailable",
                "state": status.state.value,
                "message": (
                    "The Research Assistant Core is still initializing (database, semantic "
                    "index and embedding model); retry shortly." if loading else
                    f"The Research Assistant Core failed to initialize: {status.error}"),
            })
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
