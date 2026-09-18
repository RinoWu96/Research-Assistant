"""Semantic filtering, diversity and original-source tests."""

from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from app.embedding.semantic_index import SemanticIndex
from app.search.semantic_search import SemanticSearchService
from tests.semantic_fixtures import FakeEmbedder, build_corpus


class SemanticSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "literature.db"
        build_corpus(self.database)
        self.index = SemanticIndex(self.database, self.root / "semantic", FakeEmbedder())
        self.index.update()
        self.service = SemanticSearchService(self.database, semantic_index=self.index)

    def tearDown(self):
        self.temp.cleanup()

    def test_collection_year_journal_filters_and_source_text(self):
        all_results = self.service.search("curvature induced strain", top_k=10)
        filtered = self.service.search("curvature induced strain", top_k=10, collection="IN2")
        self.assertIn("P1", {result.zotero_key for result in all_results})
        self.assertTrue(filtered)
        self.assertEqual({result.zotero_key for result in filtered}, {"P2", "P3"})
        with closing(sqlite3.connect(self.database)) as connection:
            sources = [row[0] for row in connection.execute("SELECT text FROM pages")]
        self.assertTrue(all(any(result.text in source for source in sources) for result in filtered))
        self.assertEqual({r.zotero_key for r in self.service.search("strain", year_min=2024)}, {"P3"})
        self.assertEqual({r.zotero_key for r in self.service.search("strain", journal="Physics")}, {"P1"})

    def test_max_chunks_per_paper_and_deduplication(self):
        results = self.service.search("bending deformation", top_k=20, max_chunks_per_paper=2)
        counts = {}
        for result in results:
            counts[result.paper_id] = counts.get(result.paper_id, 0) + 1
        self.assertTrue(all(count <= 2 for count in counts.values()))
        self.assertEqual(len({result.chunk_id for result in results}), len(results))
        self.assertGreaterEqual(len({result.paper_id for result in results[:3]}), 2)
