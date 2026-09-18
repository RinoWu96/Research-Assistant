"""RRF ranking, paper aggregation and semantic-failure fallback tests."""

from pathlib import Path
import tempfile
import unittest

from app.embedding.semantic_index import SemanticIndex
from app.search.hybrid_search import UnifiedSearchService
from tests.semantic_fixtures import FakeEmbedder, build_corpus


class HybridSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "literature.db"
        build_corpus(self.database)
        self.index = SemanticIndex(self.database, self.root / "semantic", FakeEmbedder())
        self.index.update()
        self.service = UnifiedSearchService(self.database, semantic_index=self.index)

    def tearDown(self):
        self.temp.cleanup()

    def test_rrf_exact_evidence_and_paper_aggregation(self):
        results = self.service.search("curvature induced strain", top_k=10)
        exact = next(result for result in results if result.zotero_key == "P1")
        self.assertIsNotNone(exact.keyword_rank)
        self.assertIsNotNone(exact.semantic_rank)
        expected = 1 / (60 + exact.keyword_rank) + 1 / (60 + exact.semantic_rank)
        self.assertAlmostEqual(exact.hybrid_score, expected)
        papers = self.service.search("curvature induced strain", top_k=10, result_level="paper")
        self.assertEqual(len({paper.paper_id for paper in papers}), len(papers))
        self.assertTrue(all(paper.best_chunk for paper in papers))
        self.assertTrue(any(paper.keyword_evidence and paper.semantic_evidence for paper in papers))

    def test_keyword_fallback_when_semantic_missing(self):
        missing = UnifiedSearchService(self.database, self.root / "missing")
        results = missing.search("curvature induced strain", mode="hybrid")
        self.assertTrue(results)
        self.assertTrue(all(result.keyword_rank is not None and result.semantic_rank is None for result in results))
        keyword = missing.search("curvature induced strain", mode="keyword")
        self.assertEqual([(r.paper_id, r.page_start) for r in results],
                         [(r.paper_id, r.page_start) for r in keyword])

    def test_keyword_fallback_when_faiss_is_corrupted(self):
        self.index.index_path.write_bytes(b"not a faiss index")
        results = self.service.search("curvature induced strain", mode="hybrid")
        self.assertTrue(results)
        self.assertTrue(all(result.semantic_rank is None for result in results))

    def test_collection_filter_and_cap(self):
        results = self.service.search("curvature induced strain", collection="IN2", top_k=10,
                                      max_chunks_per_paper=1)
        self.assertTrue(results)
        self.assertEqual({result.zotero_key for result in results}, {"P2", "P3"})
        self.assertEqual(len({result.paper_id for result in results}), len(results))
