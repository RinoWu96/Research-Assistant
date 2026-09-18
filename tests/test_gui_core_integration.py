"""End-to-end test: a real desktop GUI window, talking over real HTTP to a
real Core Service HTTP server (fixture-backed, in-process `FakeEmbedder` -
not the real BGE model), automated the same way section 19 of the V0.6
brief validates manually on a real machine: the GUI's own
`CoreProcessManager`/`CoreMonitor` detect the Core (via a patched
discovery lookup pointed at the fixture Core instead of the real discovery
file), the GUI reaches READY, and a real search/Evidence Report round
trip returns real, correctly classified results with intact provenance.

A separate, real-machine validation against the actual local
data/literature.db (including the real BGE model) is described in the
V0.6 completion report.
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from app.core_service.server import create_server
from app.core_service.state import CoreState
from app.embedding.semantic_index import SemanticIndex
from app.search.hybrid_search import UnifiedSearchService
from app.ui.main_window import MainWindow
from tests.evidence_report_fixtures import build_in2se3_corpus
from tests.semantic_fixtures import FakeEmbedder

CLAIM = "In2Se3 phase change by strain"


class GuiCoreIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
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
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def setUp(self):
        self._patch = patch("app.ui.core_process_manager.discovery.discover_running_core",
                            return_value=self.base_url)
        self._patch.start()
        # A real GUI would point at the same literature.db the Core reads;
        # Zotero auto-connect is unrelated to this test and stays off.
        self.window = MainWindow(self.database, auto_connect=False)
        self.window.show()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        self.app.processEvents()
        self._patch.stop()

    def _pump(self, seconds: float) -> None:
        """Process Qt events without QTest.qWait().

        QTest.qWait()'s own internal polling starves a background thread
        that is waiting on real socket I/O (observed here: a monitor/search
        thread's real httpx call to the real Core never completes while
        qWait is spinning, even though the same call completes in well
        under a second via plain time.sleep() + processEvents()). This is
        specific to combining a real background network call with a fully
        built, shown MainWindow under the offscreen platform - not a
        production concern (a real Qt event loop does not busy-poll like
        qWait does), but real here, so this test waits the way a real
        event loop actually behaves instead.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.05)
            self.app.processEvents()

    def _wait_ready(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while self.window.core_state != "READY" and time.monotonic() < deadline:
            self._pump(0.1)
        self.assertEqual(self.window.core_state, "READY",
                         f"GUI never reached READY (last payload: {self.window.core_status_payload})")

    def _wait_not_busy(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while self.window.busy and time.monotonic() < deadline:
            self._pump(0.1)
        self.assertFalse(self.window.busy, "Qt worker did not finish")

    def test_gui_detects_and_reuses_the_existing_core_without_spawning_a_second_one(self):
        self._wait_ready()
        self.assertEqual(self.window.core_manager.base_url, self.base_url)
        # `launched_by_us` is only ever set True on the subprocess.Popen path
        # (see CoreProcessManager.ensure_core_running) - False here proves
        # that path was never taken, i.e. no second Core was spawned.
        self.assertFalse(self.window.core_manager.launched_by_us)

    def test_gui_evidence_report_matches_direct_core_call(self):
        self._wait_ready()
        self.assertIsNotNone(self.window.search_service)
        self.window.app_mode.setCurrentIndex(self.window.app_mode.findData("evidence_report"))
        self.window.query.setText(CLAIM)
        self.window.start_search()
        self._wait_not_busy()

        gui_result = self.window.evidence_report
        self.assertIsNotNone(gui_result)
        direct_result = self.window.search_service.find_evidence(CLAIM, top_k=20)

        gui_classes = {item.evidence_class for item in gui_result.evidence}
        self.assertTrue({"DIRECT", "SECONDARY", "LIMITING", "CONTEXTUAL"}.issubset(gui_classes))
        self.assertEqual(gui_classes, {item.evidence_class for item in direct_result.evidence})
        gui_by_id = {item.evidence_id: item for item in gui_result.evidence}
        direct_by_id = {item.evidence_id: item for item in direct_result.evidence}
        self.assertEqual(set(gui_by_id), set(direct_by_id))
        for evidence_id, gui_item in gui_by_id.items():
            direct_item = direct_by_id[evidence_id]
            self.assertEqual(gui_item.paper.canonical_id, direct_item.paper.canonical_id)
            self.assertEqual(gui_item.paper.zotero_item_keys_merged, direct_item.paper.zotero_item_keys_merged)
            self.assertEqual(gui_item.provenance.origin, direct_item.provenance.origin)

    def test_gui_keyword_search_returns_real_results_through_the_core(self):
        self._wait_ready()
        self.window.search_mode.setCurrentIndex(self.window.search_mode.findData("keyword"))
        self.window.query.setText("strain")
        self.window.start_search()
        self._wait_not_busy()
        self.assertTrue(self.window.results)
        self.assertTrue(all(result.zotero_key for result in self.window.results))

    def test_second_gui_search_does_not_change_the_shared_search_service(self):
        """Sequential GUI searches must reuse the Core's already-loaded
        resources - never trigger a second load (V0.6 brief, section 22)."""
        self._wait_ready()
        search_service_before = self.server.resource_manager.search_service
        self.window.search_mode.setCurrentIndex(self.window.search_mode.findData("keyword"))
        self.window.query.setText("strain")
        self.window.start_search()
        self._wait_not_busy()
        self.window.start_search()
        self._wait_not_busy()
        self.assertIs(self.server.resource_manager.search_service, search_service_before)


if __name__ == "__main__":
    unittest.main()
