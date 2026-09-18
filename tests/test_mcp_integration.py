"""End-to-end test: find_evidence run against a real EvidenceReportService,
wired to the same fixture corpus as tests/test_evidence_report.py.

Only the *construction* of the EvidenceReportService instance is substituted
(so the test uses an isolated temp database instead of the user's real
literature.db); build_report itself - retrieval, passage/paper-identity
deduplication, classification, citation tracing and ranking - runs completely
unmodified. This is what proves the MCP layer calls the real V0.3.2 pipeline
instead of reimplementing it (Task 10.3 of the V0.4-A brief).

A separate, manual check against the user's actual Zotero-backed
data/literature.db is described in the V0.4-A completion report.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.embedding.semantic_index import SemanticIndex
from app.evidence.report import EvidenceReportService
from app.external_api.mcp_adapter import find_evidence
from app.search.hybrid_search import UnifiedSearchService
from tests.evidence_report_fixtures import build_in2se3_corpus
from tests.semantic_fixtures import FakeEmbedder

CLAIM = "In2Se3 phase change by strain"


class McpAdapterRealPipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.database = root / "literature.db"
        build_in2se3_corpus(self.database)
        index = SemanticIndex(self.database, root / "semantic", FakeEmbedder())
        index.update()
        search = UnifiedSearchService(self.database, semantic_index=index)
        self._service = EvidenceReportService(self.database, search)
        self._patcher = patch("app.external_api.mcp_adapter.EvidenceReportService",
                              return_value=self._service)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(self._tmp.cleanup)

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


if __name__ == "__main__":
    unittest.main()
