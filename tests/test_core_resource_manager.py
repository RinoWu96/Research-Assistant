"""Lifecycle tests for app/core_service/resource_manager.py.

Cover the guarantees the Core Service depends on: `state` starts at
STARTING and never blocks on initialization, a second
`start_background_init()` call is a no-op (so concurrent callers can never
trigger two heavy-resource loads), and each readiness flag
(database/semantic index/embedding model) is reported independently so
`/status` stays useful even on partial failure (V0.5 brief, sections 3-4).
"""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.search.database import Database
from app.core_service.resource_manager import SEMANTIC_INDEX_FILES, ResourceManager
from app.core_service.state import CoreState
from tests.evidence_report_fixtures import build_in2se3_corpus


class _FailingEmbedder:
    @property
    def dimension(self):
        raise RuntimeError("simulated model load failure")


class _FailingSearchService:
    def __init__(self, *args, **kwargs):
        self.semantic = type("S", (), {"index": type("I", (), {"embedder": _FailingEmbedder()})()})()


def _make_database_with_semantic_files(tmp_dir: Path) -> Path:
    database = tmp_dir / "literature.db"
    build_in2se3_corpus(database)
    semantic_dir = tmp_dir / "semantic"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    for name in SEMANTIC_INDEX_FILES:
        (semantic_dir / name).write_bytes(b"")
    return database


class ResourceManagerStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_state_is_starting_before_init_begins(self):
        database = _make_database_with_semantic_files(self.root)
        manager = ResourceManager(database)
        self.assertEqual(manager.state, CoreState.STARTING)
        self.assertIsNone(manager.search_service)

    def test_second_start_is_a_no_op_and_reports_ready(self):
        database = _make_database_with_semantic_files(self.root)
        with patch("app.core_service.resource_manager.UnifiedSearchService") as ctor:
            ctor.return_value.semantic.index.embedder.dimension = 3
            manager = ResourceManager(database)
            first = manager.start_background_init()
            second = manager.start_background_init()
            first.join(timeout=5)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(ctor.call_count, 1)
        self.assertEqual(manager.state, CoreState.READY)

    def test_reaches_ready_and_reports_paper_count(self):
        database = _make_database_with_semantic_files(self.root)
        with patch("app.core_service.resource_manager.UnifiedSearchService") as ctor:
            ctor.return_value.semantic.index.embedder.dimension = 3
            manager = ResourceManager(database)
            manager.start_background_init().join(timeout=5)
        status = manager.status()
        self.assertEqual(status.state, CoreState.READY)
        self.assertTrue(status.database_ready)
        self.assertTrue(status.semantic_index_ready)
        self.assertTrue(status.embedding_model_ready)
        self.assertGreater(status.paper_count, 0)
        self.assertIsNotNone(manager.search_service)
        self.assertIsNone(status.error)

    def test_paper_count_follows_index_updates_after_startup(self):
        database = _make_database_with_semantic_files(self.root)
        with patch("app.core_service.resource_manager.UnifiedSearchService") as ctor:
            ctor.return_value.semantic.index.embedder.dimension = 3
            manager = ResourceManager(database)
            manager.start_background_init().join(timeout=5)
        before = manager.status().paper_count
        with Database(database) as db:
            with db.transaction():
                db.connection.execute("DELETE FROM papers WHERE id=(SELECT MIN(id) FROM papers)")
        self.assertEqual(manager.status().paper_count, before - 1)

    def test_missing_semantic_index_files_is_an_error_and_never_touches_the_embedder(self):
        database = self.root / "literature.db"
        build_in2se3_corpus(database)  # no semantic/ directory created
        with patch("app.core_service.resource_manager.UnifiedSearchService") as ctor:
            manager = ResourceManager(database)
            manager.start_background_init().join(timeout=5)
        status = manager.status()
        self.assertEqual(status.state, CoreState.ERROR)
        self.assertTrue(status.database_ready)
        self.assertFalse(status.semantic_index_ready)
        self.assertFalse(status.embedding_model_ready)
        self.assertIsNotNone(status.error)
        self.assertIsNone(manager.search_service)
        ctor.assert_not_called()

    def test_unavailable_database_is_an_error_before_anything_else_is_checked(self):
        # A nonexistent path is not enough - Database() creates a fresh, empty,
        # valid database there. Force a real failure with a corrupt file instead.
        corrupt_database = self.root / "corrupt.db"
        corrupt_database.write_bytes(b"not a sqlite file at all, just garbage bytes")
        manager = ResourceManager(corrupt_database)
        with patch("app.core_service.resource_manager.UnifiedSearchService") as ctor:
            manager.start_background_init().join(timeout=5)
        status = manager.status()
        self.assertEqual(status.state, CoreState.ERROR)
        self.assertFalse(status.database_ready)
        ctor.assert_not_called()

    def test_embedder_failure_is_an_error_with_database_and_index_already_marked_ready(self):
        database = _make_database_with_semantic_files(self.root)
        with patch("app.core_service.resource_manager.UnifiedSearchService", _FailingSearchService):
            manager = ResourceManager(database)
            manager.start_background_init().join(timeout=5)
        status = manager.status()
        self.assertEqual(status.state, CoreState.ERROR)
        self.assertTrue(status.database_ready)
        self.assertTrue(status.semantic_index_ready)
        self.assertFalse(status.embedding_model_ready)
        self.assertIsNotNone(status.error)
        self.assertIsNone(manager.search_service)

    def test_only_one_of_many_concurrent_start_calls_spawns_the_init_thread(self):
        """The real race section 4 guards against: many threads calling
        `start_background_init()` at the same instant must still result in
        exactly one thread doing the (expensive) load."""
        database = _make_database_with_semantic_files(self.root)
        with patch("app.core_service.resource_manager.UnifiedSearchService") as ctor:
            ctor.return_value.semantic.index.embedder.dimension = 3
            manager = ResourceManager(database)
            start_barrier = threading.Barrier(5)
            results: list = []

            def call_start():
                start_barrier.wait(timeout=5)
                results.append(manager.start_background_init())

            threads = [threading.Thread(target=call_start) for _ in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            spawned = [thread for thread in results if thread is not None]
            self.assertEqual(len(spawned), 1)
            spawned[0].join(timeout=5)

        self.assertEqual(ctor.call_count, 1)
        self.assertEqual(manager.state, CoreState.READY)


if __name__ == "__main__":
    unittest.main()
