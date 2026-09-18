"""MCP protocol-level tests for app/external_api/mcp_server.py.

Uses the official in-memory client/server transport (`mcp.shared.memory`) so
these exercise the real MCP request/response cycle - tool listing, input
validation, structured error results - without spawning a subprocess or
touching stdio.
"""

import unittest
from unittest.mock import patch

from mcp.shared.memory import create_connected_server_and_client_session

from app.external_api import mcp_server
from app.external_api.mcp_schemas import FindEvidenceResult, RetrievalSummary


def _fake_result() -> FindEvidenceResult:
    return FindEvidenceResult(
        query="claim", evidence=[], returned_count=0, total_classified_count=0,
        retrieval_summary=RetrievalSummary(candidate_count=0, duplicate_passages_removed=0,
                                           duplicate_papers_merged=0))


class McpServerStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_exposes_the_four_read_only_scientific_tools(self):
        async with create_connected_server_and_client_session(mcp_server.mcp) as session:
            tools = await session.list_tools()
        self.assertEqual([t.name for t in tools.tools], [
            "find_evidence", "find_evidence_for_claim", "get_source", "verify_citation"])

    async def test_find_evidence_delegates_to_adapter_rather_than_reimplementing(self):
        with patch("app.external_api.mcp_server.mcp_adapter.find_evidence",
                   return_value=_fake_result()) as fake:
            async with create_connected_server_and_client_session(mcp_server.mcp) as session:
                result = await session.call_tool("find_evidence", {"query": "claim", "top_k": 3})
        self.assertFalse(result.isError)
        fake.assert_called_once_with("claim", top_k=3, evidence_types=None)

    async def test_evidence_types_argument_is_passed_through(self):
        with patch("app.external_api.mcp_server.mcp_adapter.find_evidence",
                   return_value=_fake_result()) as fake:
            async with create_connected_server_and_client_session(mcp_server.mcp) as session:
                await session.call_tool(
                    "find_evidence", {"query": "claim", "evidence_types": ["DIRECT", "LIMITING"]})
        fake.assert_called_once_with("claim", top_k=10, evidence_types=["DIRECT", "LIMITING"])

    async def test_empty_query_returns_structured_error_not_empty_evidence_list(self):
        async with create_connected_server_and_client_session(mcp_server.mcp) as session:
            result = await session.call_tool("find_evidence", {"query": ""})
        self.assertTrue(result.isError)
        self.assertTrue(result.content)

    async def test_invalid_evidence_type_returns_structured_error(self):
        async with create_connected_server_and_client_session(mcp_server.mcp) as session:
            result = await session.call_tool("find_evidence", {"query": "claim", "evidence_types": ["BOGUS"]})
        self.assertTrue(result.isError)

    async def test_engine_failure_returns_structured_error(self):
        with patch("app.external_api.mcp_server.mcp_adapter.find_evidence",
                   side_effect=RuntimeError("Evidence Engine failed: boom")):
            async with create_connected_server_and_client_session(mcp_server.mcp) as session:
                result = await session.call_tool("find_evidence", {"query": "claim"})
        self.assertTrue(result.isError)
        self.assertIn("boom", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
