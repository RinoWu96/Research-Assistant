"""Evidence grouping, traceability, limits, deduplication and exports."""

import json
from pathlib import Path
import tempfile
import unittest

from app.embedding.semantic_index import SemanticIndex
from app.evidence.evidence_service import EvidenceService, _deduplicate
from app.search.hybrid_search import HybridResult, UnifiedSearchService
from tests.semantic_fixtures import FakeEmbedder, build_corpus


def result(paper: int, page: int, text: str, score: float, *, chunk: int) -> HybridResult:
    return HybridResult(paper, f"P{paper}", f"A{paper}", f"Paper {paper}", (f"Author {paper}",),
        "2024", "Journal", f"10/{paper}", page, page, text, f"p{paper}.pdf",
        1, 1, -1.0, 0.8, score, chunk)


class StubSearch:
    def __init__(self, results):
        self.results = results
        self.options = None

    def search(self, query, **options):
        self.options = options
        return self.results[:options["top_k"]]


class EvidenceTests(unittest.TestCase):
    def test_group_limits_dedup_metadata_pages_and_original_text(self):
        original = "Curvature changes the local electronic structure without rewriting."
        duplicate = "Curvature changes the local electronic structure without rewriting. Extra"
        stub = StubSearch([
            result(1, 3, original, .050, chunk=10),
            result(1, 3, duplicate, .040, chunk=11),
            result(1, 6, "A distinct original passage on another physical page.", .035, chunk=12),
            result(2, 2, "Original evidence from a second paper.", .045, chunk=20),
            result(3, 9, "Third paper should be limited out.", .010, chunk=30),
        ])
        pack = EvidenceService(search_service=stub).build_evidence_pack(
            "scientific claim", collection="IN2", year_min=2020, year_max=2024,
            journal="Journal", max_papers=2, max_passages_per_paper=2)
        self.assertEqual(len(pack.papers), 2)
        self.assertEqual(pack.duplicate_passages_removed, 1)
        self.assertEqual([p.physical_pdf_page for p in pack.papers[0].passages], [3, 6])
        passage = pack.papers[0].passages[0]
        self.assertEqual(passage.original_text, original)
        self.assertEqual((passage.title, passage.authors, passage.year, passage.journal,
                          passage.doi, passage.zotero_key, passage.attachment_key),
                         ("Paper 1", ("Author 1",), "2024", "Journal", "10/1", "P1", "A1"))
        self.assertEqual(stub.options["mode"], "hybrid")
        self.assertGreaterEqual(stub.options["top_k"], 50)
        self.assertEqual(stub.options["collection"], "IN2")
        self.assertEqual(stub.options["year_min"], 2020)
        self.assertEqual(stub.options["year_max"], 2024)
        self.assertEqual(stub.options["journal"], "Journal")

    def test_markdown_json_and_ai_exports(self):
        pack = EvidenceService(search_service=StubSearch([
            result(1, 3, "Exact original PDF passage.", .05, chunk=10)
        ])).build_evidence_pack("A claim")
        markdown = pack.to_markdown()
        self.assertIn("# Literature Evidence Pack", markdown)
        self.assertIn("Candidate passage · PDF page 3", markdown)
        self.assertIn("> Exact original PDF passage.", markdown)
        payload = json.loads(pack.to_json())
        self.assertEqual(payload["papers"][0]["passages"][0]["original_text"],
                         "Exact original PDF passage.")
        copied = pack.to_ai_markdown()
        self.assertIn("Do not invent references", copied)
        self.assertIn("10/1", copied)

    def test_real_hybrid_collection_filter_and_paper_diversity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "literature.db"
            build_corpus(database)
            index = SemanticIndex(database, root / "semantic", FakeEmbedder())
            index.update()
            service = EvidenceService(database, UnifiedSearchService(database, semantic_index=index))
            pack = service.build_evidence_pack("curvature induced strain", collection="IN2",
                                               max_papers=2, max_passages_per_paper=1)
            self.assertEqual({paper.zotero_key for paper in pack.papers}, {"P2", "P3"})
            self.assertTrue(all(len(paper.passages) == 1 for paper in pack.papers))
            self.assertEqual(len({paper.paper_id for paper in pack.papers}), len(pack.papers))

    def test_overlap_requires_same_paper_and_page(self):
        text = "same heavily overlapping scientific passage"
        unique, removed = _deduplicate([
            result(1, 1, text, .05, chunk=1), result(1, 1, text, .04, chunk=2),
            result(1, 2, text, .03, chunk=3), result(2, 1, text, .02, chunk=4),
        ])
        self.assertEqual((len(unique), removed), (3, 1))


class SupplementaryEvidenceTests(unittest.TestCase):
    def test_passages_from_supporting_information_are_flagged(self):
        from dataclasses import replace
        from app.search.database import Database
        from tests.test_database import example_attachment, example_paper
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "literature.db"
            with Database(path) as db:
                with db.transaction():
                    main_id = db.save_paper(example_paper())
                    si_id = db.save_paper(replace(example_paper(), key="P2"))
                db.save_attachment(main_id, example_attachment("A1"), None, None, "OK", None)
                db.save_attachment(si_id, replace(example_attachment("A2"), title="Supplementary Information PDF"),
                                   None, None, "OK", None)
            stub = StubSearch([result(1, 1, "Main text passage.", .05, chunk=1),
                               result(2, 4, "Raman fitting details in the SI.", .04, chunk=2)])
            pack = EvidenceService(path, stub).build_evidence_pack("Raman")
        flags = {p.attachment_key: p.supplementary for paper in pack.papers for p in paper.passages}
        self.assertEqual(flags, {"A1": False, "A2": True})
        self.assertIn("A2 (supporting information)", pack.to_markdown())



if __name__ == "__main__":
    unittest.main()
