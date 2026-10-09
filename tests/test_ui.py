"""Offscreen desktop integration tests including the real Qt worker and PDF
preview.

V0.6: the GUI's Search/Evidence Pack/Evidence Report modes are all clients
of the Research Assistant Core Service, via `app.ui.core_search_adapter.
CoreSearchAdapter` (see app/ui/main_window.py). These tests inject
`_InProcessCoreAdapter` - a test double with that exact interface that
calls the real retrieval/evidence backends directly, in-process, instead
of over HTTP to a real Core - so MainWindow's own wiring/rendering code is
exercised against real (small, local, fixture) data without spinning up a
real Core process or loading the real BGE model in every GUI unit test.
`app.ui.core_process_manager.CoreMonitor` is never started in these tests
(`auto_connect_core=False`); Core lifecycle detection/startup itself is
covered by tests/test_ui_core_process_manager.py, and CoreSearchAdapter's
own HTTP-to-dataclass conversion by tests/test_ui_core_search_adapter.py.
tests/test_gui_core_integration.py covers one real, end-to-end GUI-over-
real-HTTP-Core path.
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import pymupdf
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.core_service.evidence_gateway import find_evidence as gateway_find_evidence
from app.evidence.evidence_service import EvidenceService
from app.evidence.models import EvidencePack, EvidencePassage, PaperEvidence
from app.external_api.mcp_schemas import (
    CitationTraceInfo,
    EvidenceItem,
    FindEvidenceResult,
    PaperInfo,
    ParsedReference,
    PassageInfo,
    ProvenanceInfo,
    ResolvedPaperRef,
    RetrievalSummary,
)
from app.search.database import Database
from app.search.hybrid_search import UnifiedSearchService
from app.search.indexer import IndexSummary
from app.ui.core_process_manager import UNAVAILABLE
from app.ui.main_window import MainWindow
from tests.test_database import example_attachment, example_page, example_paper


class _InProcessCoreAdapter:
    """`CoreSearchAdapter`'s exact interface, calling the real backends
    in-process (no HTTP, no real Core process, no real BGE model)."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._search_service = UnifiedSearchService(database_path)

    def search(self, query: str, **options):
        return self._search_service.search(query, **options)

    def build_evidence_pack(self, query: str, **options) -> EvidencePack:
        return EvidenceService(self._database_path, self._search_service).build_evidence_pack(query, **options)

    def find_evidence(self, query: str, **options) -> FindEvidenceResult:
        return gateway_find_evidence(self._search_service, query, database_path=self._database_path, **options)

    def close(self) -> None:
        pass


def _paper_info(key: str, doi: str, title: str = "Title") -> PaperInfo:
    return PaperInfo(canonical_id=f"doi:{doi}", title=title, authors=["Author"], journal="Journal",
                     year="2020", doi=doi, zotero_item_key=key, zotero_item_keys_merged=[key])


def _find_evidence_result() -> FindEvidenceResult:
    """One DIRECT item and one SECONDARY item with a resolved citation trace -
    enough to exercise grouping, provenance and citation-trace rendering."""
    direct = EvidenceItem(
        evidence_id="E1", evidence_class="DIRECT", classification_confidence=0.9,
        rationale="Directly states the claim.", retrieval_score=0.8,
        paper=_paper_info("DIRECT1", "10.1/direct", "Direct Paper"),
        passage=PassageInfo(text="The bandgap changes under strain.", pdf_page=3),
        provenance=ProvenanceInfo(attachment_key="AD1", origin="original_retrieval", parent_evidence_id=None),
        citation_traces=[])
    secondary = EvidenceItem(
        evidence_id="E2", evidence_class="SECONDARY", classification_confidence=0.6,
        rationale="Cites a primary source for the claim.", retrieval_score=0.5,
        paper=_paper_info("SEC1", "10.1/secondary", "Secondary Paper"),
        passage=PassageInfo(text="As shown by [12], strain modulates the bandgap.", pdf_page=5),
        provenance=ProvenanceInfo(attachment_key="AS1", origin="original_retrieval", parent_evidence_id=None),
        citation_traces=[CitationTraceInfo(
            citation_marker="[12]",
            parsed_reference=ParsedReference(doi="10.1/direct", title_guess=None, authors_guess=None, year=None),
            resolution_status="resolved_local",
            resolved_paper=ResolvedPaperRef(canonical_id="doi:10.1/direct", title="Direct Paper",
                                            zotero_item_keys=["DIRECT1"]))])
    return FindEvidenceResult(
        query="A scientific claim", evidence=[direct, secondary], returned_count=2, total_classified_count=2,
        retrieval_summary=RetrievalSummary(candidate_count=10, duplicate_passages_removed=1,
                                           duplicate_papers_merged=0))


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.path = root / "index.db"
        pdf = root / "paper.pdf"
        self.pdf = pdf
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), "strain bandgap In2Se3")
            doc.save(pdf)
        with Database(self.path) as db:
            with db.transaction():
                paper_id = db.save_paper(example_paper())
            with pymupdf.open(pdf) as source:
                original = source[0].get_text("text", sort=True)
            db.save_attachment(paper_id, example_attachment(), pdf, "test", "OK", None, (example_page(original),))
        self.window = MainWindow(self.path, auto_connect=False, auto_connect_core=False)
        # The Core Service is never started in these tests; inject a ready
        # in-process adapter directly instead (see module docstring).
        self.window.search_service = _InProcessCoreAdapter(self.path)
        self.window.core_state = "READY"
        self.window.core_status_payload = {"state": "READY", "database_ready": True,
                                           "semantic_index_ready": True, "embedding_model_ready": True,
                                           "paper_count": 1}
        self.window._update_search_availability()
        self.window.show()

    def wait_idle(self):
        deadline = time.monotonic() + 10
        while self.window.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window.busy, "Qt worker did not finish")

    def tearDown(self):
        self.wait_idle()
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_search_preview_copy_and_render(self):
        self.window.query.setText("strain bandgap In2Se3")
        self.window.start_search()
        self.wait_idle()
        self.assertEqual(len(self.window.results), 1)
        self.assertEqual(self.window.current_result.page, 1)
        self.assertIn("In2Se3", self.window.preview.toPlainText())
        self.window.copy_source()
        self.assertEqual(self.app.clipboard().text(), self.window.current_result.text)
        self.window.copy_reference()
        self.assertIn("PDF page 1", self.app.clipboard().text())
        self.window.show_pdf_page()
        self.wait_idle()
        self.assertTrue(self.window.pdf_dialog.isVisible())
        self.window.pdf_dialog.close()
        self.window.query.setText("nonexistent")
        self.window.start_search()
        self.wait_idle()
        self.assertEqual(len(self.window.results), 0)
        self.assertEqual(self.window.preview.toPlainText(), "")
        self.assertFalse(self.window.open_pdf.isEnabled())

    def test_disconnected_search_and_long_query(self):
        self.window._failed("connect", "Zotero is closed")
        self.window.query.setText("strain bandgap In2Se3")
        self.window.start_search()
        self.wait_idle()
        self.assertEqual(len(self.window.results), 1)
        self.window.query.setText("x" * 1001)
        self.window.start_search()
        self.assertFalse(self.window.busy)
        self.assertIn("1,000", self.window.message.text())

    def test_search_mode_and_filters_reach_unified_backend(self):
        self.assertEqual(self.window.search_mode.currentData(), "hybrid")
        self.window.collection.addItem("In2Se3", "IN2")
        self.window.collection.setCurrentIndex(1)
        self.window.year.setText("2020-2024")
        self.window.journal.setText("Materials")
        self.window.query.setText("mechanically driven electronic structure modification")
        with patch.object(self.window.search_service, "search", return_value=[]) as search:
            self.window.start_search()
            self.assertEqual(self.window.message.text(), "Searching...")
            self.wait_idle()
        self.assertEqual(search.call_args.args[0], "mechanically driven electronic structure modification")
        self.assertEqual(search.call_args.kwargs["mode"], "hybrid")
        self.assertEqual(search.call_args.kwargs["collection"], "IN2")
        self.assertEqual(search.call_args.kwargs["year_min"], 2020)
        self.assertEqual(search.call_args.kwargs["year_max"], 2024)
        self.assertEqual(search.call_args.kwargs["journal"], "Materials")
        self.assertEqual(search.call_args.kwargs["max_chunks_per_paper"], 2)
        self.assertIn("0 hybrid matches", self.window.results_label.text())

    def test_keyword_and_semantic_modes_also_reach_the_core_backend(self):
        """V0.6: all three modes - not just hybrid - go through the same
        Core-backed adapter (section 4/18 of the brief)."""
        self.window.query.setText("claim")
        for mode in ("keyword", "semantic"):
            self.window.search_mode.setCurrentIndex(self.window.search_mode.findData(mode))
            with patch.object(self.window.search_service, "search", return_value=[]) as search:
                self.window.start_search()
                self.wait_idle()
            self.assertEqual(search.call_args.kwargs["mode"], mode)

    def test_search_blocked_while_core_not_ready_then_works_once_core_becomes_ready(self):
        """V0.6, section 9: search is gated on Core readiness, not on a local
        semantic-index-file check - and never triggers a local fallback load."""
        self.window.core_state = "LOADING"
        self.window._update_search_availability()
        self.assertFalse(self.window.search_button.isEnabled())
        self.window.query.setText("strain bandgap In2Se3")
        self.window.start_search()
        self.assertFalse(self.window.busy)
        self.assertIn("still initializing", self.window.message.text())
        self.assertEqual(self.window.results, [])

        self.window.core_state = "READY"
        self.window._update_search_availability()
        self.assertTrue(self.window.search_button.isEnabled())
        self.window.start_search()
        self.wait_idle()
        self.assertEqual(len(self.window.results), 1)
        self.assertIn("In2Se3", self.window.preview.toPlainText())

    def _evidence_pack(self, *, papers=2):
        output = []
        for number in range(1, papers + 1):
            passage = EvidencePassage(number, f"P{number}", f"A{number}", f"Paper {number}",
                (f"Author {number}",), "2024", "Journal", f"10/{number}", number,
                f"Original PDF text {number}", .04, .8, -1.0, number, str(self.pdf))
            output.append(PaperEvidence(number, f"P{number}", f"Paper {number}",
                (f"Author {number}",), "2024", "Journal", f"10/{number}", .04,
                (passage,)))
        return EvidencePack("A scientific claim", tuple(output), 50, 0)

    def test_evidence_mode_filters_settings_grouping_and_selection(self):
        self.window.app_mode.setCurrentIndex(self.window.app_mode.findData("evidence"))
        self.assertEqual(self.window.search_button.text(), "Find Evidence")
        self.assertTrue(self.window.evidence_settings.isVisible())
        self.window.collection.addItem("In2Se3", "IN2")
        self.window.collection.setCurrentIndex(1)
        self.window.year.setText("2020-2024")
        self.window.journal.setText("Journal")
        self.window.max_papers.setValue(6)
        self.window.max_passages.setValue(2)
        self.window.candidate_pool.setValue(60)
        self.window.query.setText("A scientific claim")
        pack = self._evidence_pack()
        with patch.object(self.window.search_service, "build_evidence_pack", return_value=pack) as build:
            self.window.start_search()
            self.assertEqual(self.window.message.text(), "Building evidence pack...")
            self.wait_idle()
        self.assertEqual(build.call_args.kwargs, {
            "collection": "IN2", "year_min": 2020, "year_max": 2024, "journal": "Journal",
            "max_papers": 6, "max_passages_per_paper": 2, "candidate_pool": 60})
        self.assertEqual(len(self.window.evidence_checks), 2)
        self.assertTrue(all(check.isChecked() for check, _ in self.window.evidence_checks))
        self.window.evidence_checks[1][0].setChecked(False)
        self.window.copy_evidence_pack()
        copied = self.app.clipboard().text()
        self.assertIn("# Literature Evidence Pack", copied)
        self.assertIn("Original PDF text 1", copied)
        self.assertNotIn("Original PDF text 2", copied)
        self.window.copy_for_ai()
        self.assertIn("Do not invent references", self.app.clipboard().text())
        self.assertIn("PDF page 1", self.app.clipboard().text())
        with patch("app.ui.main_window.QDesktopServices.openUrl", return_value=True) as opened:
            self.window.open_evidence_pdf(pack.papers[0].passages[0])
        self.assertEqual(opened.call_count, 1)

    def test_evidence_exports_and_empty_state(self):
        pack = self._evidence_pack(papers=1)
        self.window.evidence_pack = pack
        self.window._render_evidence_pack(pack)
        markdown = Path(self.temp.name) / "pack.md"
        with patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(str(markdown), "")):
            self.window.export_evidence("markdown")
        self.assertIn("Original PDF text 1", markdown.read_text(encoding="utf-8"))
        target = Path(self.temp.name) / "pack.json"
        with patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            self.window.export_evidence("json")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["papers"][0]["passages"][0]["physical_pdf_page"], 1)
        self.assertEqual(payload["papers"][0]["passages"][0]["original_text"], "Original PDF text 1")
        self.window._finished("evidence", EvidencePack("none", (), 0, 0))
        self.assertIn("No relevant literature", self.window.evidence_summary.text())

    def test_evidence_report_mode_groups_classes_and_shows_provenance_and_traces(self):
        """V0.6, section 13: DIRECT/SECONDARY/... grouping, paper/PDF/DOI/
        Zotero-key provenance, and citation-trace status, in a plain
        readable report pane - no card redesign."""
        self.window.app_mode.setCurrentIndex(self.window.app_mode.findData("evidence_report"))
        self.assertEqual(self.window.search_button.text(), "Find Evidence")
        self.window.query.setText("A scientific claim")
        result = _find_evidence_result()
        with patch.object(self.window.search_service, "find_evidence", return_value=result) as find:
            self.window.start_search()
            self.assertEqual(self.window.message.text(), "Building evidence report...")
            self.wait_idle()
        find.assert_called_once_with("A scientific claim", top_k=20)
        text = self.window.evidence_report_text.toPlainText()
        self.assertIn("=== DIRECT (1) ===", text)
        self.assertIn("=== SECONDARY (1) ===", text)
        self.assertIn("Direct Paper", text)
        self.assertIn("DOI: 10.1/direct", text)
        self.assertIn("Zotero: DIRECT1", text)
        self.assertIn("PDF page 3", text)
        self.assertIn("The bandgap changes under strain.", text)
        self.assertIn("Citation trace [12]: resolved_local -> Direct Paper", text)
        self.assertTrue(self.window.copy_report_button.isEnabled())
        self.window.copy_evidence_report()
        self.assertEqual(self.app.clipboard().text(), text)

    def test_evidence_report_empty_state(self):
        self.window.app_mode.setCurrentIndex(self.window.app_mode.findData("evidence_report"))
        empty = FindEvidenceResult(query="x", evidence=[], returned_count=0, total_classified_count=0,
                                   retrieval_summary=RetrievalSummary(candidate_count=0,
                                                                      duplicate_passages_removed=0,
                                                                      duplicate_papers_merged=0))
        self.window._finished("evidence_report", empty)
        self.assertIn("No relevant literature", self.window.evidence_report_summary.text())
        self.assertFalse(self.window.copy_report_button.isEnabled())

    def test_core_loading_and_error_states_are_shown_clearly(self):
        self.window._core_state_changed("LOADING", {"state": "LOADING"})
        self.assertIn("preparing", self.window.core_status.text())
        self.assertIn("preparing the semantic model", self.window.message.text())
        self.assertFalse(self.window.search_button.isEnabled())

        self.window._core_state_changed(
            "ERROR", {"state": "ERROR", "error": "Semantic index is missing or incomplete."})
        self.assertIn("error", self.window.core_status.text().lower())
        self.assertIn("Semantic index is missing or incomplete.", self.window.message.text())
        self.assertFalse(self.window.search_button.isEnabled())

    def test_core_unavailable_does_not_fall_back_to_a_local_heavy_search(self):
        """V0.6, section 11: no DesktopSearchService/UnifiedSearchService
        fallback when the Core cannot be reached or started."""
        window = MainWindow(self.path, auto_connect=False, auto_connect_core=False)
        try:
            window._core_state_changed(UNAVAILABLE, {})
            self.assertIsNone(window.search_service)
            self.assertIn("unavailable", window.core_status.text())
            self.assertFalse(window.search_button.isEnabled())
            window.query.setText("strain bandgap In2Se3")
            window.start_search()
            self.assertFalse(window.busy)
            self.assertIsNone(window.search_service)
        finally:
            window.close()
            self.app.processEvents()
            window.deleteLater()
            self.app.processEvents()

    def test_closing_the_gui_never_touches_the_core_process(self):
        """V0.6, section 7: GUI shutdown must not stop the Core - there is no
        such method to even call, and closeEvent never reaches for one."""
        self.assertFalse(hasattr(self.window.core_manager, "stop"))
        self.assertFalse(hasattr(self.window.core_manager, "terminate"))
        with patch.object(self.window.core_monitor, "stop") as stop:
            self.window.close()
            self.app.processEvents()
        stop.assert_called_once()

    def test_incremental_and_rebuild_are_separate_actions(self):
        self.window.collection.addItem("Selected collection", "C1")
        self.window.collection.setCurrentIndex(1)
        summary = IndexSummary(papers=3, indexed=1, skipped=1, updated=1)
        def update(*args, **kwargs):
            kwargs["progress"](summary.describe("Progress"))
            return summary
        semantic = patch("app.embedding.semantic_index.SemanticIndex")
        with semantic as semantic_index,                 patch("app.ui.main_window.Indexer.run", side_effect=update) as incremental:
            semantic_index.return_value.update.return_value.describe.return_value = "semantic done"
            self.window.index_button.click()
            self.assertFalse(self.window.rebuild_button.isEnabled())
            self.wait_idle()
            self.assertEqual(incremental.call_args.args, ("C1",))
            self.assertNotIn("force", incremental.call_args.kwargs)
            self.assertIn("1 indexed, 1 skipped, 1 updated, 0 failed", self.window.index_counts.text())
            # The semantic index is brought up to date in the same action.
            semantic_index.return_value.update.assert_called_once()
            self.assertIn("semantic done", self.window.index_counts.text())
        with semantic as semantic_index,                 patch("app.ui.main_window.Indexer.rebuild", return_value=summary) as rebuild:
            semantic_index.return_value.update.return_value.describe.return_value = "semantic done"
            self.window.rebuild_button.click()
            self.wait_idle()
            self.assertEqual(rebuild.call_args.args, ())
            self.assertNotIn("collection_key", rebuild.call_args.kwargs)
            semantic_index.return_value.update.assert_called_once()
        self.window.query.setText("strain")
        self.window.start_search()
        self.wait_idle()
        self.assertIn("1 updated", self.window.index_counts.text())


class GuiDoesNotOwnHeavyResourcesTests(unittest.TestCase):
    """V0.6, sections 1-2, 11-12: structural check that the production GUI
    module never imports or constructs the heavy Evidence Engine directly."""

    def test_main_window_module_has_no_heavy_engine_names(self):
        import app.ui.main_window as main_window_module
        heavy_names = {"EvidenceReportService", "UnifiedSearchService", "LocalEmbedder",
                      "SemanticIndex", "DesktopSearchService", "EvidenceService"}
        self.assertFalse(heavy_names & set(dir(main_window_module)))


if __name__ == "__main__":
    unittest.main()
