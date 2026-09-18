"""Unit tests for app/ui/core_search_adapter.py: the thin GUI-specific
adapter around app.core_service.client.CoreClient (V0.6 brief, section 3).
Mocks CoreClient entirely - no HTTP call is ever made - and checks only
that dicts round-trip back into the exact dataclasses
app.ui.main_window's rendering code expects, with provenance fields
(zotero_key, attachment_key, DOI, PDF page, etc.) intact.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.evidence.models import EvidencePack
from app.external_api.mcp_schemas import FindEvidenceResult, RetrievalSummary
from app.search.hybrid_search import HybridResult
from app.ui.core_search_adapter import CoreSearchAdapter

BASE_URL = "http://127.0.0.1:8756"

RESULT_DICT = {
    "paper_id": 1, "zotero_key": "P1", "attachment_key": "A1", "title": "Title",
    "authors": ["Author One", "Author Two"], "year": "2020", "journal": "Journal", "doi": "10.1/x",
    "page_start": 3, "page_end": 3, "text": "Passage text.", "pdf_path": "P1.pdf",
    "keyword_rank": 1, "semantic_rank": None, "keyword_score": 0.5, "semantic_score": None,
    "hybrid_score": 0.5, "chunk_id": 7,
}

PACK_DICT = {
    "query": "claim", "candidate_count": 10, "duplicate_passages_removed": 1,
    "papers": [{
        "paper_id": 1, "zotero_key": "P1", "title": "Title", "authors": ["Author"], "year": "2020",
        "journal": "Journal", "doi": "10.1/x", "relevance_score": 0.9,
        "passages": [{
            "paper_id": 1, "zotero_key": "P1", "attachment_key": "A1", "title": "Title",
            "authors": ["Author"], "year": "2020", "journal": "Journal", "doi": "10.1/x",
            "physical_pdf_page": 3, "original_text": "Passage.", "hybrid_score": 0.5,
            "semantic_score": None, "keyword_score": 0.5, "chunk_id": 7, "pdf_path": "P1.pdf",
        }],
    }],
}


class SearchTests(unittest.TestCase):
    @patch("app.ui.core_search_adapter.CoreClient")
    def test_search_forwards_to_core_client_and_returns_hybrid_results(self, client_cls):
        client_cls.return_value.search.return_value = [RESULT_DICT]
        adapter = CoreSearchAdapter(BASE_URL)
        results = adapter.search("claim", mode="hybrid", top_k=10, collection="C1")
        client_cls.assert_called_once_with(base_url=BASE_URL)
        client_cls.return_value.search.assert_called_once_with("claim", mode="hybrid", top_k=10, collection="C1")
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertIsInstance(result, HybridResult)
        self.assertEqual(result.zotero_key, "P1")
        self.assertEqual(result.attachment_key, "A1")
        self.assertEqual(result.doi, "10.1/x")
        self.assertEqual(result.page, 3)  # HybridResult.page property == page_start
        self.assertEqual(result.authors, ("Author One", "Author Two"))

    @patch("app.ui.core_search_adapter.CoreClient")
    def test_empty_results_round_trip_cleanly(self, client_cls):
        client_cls.return_value.search.return_value = []
        self.assertEqual(CoreSearchAdapter(BASE_URL).search("claim"), [])


class BuildEvidencePackTests(unittest.TestCase):
    @patch("app.ui.core_search_adapter.CoreClient")
    def test_round_trips_into_evidence_pack_dataclasses(self, client_cls):
        client_cls.return_value.build_evidence_pack.return_value = PACK_DICT
        adapter = CoreSearchAdapter(BASE_URL)
        pack = adapter.build_evidence_pack("claim", max_papers=5)
        client_cls.return_value.build_evidence_pack.assert_called_once_with("claim", max_papers=5)
        self.assertIsInstance(pack, EvidencePack)
        self.assertEqual(pack.query, "claim")
        self.assertEqual(pack.candidate_count, 10)
        self.assertEqual(len(pack.papers), 1)
        paper = pack.papers[0]
        self.assertEqual(paper.zotero_key, "P1")
        passage = paper.passages[0]
        self.assertEqual(passage.attachment_key, "A1")
        self.assertEqual(passage.physical_pdf_page, 3)
        self.assertEqual(passage.authors, ("Author",))


class FindEvidenceTests(unittest.TestCase):
    @patch("app.ui.core_search_adapter.CoreClient")
    def test_delegates_directly_to_core_client(self, client_cls):
        expected = FindEvidenceResult(
            query="claim", evidence=[], returned_count=0, total_classified_count=0,
            retrieval_summary=RetrievalSummary(candidate_count=0, duplicate_passages_removed=0,
                                               duplicate_papers_merged=0))
        client_cls.return_value.find_evidence.return_value = expected
        adapter = CoreSearchAdapter(BASE_URL)
        result = adapter.find_evidence("claim", top_k=20)
        client_cls.return_value.find_evidence.assert_called_once_with("claim", top_k=20)
        self.assertIs(result, expected)


class CloseTests(unittest.TestCase):
    @patch("app.ui.core_search_adapter.CoreClient")
    def test_close_never_raises(self, client_cls):
        CoreSearchAdapter(BASE_URL).close()  # must not raise; no persistent connection to close


if __name__ == "__main__":
    unittest.main()
