"""End-to-end V0.3 Evidence Report: dedup + classification + citation tracing + ranking."""

from pathlib import Path
import tempfile
import unittest

from app.embedding.semantic_index import SemanticIndex
from app.evidence.citation_tracer import CitationStatus
from app.evidence.classifier import EvidenceClass
from app.evidence.ranker import TIER_ORDER, tier_for
from app.evidence.report import EvidenceReportService
from app.search.hybrid_search import UnifiedSearchService
from tests.evidence_report_fixtures import build_in2se3_corpus
from tests.semantic_fixtures import FakeEmbedder

CLAIM = "In2Se3 phase change by strain"


class EvidenceReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.database = root / "literature.db"
        build_in2se3_corpus(self.database)
        index = SemanticIndex(self.database, root / "semantic", FakeEmbedder())
        index.update()
        search = UnifiedSearchService(self.database, semantic_index=index)
        self.report = EvidenceReportService(self.database, search).build_report(
            CLAIM, max_papers=10, max_passages_per_paper=1, candidate_pool=30)

    def tearDown(self):
        self._tmp.cleanup()

    def test_duplicate_primary_records_merge_into_one_identity(self):
        primary_items = [item for item in self.report.items if item.doi == "10.1000/primary"
                         and item.evidence_class == EvidenceClass.DIRECT]
        self.assertGreaterEqual(len(primary_items), 1)
        identity = primary_items[0].paper_identity
        self.assertEqual(set(identity.zotero_item_keys), {"PRIM1", "PRIM2"})
        self.assertEqual(len(identity.paper_ids), 2)
        self.assertGreaterEqual(self.report.duplicate_papers_merged, 1)

    def test_direct_evidence_found_for_strain_induced_transition(self):
        direct = [item for item in self.report.items if item.evidence_class == EvidenceClass.DIRECT]
        self.assertTrue(any(item.doi == "10.1000/primary" for item in direct))

    def test_secondary_summary_detected_and_traced_to_primary(self):
        secondary = [item for item in self.report.items if item.evidence_class == EvidenceClass.SECONDARY]
        self.assertEqual(len(secondary), 1)
        traces = secondary[0].citation_traces
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].status, CitationStatus.RESOLVED_LOCAL)
        self.assertEqual(set(traces[0].resolved_identity.zotero_item_keys), {"PRIM1", "PRIM2"})

    def test_primary_paper_is_independently_re_retrieved_and_classified(self):
        traced = [item for item in self.report.items if item.provenance == "citation_traced"]
        self.assertTrue(traced, "citation tracing should have re-searched the resolved primary paper")
        secondary_id = [item for item in self.report.items
                        if item.evidence_class == EvidenceClass.SECONDARY][0].evidence_id
        self.assertTrue(all(item.parent_evidence_id == secondary_id for item in traced))
        self.assertTrue(any(item.evidence_class == EvidenceClass.DIRECT for item in traced))

    def test_contextual_current_induced_transition_is_not_direct(self):
        contextual = [item for item in self.report.items if item.doi == "10.1000/contextual"]
        self.assertEqual(len(contextual), 1)
        self.assertEqual(contextual[0].evidence_class, EvidenceClass.CONTEXTUAL)

    def test_limiting_negligible_strain_effect_is_detected_and_kept(self):
        limiting = [item for item in self.report.items if item.doi == "10.1000/limiting"]
        self.assertEqual(len(limiting), 1)
        self.assertEqual(limiting[0].evidence_class, EvidenceClass.LIMITING)

    def test_ranking_prefers_direct_and_limiting_over_secondary_and_contextual(self):
        tiers = [TIER_ORDER.index(tier_for(item)) for item in self.report.items]
        self.assertEqual(tiers, sorted(tiers), "report.items must already be globally ranked by tier")
        classes = {item.evidence_class for item in self.report.items}
        self.assertTrue({EvidenceClass.DIRECT, EvidenceClass.SECONDARY, EvidenceClass.CONTEXTUAL,
                         EvidenceClass.LIMITING}.issubset(classes))

    def test_source_text_and_rationale_stay_in_separate_fields(self):
        for item in self.report.items:
            self.assertNotEqual(item.original_text, item.rationale)
            self.assertNotIn(item.rationale, item.original_text)

    def test_markdown_report_has_expected_sections_and_unmodified_passages(self):
        markdown = self.report.to_markdown()
        for heading in ("## Strong direct evidence", "## Secondary evidence", "## Limiting evidence",
                       "## Contextual evidence", "## Unresolved citations"):
            self.assertIn(heading, markdown)
        direct_item = next(item for item in self.report.items if item.evidence_class == EvidenceClass.DIRECT
                           and item.provenance == "original_retrieval")
        self.assertIn(direct_item.original_text, markdown)


if __name__ == "__main__":
    unittest.main()
