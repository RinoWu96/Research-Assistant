"""Persistent FAISS metadata and incremental indexing tests."""

from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from app.embedding.semantic_index import SemanticIndex, SemanticRebuildRequired
from tests.semantic_fixtures import FakeEmbedder, build_corpus


class SemanticIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "literature.db"
        self.semantic = self.root / "semantic"
        build_corpus(self.database)
        self.embedder = FakeEmbedder()
        self.index = SemanticIndex(self.database, self.semantic, self.embedder)

    def tearDown(self):
        self.temp.cleanup()

    def test_persistent_save_load_metadata_and_unchanged_skip(self):
        first = self.index.update()
        self.assertEqual((first.new_papers, first.failed_papers), (3, 0))
        self.assertTrue(self.index.index_path.is_file())
        self.assertTrue(self.index.cache_path.is_file())
        metadata = self.index.validate_for_search()
        self.assertEqual(metadata["embedding_model"], self.embedder.model_name)
        self.assertEqual(metadata["embedding_dimension"], 4)
        self.assertEqual(metadata["total_papers"], 3)
        calls = self.embedder.embedded_documents
        second = self.index.update()
        self.assertEqual((second.skipped_papers, second.new_chunks, second.updated_chunks), (3, 0, 0))
        self.assertEqual(self.embedder.embedded_documents, calls)
        self.assertFalse(second.faiss_rebuilt)

    def test_metadata_only_content_update_and_removal(self):
        self.index.update()
        calls = self.embedder.embedded_documents
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE papers SET title='Renamed paper' WHERE zotero_key='P2'")
        metadata = self.index.update()
        self.assertEqual(metadata.metadata_only_papers, 1)
        self.assertEqual(self.embedder.embedded_documents, calls)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE attachments SET content_hash='changed' WHERE zotero_key='A2'")
            connection.execute("UPDATE pages SET text='new flexural lattice deformation source' WHERE attachment_id=(SELECT id FROM attachments WHERE zotero_key='A2')")
        changed = self.index.update()
        self.assertEqual(changed.updated_papers, 1)
        self.assertGreater(self.embedder.embedded_documents, calls)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM papers WHERE zotero_key='P3'")
        removed = self.index.update()
        self.assertEqual(removed.removed_papers, 1)

    def test_incompatible_configuration_requires_explicit_rebuild(self):
        self.index.update()
        incompatible = FakeEmbedder()
        incompatible.model_name = "different/model"
        other = SemanticIndex(self.database, self.semantic, incompatible)
        with self.assertRaises(SemanticRebuildRequired):
            other.update()
        result = other.update(rebuild=True)
        self.assertEqual(result.new_papers, 3)
        self.assertEqual(other.read_metadata()["embedding_model"], "different/model")
