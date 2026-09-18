"""Tests for app/core_service/http_api.py: the Core Service's three HTTP
endpoints (V0.5 brief, sections 2-3, 5, 11-12).

A fake `ResourceManager` (matching the small surface `http_api` actually
uses - `.status()` and `.search_service`) drives each lifecycle state
directly, so these tests do not depend on real database/index/model
initialization; `tests/test_mcp_integration.py` covers the real pipeline
end to end.
"""

from contextlib import contextmanager
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import httpx

from app.core_service import evidence_gateway
from app.core_service.http_api import CoreHTTPServer
from app.core_service.resource_manager import ResourceStatus
from app.core_service.state import CoreState
from app.external_api.mcp_schemas import FindEvidenceResult, RetrievalSummary

READY_STATUS = ResourceStatus(CoreState.READY, database_ready=True, semantic_index_ready=True,
                              embedding_model_ready=True, paper_count=201, error=None)
LOADING_STATUS = ResourceStatus(CoreState.LOADING, database_ready=True, semantic_index_ready=False,
                                embedding_model_ready=False, paper_count=201, error=None)
ERROR_STATUS = ResourceStatus(CoreState.ERROR, database_ready=True, semantic_index_ready=False,
                              embedding_model_ready=False, paper_count=None,
                              error="Semantic index is missing or incomplete.")


class _FakeResourceManager:
    def __init__(self, status: ResourceStatus, search_service=object()):
        self._status = status
        self.search_service = search_service
        self.database_path = Path("fake-literature.db")

    def status(self) -> ResourceStatus:
        return self._status


@contextmanager
def _running_server(resource_manager):
    server = CoreHTTPServer(("127.0.0.1", 0), resource_manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fake_result() -> FindEvidenceResult:
    return FindEvidenceResult(
        query="claim", evidence=[], returned_count=0, total_classified_count=0,
        retrieval_summary=RetrievalSummary(candidate_count=0, duplicate_passages_removed=0,
                                           duplicate_papers_merged=0))


class HealthEndpointTests(unittest.TestCase):
    def test_health_is_ok_even_while_loading(self):
        with _running_server(_FakeResourceManager(LOADING_STATUS)) as base_url:
            response = httpx.get(f"{base_url}/health", timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_health_never_calls_status(self):
        manager = _FakeResourceManager(READY_STATUS)
        with patch.object(manager, "status") as fake_status:
            with _running_server(manager) as base_url:
                httpx.get(f"{base_url}/health", timeout=5)
            fake_status.assert_not_called()


class StatusEndpointTests(unittest.TestCase):
    def test_reports_loading_state_and_partial_readiness(self):
        with _running_server(_FakeResourceManager(LOADING_STATUS)) as base_url:
            response = httpx.get(f"{base_url}/status", timeout=5)
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["state"], "LOADING")
        self.assertTrue(body["database_ready"])
        self.assertFalse(body["embedding_model_ready"])
        self.assertEqual(body["paper_count"], 201)
        self.assertNotIn("error", body)

    def test_reports_ready_state(self):
        with _running_server(_FakeResourceManager(READY_STATUS)) as base_url:
            body = httpx.get(f"{base_url}/status", timeout=5).json()
        self.assertEqual(body["state"], "READY")
        self.assertTrue(body["embedding_model_ready"])

    def test_reports_error_state_with_a_message_but_no_filesystem_paths(self):
        with _running_server(_FakeResourceManager(ERROR_STATUS)) as base_url:
            body = httpx.get(f"{base_url}/status", timeout=5).json()
        self.assertEqual(body["state"], "ERROR")
        self.assertIn("error", body)
        self.assertNotIn(":\\", body["error"])  # no raw Windows path leaked


class FindEvidenceEndpointTests(unittest.TestCase):
    def test_returns_503_while_loading_without_calling_the_gateway(self):
        with patch.object(evidence_gateway, "find_evidence") as fake_find:
            with _running_server(_FakeResourceManager(LOADING_STATUS)) as base_url:
                response = httpx.post(f"{base_url}/find_evidence", json={"query": "claim"}, timeout=5)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "ResearchAssistantLoading")
        fake_find.assert_not_called()

    def test_returns_503_with_error_detail_when_core_state_is_error(self):
        with _running_server(_FakeResourceManager(ERROR_STATUS)) as base_url:
            response = httpx.post(f"{base_url}/find_evidence", json={"query": "claim"}, timeout=5)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "ResearchAssistantUnavailable")

    def test_ready_state_calls_the_gateway_with_the_shared_search_service(self):
        search_service = object()
        manager = _FakeResourceManager(READY_STATUS, search_service=search_service)
        with patch.object(evidence_gateway, "find_evidence", return_value=_fake_result()) as fake_find:
            with _running_server(manager) as base_url:
                response = httpx.post(
                    f"{base_url}/find_evidence",
                    json={"query": "claim", "top_k": 3, "evidence_types": ["DIRECT"]}, timeout=5)
        self.assertEqual(response.status_code, 200)
        fake_find.assert_called_once_with(
            search_service, query="claim", top_k=3, evidence_types=["DIRECT"],
            database_path=manager.database_path)

    def test_invalid_query_maps_to_400(self):
        with patch.object(evidence_gateway, "find_evidence",
                          side_effect=evidence_gateway.InvalidQueryError("bad")):
            with _running_server(_FakeResourceManager(READY_STATUS)) as base_url:
                response = httpx.post(f"{base_url}/find_evidence", json={"query": ""}, timeout=5)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "InvalidQuery")

    def test_database_unavailable_maps_to_503(self):
        with patch.object(evidence_gateway, "find_evidence",
                          side_effect=evidence_gateway.DatabaseUnavailableError("down")):
            with _running_server(_FakeResourceManager(READY_STATUS)) as base_url:
                response = httpx.post(f"{base_url}/find_evidence", json={"query": "claim"}, timeout=5)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "DatabaseUnavailable")

    def test_unexpected_engine_error_maps_to_500(self):
        with patch.object(evidence_gateway, "find_evidence",
                          side_effect=evidence_gateway.EvidenceEngineError("boom")):
            with _running_server(_FakeResourceManager(READY_STATUS)) as base_url:
                response = httpx.post(f"{base_url}/find_evidence", json={"query": "claim"}, timeout=5)
        self.assertEqual(response.status_code, 500)

    def test_malformed_json_body_maps_to_400(self):
        with _running_server(_FakeResourceManager(READY_STATUS)) as base_url:
            response = httpx.post(f"{base_url}/find_evidence", content=b"not json",
                                  headers={"Content-Type": "application/json"}, timeout=5)
        self.assertEqual(response.status_code, 400)

    def test_unknown_path_is_404(self):
        with _running_server(_FakeResourceManager(READY_STATUS)) as base_url:
            response = httpx.get(f"{base_url}/nope", timeout=5)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
