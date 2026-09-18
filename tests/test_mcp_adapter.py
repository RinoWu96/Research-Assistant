"""Unit tests for the MCP proxy boundary (app/external_api/mcp_adapter.py).

V0.5: this module is a thin HTTP proxy to the Research Assistant Core
Service - it must never import or construct the Evidence Engine, the
semantic index, or the embedder (section 16, requirement 7). These tests
mock `app.core_service.client.CoreClient.find_evidence` and check only
error translation and forwarding; the actual evidence-report logic is
tested against a real `EvidenceReportService` in
tests/test_core_evidence_gateway.py and tests/test_core_evidence_gateway_integration.py.
"""

import unittest
from unittest.mock import patch

from app.core_service import client as core_client
from app.external_api import mcp_adapter
from app.external_api.mcp_adapter import (
    DatabaseUnavailableError,
    EvidenceEngineError,
    InvalidEvidenceTypeError,
    InvalidQueryError,
    ResearchAssistantLoadingError,
    ResearchAssistantUnavailableError,
    find_evidence,
)
from app.external_api.mcp_schemas import FindEvidenceResult, RetrievalSummary


def _fake_result() -> FindEvidenceResult:
    return FindEvidenceResult(
        query="claim", evidence=[], returned_count=0, total_classified_count=0,
        retrieval_summary=RetrievalSummary(candidate_count=0, duplicate_passages_removed=0,
                                           duplicate_papers_merged=0))


class ProxyNeverImportsTheHeavyEngineTests(unittest.TestCase):
    """The MCP process must stay lightweight: no Evidence Engine, semantic
    index, or embedder import anywhere in its module graph (section 6/15)."""

    def test_mcp_adapter_module_has_no_heavy_engine_names(self):
        heavy_names = {"EvidenceReportService", "UnifiedSearchService", "LocalEmbedder", "SemanticIndex"}
        self.assertFalse(heavy_names & set(dir(mcp_adapter)))


class FindEvidenceForwardingTests(unittest.TestCase):
    @patch.object(mcp_adapter._client, "find_evidence")
    def test_forwards_query_top_k_and_evidence_types_unchanged(self, fake_find):
        fake_find.return_value = _fake_result()
        find_evidence("claim", top_k=3, evidence_types=["DIRECT"])
        fake_find.assert_called_once_with("claim", top_k=3, evidence_types=["DIRECT"])

    @patch.object(mcp_adapter._client, "find_evidence")
    def test_returns_the_client_result_unmodified(self, fake_find):
        fake_find.return_value = _fake_result()
        result = find_evidence("claim")
        self.assertIs(result, fake_find.return_value)


class ErrorTranslationTests(unittest.TestCase):
    CASES = [
        (core_client.InvalidQueryError, InvalidQueryError),
        (core_client.InvalidEvidenceTypeError, InvalidEvidenceTypeError),
        (core_client.DatabaseUnavailableError, DatabaseUnavailableError),
        (core_client.EvidenceEngineError, EvidenceEngineError),
        (core_client.ResearchAssistantLoadingError, ResearchAssistantLoadingError),
        (core_client.ResearchAssistantUnavailableError, ResearchAssistantUnavailableError),
    ]

    def test_each_core_client_error_maps_to_its_mcp_error(self):
        for client_error_cls, mcp_error_cls in self.CASES:
            with self.subTest(client_error_cls=client_error_cls):
                with patch.object(mcp_adapter._client, "find_evidence",
                                   side_effect=client_error_cls("boom")):
                    with self.assertRaises(mcp_error_cls) as ctx:
                        find_evidence("claim")
                self.assertIn("boom", str(ctx.exception))

    def test_core_unavailable_is_a_distinct_clear_error_not_a_silent_fallback(self):
        """Section 15: if the Core is not running, the proxy must raise a clear
        structured error and never fall back to loading the Evidence Engine."""
        with patch.object(mcp_adapter._client, "find_evidence",
                           side_effect=core_client.ResearchAssistantUnavailableError("Core not running")):
            with self.assertRaises(ResearchAssistantUnavailableError):
                find_evidence("claim")


if __name__ == "__main__":
    unittest.main()
