"""Unit tests for app/core_service/client.py's new V0.6 methods
(`.health()`, `.status()`, `.search()`, `.build_evidence_pack()`) - the
one HTTP client implementation the MCP proxy and the desktop GUI both
reuse (V0.6 brief, section 3). `.find_evidence()` is unchanged and already
covered by tests/test_mcp_adapter.py and tests/test_mcp_integration.py.

Mocks `httpx.get`/`httpx.post` directly (the module-level functions
`CoreClient` calls) rather than a real HTTP server, so these run instantly
and never touch a socket.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.core_service.client import (
    CoreClient,
    InvalidSearchRequestError,
    ResearchAssistantUnavailableError,
    SearchEngineError,
)

BASE_URL = "http://127.0.0.1:8756"


def _response(status_code: int, payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


class HealthTests(unittest.TestCase):
    def test_true_when_ok(self):
        with patch("httpx.get", return_value=_response(200, {"ok": True})):
            self.assertTrue(CoreClient(base_url=BASE_URL).health())

    def test_false_on_non_200(self):
        with patch("httpx.get", return_value=_response(503, {"ok": False})):
            self.assertFalse(CoreClient(base_url=BASE_URL).health())

    def test_false_never_raises_on_connection_error(self):
        import httpx as httpx_module
        with patch("httpx.get", side_effect=httpx_module.ConnectError("refused")):
            self.assertFalse(CoreClient(base_url=BASE_URL).health())


class StatusTests(unittest.TestCase):
    def test_returns_the_parsed_payload(self):
        payload = {"state": "READY", "paper_count": 201}
        with patch("httpx.get", return_value=_response(200, payload)):
            self.assertEqual(CoreClient(base_url=BASE_URL).status(), payload)

    def test_connection_error_becomes_unavailable(self):
        import httpx as httpx_module
        with patch("httpx.get", side_effect=httpx_module.ConnectError("refused")):
            with self.assertRaises(ResearchAssistantUnavailableError):
                CoreClient(base_url=BASE_URL).status()


class SearchTests(unittest.TestCase):
    def test_forwards_query_mode_top_k_and_options_and_returns_results(self):
        with patch("httpx.post", return_value=_response(200, {"results": [{"paper_id": 1}]})) as fake_post:
            results = CoreClient(base_url=BASE_URL).search(
                "claim", mode="semantic", top_k=15, collection="C1", year_min=2020)
        self.assertEqual(results, [{"paper_id": 1}])
        fake_post.assert_called_once()
        self.assertEqual(fake_post.call_args.args[0], f"{BASE_URL}/search")
        self.assertEqual(fake_post.call_args.kwargs["json"], {
            "query": "claim", "mode": "semantic", "top_k": 15, "collection": "C1", "year_min": 2020})

    def test_invalid_search_request_error_maps_correctly(self):
        with patch("httpx.post", return_value=_response(
                400, {"error": "InvalidSearchRequest", "message": "bad mode"})):
            with self.assertRaises(InvalidSearchRequestError):
                CoreClient(base_url=BASE_URL).search("claim", mode="bogus")

    def test_search_engine_error_maps_correctly(self):
        with patch("httpx.post", return_value=_response(
                500, {"error": "SearchEngineError", "message": "boom"})):
            with self.assertRaises(SearchEngineError):
                CoreClient(base_url=BASE_URL).search("claim")


class BuildEvidencePackTests(unittest.TestCase):
    def test_forwards_query_and_options_and_returns_the_pack_dict(self):
        pack_dict = {"query": "claim", "papers": [], "candidate_count": 0, "duplicate_passages_removed": 0}
        with patch("httpx.post", return_value=_response(200, {"pack": pack_dict})) as fake_post:
            result = CoreClient(base_url=BASE_URL).build_evidence_pack("claim", max_papers=6)
        self.assertEqual(result, pack_dict)
        self.assertEqual(fake_post.call_args.args[0], f"{BASE_URL}/evidence_pack")
        self.assertEqual(fake_post.call_args.kwargs["json"], {"query": "claim", "max_papers": 6})


if __name__ == "__main__":
    unittest.main()
