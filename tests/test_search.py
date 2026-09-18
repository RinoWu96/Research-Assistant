"""Keyword, source identity and normalization regression tests."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from app.search.database import Database
from app.search.search_service import SearchService
from app.search.text import normalize, query_terms
from app.zotero.models import Collection
from tests.test_database import example_paper, example_attachment, example_page


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "index.db"
        self.source = "Applying strain changes the band gap in α-In₂Se₃. Source text is preserved."
        with Database(self.path) as db:
            db.save_collections([Collection("C1", "Strain")])
            with db.transaction():
                paper_id = db.save_paper(example_paper())
            db.save_attachment(paper_id, example_attachment(), Path("paper.pdf"), "1", "OK", None,
                (example_page(self.source, 5), example_page("strain only", 6), example_page("bandgap In2Se3", 7)))
            db.reconcile_collection("C1", ["P1"])
        self.service = SearchService(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_acceptance_query_and_original_text(self):
        results = self.service.search("strain bandgap In2Se3")
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual((result.zotero_key, result.attachment_key, result.page, result.doi),
                         ("P1", "A1", 5, "10.test/1"))
        self.assertEqual(result.text, self.source)
        self.assertIn("α-In₂Se₃", result.snippet)
        self.assertEqual(results, self.service.search("strain bandgap In2Se3"))

    def test_filters(self):
        for filters in ({"collection": "C1"}, {"year": "2024"}, {"journal": "journal"}):
            self.assertEqual(len(self.service.search("strain bandgap", **filters)), 1)
        for filters in ({"collection": "absent"}, {"year": "1990"}, {"journal": "absent"}):
            self.assertEqual(self.service.search("strain", **filters), [])

    def test_literal_syntax_empty_and_limits(self):
        self.assertEqual(self.service.search("'' -- *"), [])
        self.assertEqual(self.service.search("strain OR nonexistent"), [])
        self.service.search('"strain"; DROP TABLE pages;')
        self.assertEqual(len(self.service.search("strain bandgap In2Se3")), 1)
        with self.assertRaises(ValueError):
            self.service.search("x" * 1001)

    def test_typographic_variants(self):
        for value in ("In₂Se₃", "In 2 Se 3", "In2 Se3", "In2Se3"):
            self.assertEqual(normalize(value), "In2Se3")
        self.assertEqual(normalize("band-\ngap"), "bandgap")
        self.assertEqual(query_terms("strain band gap In₂Se₃"), ["strain", "bandgap", "in2se3"])

    def test_failed_refresh_not_searchable(self):
        with Database(self.path) as db:
            db.save_attachment(1, example_attachment(), None, None, "PDF_MISSING", "Missing")
        self.assertEqual(self.service.search("strain"), [])
