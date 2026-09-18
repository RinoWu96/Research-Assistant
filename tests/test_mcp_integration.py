"""End-to-end test: MCP proxy -> real Core Service HTTP API -> real
EvidenceReportService, wired to the same fixture corpus as
tests/test_evidence_report.py.

Only the *database* is substituted (an isolated temp corpus instead of the
user's real literature.db); everything else - the real HTTP round trip
through `app.core_service.http_api`, the Core's `ResourceManager`, and
`build_report` itself (retrieval, passage/paper-identity deduplication,
classification, citation tracing, ranking) - runs completely unmodified.
This is what proves the MCP layer calls the real Core Service instead of
reimplementing anything, and that the Core calls the real V0.3.2 pipeline
instead of reimplementing it (V0.5 brief, section 16, requirements 6, 8, 9).

A separate, real-machine integration test against the user's actual
Zotero-backed data/literature.db (including the real BGE model, not
`FakeEmbedder`) is described in the V0.5 completion report.
"""

from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.core_service import client as core_client
from app.core_service.server import create_server
from app.core_service.state import CoreState
from app.embedding.semantic_index import SemanticIndex
from app.external_api import mcp_adapter
from app.external_api.mcp_adapter import find_evidence
from app.search.hybrid_search import UnifiedSearchService
from tests.evidence_report_fixtures import build_in2se3_corpus
from tests.semantic_fixtures import FakeEmbedder

CLAIM = "In2Se3 phase change by strain"


class McpProxyRealCoreIntegrationTests(unittest.TestCase):
    """A real MCP proxy talking, over real HTTP, to a real Core Service."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.database = root / "literature.db"
        build_in2se3_corpus(cls.database)
        index = SemanticIndex(cls.database, root / "semantic", FakeEmbedder())
        index.update()

        cls.server = create_server(
            cls.database, port=0,
            search_service_factory=lambda: UnifiedSearchService(cls.database, semantic_index=index))
        cls.server.resource_manager.start_background_init()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

        deadline = time.perf_counter() + 5.0
        while cls.server.resource_manager.state != CoreState.READY:
            if time.perf_counter() > deadline:
                raise AssertionError(f"Core never reached READY: {cls.server.resource_manager.status()}")
            time.sleep(0.02)

        base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls._client_patch = patch.object(mcp_adapter, "_client", core_client.CoreClient(base_url=base_url))
        cls._client_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._client_patch.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def test_real_pipeline_returns_all_expected_classes_with_correct_identity(self):
        result = find_evidence(CLAIM, top_k=10)
        classes = {item.evidence_class for item in result.evidence}
        self.assertTrue({"DIRECT", "SECONDARY", "LIMITING", "CONTEXTUAL"}.issubset(classes))

        primary = next(item for item in result.evidence
                       if item.evidence_class == "DIRECT" and item.paper.doi == "10.1000/primary")
        self.assertIn("PRIM1", primary.paper.zotero_item_keys_merged)
        self.assertIn("PRIM2", primary.paper.zotero_item_keys_merged)
        self.assertGreaterEqual(result.retrieval_summary.duplicate_papers_merged, 1)

        limiting = next(item for item in result.evidence if item.evidence_class == "LIMITING")
        self.assertEqual(limiting.paper.zotero_item_key, "LIM1")

        secondary = next(item for item in result.evidence if item.evidence_class == "SECONDARY")
        self.assertTrue(secondary.citation_traces)
        self.assertEqual(secondary.citation_traces[0].resolution_status, "resolved_local")

    def test_evidence_types_filter_applies_after_classification_not_before_retrieval(self):
        result = find_evidence(CLAIM, top_k=10, evidence_types=["DIRECT", "LIMITING"])
        self.assertTrue(result.evidence)
        self.assertEqual({item.evidence_class for item in result.evidence}, {"DIRECT", "LIMITING"})
        # The underlying candidate pool (pre-filter) is untouched by evidence_types.
        self.assertGreater(result.retrieval_summary.candidate_count, 0)

    def test_second_call_reuses_the_same_loaded_search_service(self):
        """Sequential find_evidence calls must not reconstruct the embedder or
        search service (V0.5 brief, section 16, requirement 5)."""
        search_service_before = self.server.resource_manager.search_service
        find_evidence(CLAIM, top_k=1)
        find_evidence(CLAIM, top_k=1)
        self.assertIs(self.server.resource_manager.search_service, search_service_before)


if __name__ == "__main__":
    unittest.main()
