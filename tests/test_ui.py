"""Offscreen desktop integration tests including the real Qt worker and PDF preview."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import json

import pymupdf
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from app.search.database import Database
from app.search.indexer import IndexSummary
from app.evidence.models import EvidencePack, EvidencePassage, PaperEvidence
from app.ui.main_window import MainWindow
from tests.test_database import example_paper, example_attachment, example_page


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
        self.window = MainWindow(self.path, auto_connect=False)
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

    def test_semantic_missing_state_is_clear_and_keyword_remains_available(self):
        self.window.search_mode.setCurrentIndex(self.window.search_mode.findData("semantic"))
        self.assertIn("Not built", self.window.semantic_status.text())
        self.window.query.setText("conceptual deformation")
        self.window.start_search()
        self.assertFalse(self.window.busy)
        self.assertIn("Semantic index has not been built", self.window.message.text())
        self.window.search_mode.setCurrentIndex(self.window.search_mode.findData("keyword"))
        self.assertIn("精确关键词检索", self.window.search_help.text())
        self.window.query.setText("strain bandgap In2Se3")
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

    def _semantic_ready(self):
        directory = self.path.parent / "semantic"
        directory.mkdir()
        for name in ("index.faiss", "chunks.sqlite", "index_metadata.json"):
            (directory / name).touch()

    def test_evidence_mode_filters_settings_grouping_and_selection(self):
        self._semantic_ready()
        self.window._refresh_semantic_status()
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

    def test_incremental_and_rebuild_are_separate_actions(self):
        self.window.collection.addItem("Selected collection", "C1")
        self.window.collection.setCurrentIndex(1)
        summary = IndexSummary(papers=3, indexed=1, skipped=1, updated=1)
        def update(*args, **kwargs):
            kwargs["progress"](summary.describe("Progress"))
            return summary
        with patch("app.ui.main_window.Indexer.run", side_effect=update) as incremental:
            self.window.index_button.click()
            self.assertFalse(self.window.rebuild_button.isEnabled())
            self.wait_idle()
            self.assertEqual(incremental.call_args.args, ("C1",))
            self.assertNotIn("force", incremental.call_args.kwargs)
            self.assertIn("1 indexed, 1 skipped, 1 updated, 0 failed", self.window.index_counts.text())
        with patch("app.ui.main_window.Indexer.rebuild", return_value=summary) as rebuild:
            self.window.rebuild_button.click()
            self.wait_idle()
            self.assertEqual(rebuild.call_args.args, ())
            self.assertNotIn("collection_key", rebuild.call_args.kwargs)
        self.window.query.setText("strain")
        self.window.start_search()
        self.wait_idle()
        self.assertIn("1 updated", self.window.index_counts.text())
