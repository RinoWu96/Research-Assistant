"""V0.7 source inspection, claim search, verification, and handle lifecycle."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core_service.report_cache import (
    EvidenceNotFoundError, ReportCache, ReportExpiredError, ReportNotFoundError,
)
from app.core_service.scientific_gateway import (
    find_evidence_for_claim, get_source, verify_citation,
)
from app.embedding.semantic_index import SemanticIndex
from app.evidence.classifier import EvidenceClass
from app.evidence.models import EvidenceReport
from app.evidence.report import EvidenceReportService
from app.search.hybrid_search import UnifiedSearchService
from tests.evidence_report_fixtures import build_in2se3_corpus
from tests.semantic_fixtures import FakeEmbedder
from app.external_api import mcp_adapter

CLAIM = "Mechanical strain induces beta-prime to alpha phase transition in In2Se3."


class _Clock:
    value = 0.0
    def __call__(self): return self.value


class ReportCacheTests(unittest.TestCase):
    def test_unique_bounded_handles_and_clear_missing_or_expired_errors(self):
        clock = _Clock()
        report = EvidenceReport("q", (), 0, 0, 0)
        cache = ReportCache(ttl_seconds=10, max_reports=1, clock=clock)
        first = cache.store(report)
        second = cache.store(report)
        self.assertNotEqual(first, second)
        self.assertRegex(second, r"^evr_")
        with self.assertRaises(ReportExpiredError):
            cache.get(first)  # capacity eviction is never allowed to resolve elsewhere
        clock.value = 11
        with self.assertRaises(ReportExpiredError):
            cache.get(second)
        with self.assertRaises(ReportNotFoundError):
            cache.get("evr_never_existed")


class ThinMCPProxyTests(unittest.TestCase):
    def test_all_v07_tools_delegate_only_to_core_client(self):
        sentinel = object()
        with patch.object(mcp_adapter._client, "get_source", return_value=sentinel) as call:
            self.assertIs(mcp_adapter.get_source("r", "E1", 2), sentinel)
            call.assert_called_once_with("r", "E1", context_pages=2)
        with patch.object(mcp_adapter._client, "find_evidence_for_claim", return_value=sentinel) as call:
            self.assertIs(mcp_adapter.find_evidence_for_claim("claim", 4, ["DIRECT"]), sentinel)
            call.assert_called_once_with("claim", top_k=4, evidence_types=["DIRECT"])
        with patch.object(mcp_adapter._client, "verify_citation", return_value=sentinel) as call:
            self.assertIs(mcp_adapter.verify_citation("claim", "r", "E1"), sentinel)
            call.assert_called_once_with("claim", "r", "E1")


class ScientificToolsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.database = root / "literature.db"
        build_in2se3_corpus(self.database)
        index = SemanticIndex(self.database, root / "semantic", FakeEmbedder())
        index.update()
        self.search = UnifiedSearchService(self.database, semantic_index=index)
        self.report = EvidenceReportService(self.database, self.search).build_report(
            CLAIM, max_papers=10, max_passages_per_paper=1, candidate_pool=30)
        self.cache = ReportCache()
        self.report_id = self.cache.store(self.report)

    def tearDown(self):
        self._tmp.cleanup()

    def _item(self, cls, *, doi=None, provenance=None):
        return next(item for item in self.report.items
                    if item.evidence_class == cls
                    and (doi is None or item.doi == doi)
                    and (provenance is None or item.provenance == provenance))

    def test_get_source_preserves_passage_metadata_context_and_boundaries(self):
        secondary = self._item(EvidenceClass.SECONDARY)
        database_before = self.database.read_bytes()
        source = get_source(self.cache, self.database, self.report_id, secondary.evidence_id, 1)
        self.assertEqual(self.database.read_bytes(), database_before)
        self.assertEqual(source.paper.zotero_item_key, "SEC1")
        self.assertEqual(source.source.evidence_passage, secondary.original_text)
        self.assertEqual([p.pdf_page for p in source.source.pages], [1, 2])
        self.assertEqual(source.source.start_page, 1)
        self.assertEqual(source.source.end_page, 2)
        self.assertEqual(source.source.status, "COMPLETE")  # lower boundary is cleanly clipped at page 1
        self.assertIn("References", source.source.text)

        exact = get_source(self.cache, self.database, self.report_id, secondary.evidence_id, 0)
        self.assertEqual([p.pdf_page for p in exact.source.pages], [1])
        self.assertEqual(exact.source.status, "COMPLETE")

    def test_get_source_rejects_bad_handles_and_has_no_path_input(self):
        item = self.report.items[0]
        with self.assertRaises(EvidenceNotFoundError):
            get_source(self.cache, self.database, self.report_id, "E999", 1)
        with self.assertRaises(ReportNotFoundError):
            get_source(self.cache, self.database, "C:\\anything", item.evidence_id, 1)
        with self.assertRaises(ValueError):
            get_source(self.cache, self.database, self.report_id, item.evidence_id, 3)

    def test_citation_traced_source_keeps_primary_provenance(self):
        traced = next(item for item in self.report.items if item.provenance == "citation_traced")
        source = get_source(self.cache, self.database, self.report_id, traced.evidence_id, 0)
        self.assertEqual(source.provenance.origin, "citation_tracing")
        self.assertEqual(source.provenance.parent_evidence_id, traced.parent_evidence_id)
        self.assertEqual(source.source.attachment_key, traced.attachment_key)

    def test_claim_search_groups_all_existing_evidence_classes_and_returns_usable_handle(self):
        database_before = self.database.read_bytes()
        result = find_evidence_for_claim(
            self.search, self.cache, CLAIM, top_k=20, database_path=self.database)
        self.assertEqual(self.database.read_bytes(), database_before)
        self.assertTrue(result.report_id)
        self.assertTrue(result.direct_support)
        self.assertTrue(result.secondary)
        self.assertTrue(result.limiting)
        self.assertTrue(result.contextual)
        chosen = result.direct_support[0]
        source = get_source(self.cache, self.database, result.report_id, chosen.evidence_id, 0)
        self.assertEqual(source.evidence_id, chosen.evidence_id)
        self.assertTrue(any(
            trace.resolution_status == "resolved_local"
            for item in result.secondary for trace in item.citation_traces))
        self.assertTrue(any(item.paper.doi == "10.1000/primary"
                            for item in result.direct_support))

    def test_verify_direct_secondary_contextual_limiting_and_ambiguous(self):
        cases = [
            (self._item(EvidenceClass.DIRECT, doi="10.1000/primary"), "SUPPORTED"),
            (self._item(EvidenceClass.SECONDARY), "SECONDARY_SUPPORT_ONLY"),
            (self._item(EvidenceClass.CONTEXTUAL, doi="10.1000/contextual"), "NOT_SUPPORTED"),
            (self._item(EvidenceClass.LIMITING), "LIMITS_OR_CONTRADICTS"),
        ]
        for item, expected in cases:
            with self.subTest(expected=expected):
                result = verify_citation(self.cache, CLAIM, self.report_id, item.evidence_id)
                self.assertEqual(result.status, expected)
                self.assertEqual(result.evidence_id, item.evidence_id)
                self.assertEqual(result.reason, result.reason)  # non-generated deterministic field

        base = self.report.items[0]
        ambiguous = replace(base, evidence_id="EAMB", title="Unrelated observations",
                            original_text="Measurements were collected for this sample.")
        ambiguous_report = replace(self.report, items=(ambiguous,))
        ambiguous_id = self.cache.store(ambiguous_report)
        result = verify_citation(self.cache, CLAIM, ambiguous_id, "EAMB")
        self.assertEqual(result.status, "INSUFFICIENT_CONTEXT")
        self.assertFalse(result.matches.material)

    def test_wrong_material_is_not_supported_and_missing_handle_fails(self):
        base = self.report.items[0]
        wrong = replace(base, evidence_id="EWRONG", title="Graphene phase transition",
                        original_text="We show that mechanical strain induces a phase transition in graphene.")
        wrong_id = self.cache.store(replace(self.report, items=(wrong,)))
        result = verify_citation(self.cache, CLAIM, wrong_id, "EWRONG")
        self.assertEqual(result.status, "NOT_SUPPORTED")
        self.assertFalse(result.matches.material)
        with self.assertRaises(EvidenceNotFoundError):
            verify_citation(self.cache, CLAIM, wrong_id, "missing")


if __name__ == "__main__":
    unittest.main()
