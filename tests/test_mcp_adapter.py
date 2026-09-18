"""Unit tests for the MCP adapter boundary (app/external_api/mcp_adapter.py).

These mock `EvidenceReportService` itself - the "MCP adapter boundary" - so
they test only the MCP-facing validation/filtering/translation logic, not the
Evidence Engine (already covered by tests/test_evidence_report.py and friends).
A separate end-to-end test using the real Evidence Engine lives in
tests/test_mcp_integration.py.
"""

import unittest
from unittest.mock import patch

from app.evidence.citation_tracer import CitationStatus, CitationTrace
from app.evidence.classifier import EvidenceClass
from app.evidence.models import ClassifiedEvidence, EvidenceReport
from app.evidence.paper_identity import PaperIdentity
from app.external_api.mcp_adapter import (
    VALID_EVIDENCE_CLASSES,
    DatabaseUnavailableError,
    EvidenceEngineError,
    InvalidEvidenceTypeError,
    InvalidQueryError,
    find_evidence,
)
from app.search.database import DatabaseError


def _identity(key="P1", doi="10.1/x", zotero_keys=None):
    return PaperIdentity(f"doi:{key}", doi, zotero_keys or (key,), f"Title {key}",
                         (f"Author {key}",), "2020", (1,))


def _item(evidence_id, evidence_class, *, doi="10.1/x", zotero_key="P1", zotero_keys=None,
          score=0.5, provenance="original_retrieval", parent=None, traces=()):
    return ClassifiedEvidence(
        evidence_id=evidence_id, paper_identity=_identity(zotero_key, doi, zotero_keys),
        title=f"Title {zotero_key}", authors=(f"Author {zotero_key}",), journal="Journal",
        year="2020", doi=doi, zotero_key=zotero_key, attachment_key=f"A{zotero_key}",
        physical_pdf_page=1, pdf_path=f"{zotero_key}.pdf", original_text="Original passage text.",
        retrieval_score=score, evidence_class=evidence_class, evidence_confidence=0.8,
        rationale="because", claim_match=True, stimulus_match=True, material_match=True,
        phase_match=True, classification_source_type="unspecified", provenance=provenance,
        parent_evidence_id=parent, citation_traces=traces)


def _report(items):
    return EvidenceReport("query", tuple(items), candidate_count=len(items),
                          duplicate_passages_removed=0, duplicate_papers_merged=0)


class EvidenceVocabularyTests(unittest.TestCase):
    def test_public_evidence_types_match_internal_classifier_vocabulary(self):
        from app.external_api.mcp_schemas import EvidenceTypeName
        self.assertEqual(set(EvidenceTypeName.__args__), VALID_EVIDENCE_CLASSES)


class FindEvidenceInputValidationTests(unittest.TestCase):
    def test_empty_query_rejected(self):
        with self.assertRaises(InvalidQueryError):
            find_evidence("   ")

    def test_non_string_query_rejected(self):
        with self.assertRaises(InvalidQueryError):
            find_evidence(None)  # type: ignore[arg-type]

    def test_invalid_evidence_type_rejected(self):
        with self.assertRaises(InvalidEvidenceTypeError):
            find_evidence("claim", evidence_types=["NOT_A_CLASS"])

    def test_top_k_too_low_rejected(self):
        with self.assertRaises(InvalidQueryError):
            find_evidence("claim", top_k=0)

    def test_top_k_too_high_rejected(self):
        with self.assertRaises(InvalidQueryError):
            find_evidence("claim", top_k=10_000)


@patch("app.external_api.mcp_adapter.EvidenceReportService")
class FindEvidenceEngineDelegationTests(unittest.TestCase):
    def test_calls_real_evidence_report_service_not_a_reimplementation(self, service_cls):
        instance = service_cls.return_value
        instance.build_report.return_value = _report([_item("E1", EvidenceClass.DIRECT)])
        find_evidence("In2Se3 phase change by strain")
        service_cls.assert_called_once_with()
        instance.build_report.assert_called_once_with("In2Se3 phase change by strain")

    def test_database_error_becomes_structured_error(self, service_cls):
        service_cls.return_value.build_report.side_effect = DatabaseError("locked")
        with self.assertRaises(DatabaseUnavailableError):
            find_evidence("claim")

    def test_unexpected_engine_exception_becomes_structured_error(self, service_cls):
        service_cls.return_value.build_report.side_effect = RuntimeError("boom")
        with self.assertRaises(EvidenceEngineError):
            find_evidence("claim")

    def test_filtering_happens_after_classification_and_preserves_rank_order(self, service_cls):
        items = [_item("E1", EvidenceClass.DIRECT), _item("E2", EvidenceClass.LIMITING, zotero_key="P2"),
                 _item("E3", EvidenceClass.SECONDARY, zotero_key="P3")]
        service_cls.return_value.build_report.return_value = _report(items)
        result = find_evidence("claim", evidence_types=["DIRECT", "LIMITING"])
        self.assertEqual([e.evidence_id for e in result.evidence], ["E1", "E2"])
        self.assertEqual(result.total_classified_count, 2)

    def test_evidence_types_filter_never_hides_limiting_evidence(self, service_cls):
        items = [_item("E1", EvidenceClass.DIRECT), _item("E2", EvidenceClass.LIMITING, zotero_key="P2")]
        service_cls.return_value.build_report.return_value = _report(items)
        result = find_evidence("supporting evidence for claim")
        self.assertIn("LIMITING", {e.evidence_class for e in result.evidence})

    def test_top_k_applies_after_filtering(self, service_cls):
        items = [_item(f"E{i}", EvidenceClass.DIRECT, zotero_key=f"P{i}") for i in range(5)]
        service_cls.return_value.build_report.return_value = _report(items)
        result = find_evidence("claim", top_k=2)
        self.assertEqual(len(result.evidence), 2)
        self.assertEqual(result.total_classified_count, 5)

    def test_all_evidence_classes_serialize_correctly(self, service_cls):
        classes = [EvidenceClass.DIRECT, EvidenceClass.SECONDARY, EvidenceClass.LIMITING,
                   EvidenceClass.CONTEXTUAL, EvidenceClass.UNCERTAIN]
        items = [_item(f"E{i}", cls, zotero_key=f"P{i}") for i, cls in enumerate(classes)]
        service_cls.return_value.build_report.return_value = _report(items)
        result = find_evidence("claim", top_k=10)
        self.assertEqual({e.evidence_class for e in result.evidence}, {c.value for c in classes})

    def test_structured_output_contains_provenance_and_citation_traces(self, service_cls):
        trace = CitationTrace(marker="[30]", reference_number=30, status=CitationStatus.RESOLVED_LOCAL,
                              reference_doi="10.1/primary", reference_title_guess=None,
                              reference_authors_guess=None, reference_year=None,
                              resolved_identity=_identity("PRIM", "10.1/primary", ("PRIM1", "PRIM2")))
        secondary = _item("E1", EvidenceClass.SECONDARY, traces=(trace,))
        traced = _item("E2", EvidenceClass.DIRECT, zotero_key="PRIM", doi="10.1/primary",
                       provenance="citation_traced", parent="E1")
        service_cls.return_value.build_report.return_value = _report([secondary, traced])
        result = find_evidence("claim")
        by_id = {e.evidence_id: e for e in result.evidence}
        self.assertEqual(by_id["E1"].citation_traces[0].resolution_status, "resolved_local")
        self.assertEqual(by_id["E1"].citation_traces[0].resolved_paper.zotero_item_keys, ["PRIM1", "PRIM2"])
        self.assertEqual(by_id["E1"].provenance.origin, "original_retrieval")
        self.assertEqual(by_id["E1"].provenance.parent_evidence_id, None)
        self.assertEqual(by_id["E2"].provenance.origin, "citation_tracing")
        self.assertEqual(by_id["E2"].provenance.parent_evidence_id, "E1")

    def test_missing_optional_fields_are_null_not_fabricated(self, service_cls):
        item = _item("E1", EvidenceClass.UNCERTAIN, doi="")
        service_cls.return_value.build_report.return_value = _report([item])
        result = find_evidence("claim")
        self.assertIsNone(result.evidence[0].paper.doi)
        self.assertEqual(result.evidence[0].citation_traces, [])


if __name__ == "__main__":
    unittest.main()
