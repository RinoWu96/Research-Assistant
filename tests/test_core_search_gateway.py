"""Unit tests for app/core_service/search_gateway.py - the single place
`/search` and `/evidence_pack` input validation and result shaping happen
(V0.6 brief, sections 5 and 7: no duplicate retrieval implementation).

These mock `UnifiedSearchService`/`EvidenceService` themselves, so they
test only the gateway's validation/shaping logic, not real retrieval
(already covered by tests/test_hybrid_search.py and friends). A real,
fixture-backed end-to-end path is exercised in tests/test_core_http_api.py
and tests/test_gui_core_integration.py.
"""

from dataclasses import replace
import unittest
from unittest.mock import MagicMock, patch

from app.core_service import search_gateway
from app.search.hybrid_search import HybridResult

SAMPLE_RESULT = HybridResult(
    paper_id=1, zotero_key="P1", attachment_key="A1", title="Title", authors=("Author",),
    year="2020", journal="Journal", doi="10.1/x", page_start=3, page_end=3, text="Passage text.",
    pdf_path="P1.pdf", keyword_rank=1, semantic_rank=None, keyword_score=0.5, semantic_score=None,
    hybrid_score=0.5, chunk_id=7)


class SearchInputValidationTests(unittest.TestCase):
    def test_empty_query_rejected(self):
        with self.assertRaises(search_gateway.InvalidSearchRequestError):
            search_gateway.search(MagicMock(), "   ")

    def test_unknown_mode_rejected(self):
        with self.assertRaises(search_gateway.InvalidSearchRequestError):
            search_gateway.search(MagicMock(), "claim", mode="bogus")

    def test_top_k_out_of_range_rejected(self):
        with self.assertRaises(search_gateway.InvalidSearchRequestError):
            search_gateway.search(MagicMock(), "claim", top_k=0)
        with self.assertRaises(search_gateway.InvalidSearchRequestError):
            search_gateway.search(MagicMock(), "claim", top_k=10_000)


class SearchDelegationTests(unittest.TestCase):
    def test_delegates_to_the_shared_search_service_not_a_reimplementation(self):
        search_service = MagicMock()
        search_service.search.return_value = [SAMPLE_RESULT]
        results = search_gateway.search(search_service, "claim", mode="keyword", top_k=5,
                                        collection="C1", year_min=2020)
        search_service.search.assert_called_once_with(
            "claim", mode="keyword", top_k=5, collection="C1", year_min=2020)
        self.assertEqual(results, [
            {"paper_id": 1, "zotero_key": "P1", "attachment_key": "A1", "title": "Title",
             "authors": ("Author",), "year": "2020", "journal": "Journal", "doi": "10.1/x",
             "page_start": 3, "page_end": 3, "text": "Passage text.", "pdf_path": "P1.pdf",
             "keyword_rank": 1, "semantic_rank": None, "keyword_score": 0.5, "semantic_score": None,
             "hybrid_score": 0.5, "chunk_id": 7}])

    def test_value_error_from_backend_becomes_invalid_request(self):
        search_service = MagicMock()
        search_service.search.side_effect = ValueError("bad mode")
        with self.assertRaises(search_gateway.InvalidSearchRequestError):
            search_gateway.search(search_service, "claim")

    def test_unexpected_backend_exception_becomes_search_engine_error(self):
        search_service = MagicMock()
        search_service.search.side_effect = RuntimeError("boom")
        with self.assertRaises(search_gateway.SearchEngineError):
            search_gateway.search(search_service, "claim")

    def test_multiple_results_preserve_order(self):
        second = replace(SAMPLE_RESULT, paper_id=2, zotero_key="P2")
        search_service = MagicMock()
        search_service.search.return_value = [SAMPLE_RESULT, second]
        results = search_gateway.search(search_service, "claim")
        self.assertEqual([r["zotero_key"] for r in results], ["P1", "P2"])


class BuildEvidencePackTests(unittest.TestCase):
    def test_empty_query_rejected(self):
        with self.assertRaises(search_gateway.InvalidSearchRequestError):
            search_gateway.build_evidence_pack(MagicMock(), "   ")

    def test_delegates_to_evidence_service_reusing_the_shared_search_service(self):
        search_service = MagicMock()
        with patch("app.core_service.search_gateway.EvidenceService") as service_cls:
            pack = MagicMock()
            pack.to_dict.return_value = {"query": "claim", "papers": [], "candidate_count": 0,
                                        "duplicate_passages_removed": 0}
            service_cls.return_value.build_evidence_pack.return_value = pack
            result = search_gateway.build_evidence_pack(search_service, "claim", max_papers=5)
        service_cls.assert_called_once_with(search_gateway.DATABASE_PATH, search_service)
        service_cls.return_value.build_evidence_pack.assert_called_once_with("claim", max_papers=5)
        self.assertEqual(result["query"], "claim")

    def test_unexpected_backend_exception_becomes_search_engine_error(self):
        search_service = MagicMock()
        with patch("app.core_service.search_gateway.EvidenceService") as service_cls:
            service_cls.return_value.build_evidence_pack.side_effect = RuntimeError("boom")
            with self.assertRaises(search_gateway.SearchEngineError):
                search_gateway.build_evidence_pack(search_service, "claim")


if __name__ == "__main__":
    unittest.main()
