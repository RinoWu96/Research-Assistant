"""Evidence ranking by scientific usefulness, not retrieval score (V0.3 Task 6)."""

from dataclasses import dataclass
import unittest

from app.evidence.classifier import EvidenceClass
from app.evidence.ranker import EvidenceTier, rank, tier_for


@dataclass(frozen=True)
class FakeEvidence:
    label: str
    evidence_class: EvidenceClass
    doi: str
    retrieval_score: float


class RankerTests(unittest.TestCase):
    def test_direct_primary_ranks_above_secondary(self):
        direct = FakeEvidence("direct", EvidenceClass.DIRECT, "10.1/x", 0.1)
        secondary = FakeEvidence("secondary", EvidenceClass.SECONDARY, "10.1/y", 0.9)
        ranked = rank([secondary, direct])
        self.assertEqual([item.label for item in ranked], ["direct", "secondary"])

    def test_limiting_evidence_remains_visible_near_the_top(self):
        limiting = FakeEvidence("limiting", EvidenceClass.LIMITING, "10.1/x", 0.05)
        contextual = FakeEvidence("contextual", EvidenceClass.CONTEXTUAL, "10.1/y", 0.99)
        secondary = FakeEvidence("secondary", EvidenceClass.SECONDARY, "10.1/z", 0.99)
        ranked = rank([contextual, secondary, limiting])
        self.assertEqual(ranked[0].label, "limiting")

    def test_high_similarity_contextual_never_outranks_direct(self):
        contextual = FakeEvidence("contextual", EvidenceClass.CONTEXTUAL, "10.1/y", 0.99)
        direct = FakeEvidence("direct", EvidenceClass.DIRECT, "10.1/x", 0.01)
        ranked = rank([contextual, direct])
        self.assertEqual(ranked[0].label, "direct")

    def test_direct_without_doi_ranks_below_direct_with_doi(self):
        verified = FakeEvidence("verified", EvidenceClass.DIRECT, "10.1/x", 0.1)
        unverified = FakeEvidence("unverified", EvidenceClass.DIRECT, "", 0.9)
        ranked = rank([unverified, verified])
        self.assertEqual([item.label for item in ranked], ["verified", "unverified"])
        self.assertEqual(tier_for(verified), EvidenceTier.DIRECT_PRIMARY)
        self.assertEqual(tier_for(unverified), EvidenceTier.DIRECT_UNVERIFIED)

    def test_uncertain_ranks_last(self):
        uncertain = FakeEvidence("uncertain", EvidenceClass.UNCERTAIN, "", 0.99)
        contextual = FakeEvidence("contextual", EvidenceClass.CONTEXTUAL, "", 0.01)
        ranked = rank([uncertain, contextual])
        self.assertEqual(ranked[-1].label, "uncertain")


if __name__ == "__main__":
    unittest.main()
