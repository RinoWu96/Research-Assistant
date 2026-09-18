"""Deterministic evidence-relationship classification (V0.3 Task 2/3)."""

import unittest

from app.evidence.classifier import EvidenceClass, classify_passage

CLAIM = "In2Se3 phase transition induced by strain"


class ClassifierTests(unittest.TestCase):
    def test_direct_strain_induced_phase_transition(self):
        passage = ("We show that laser-induced wrinkling enables β′ → α phase transitions "
                   "through accumulated internal strain.")
        result = classify_passage(CLAIM, passage, paper_title="Wrinkle-Induced Phase Transition in In2Se3")
        self.assertEqual(result.evidence_class, EvidenceClass.DIRECT)
        self.assertTrue(result.stimulus_match)
        self.assertTrue(result.phase_match)
        self.assertGreaterEqual(result.evidence_confidence, 0.7)

    def test_secondary_attribution_language(self):
        passage = "Zhao et al. reported mechanical strain induced β′–α reversible phase transitions [30]."
        result = classify_passage(CLAIM, passage, paper_title="A Review of In2Se3 Phase Engineering")
        self.assertEqual(result.evidence_class, EvidenceClass.SECONDARY)

    def test_contextual_wrong_stimulus(self):
        passage = "We observe a current-induced α→β phase transition in In2Se3 devices."
        result = classify_passage(CLAIM, passage, paper_title="Electrically Driven Transitions in In2Se3")
        self.assertEqual(result.evidence_class, EvidenceClass.CONTEXTUAL)
        self.assertFalse(result.stimulus_match)
        self.assertTrue(result.material_match)
        self.assertTrue(result.phase_match)

    def test_limiting_negligible_effect(self):
        passage = ("External strain imposed on γ-In2Se3 and InSe exhibits a negligible effect "
                   "during the phase transition process.")
        result = classify_passage("strain drives In2Se3 phase transition", passage)
        self.assertEqual(result.evidence_class, EvidenceClass.LIMITING)

    def test_uncertain_when_off_topic(self):
        passage = "The synthesis method affects surface roughness in nanostructured thin films."
        result = classify_passage(CLAIM, passage, paper_title="Thin Film Synthesis Methods")
        self.assertEqual(result.evidence_class, EvidenceClass.UNCERTAIN)
        self.assertLessEqual(result.evidence_confidence, 0.4)

    def test_confidence_is_not_a_truth_probability(self):
        # Two DIRECT passages with different signal strength should get different
        # confidence, but both remain classification confidence, not truth probability.
        strong = classify_passage(CLAIM,
            "We demonstrate that applied strain directly induces the β′ to α phase transition in In2Se3.",
            paper_title="Strain-Induced Phase Transition in In2Se3")
        weak = classify_passage(CLAIM,
            "Strain triggers a phase transition.",
            paper_title="Strain-Induced Phase Transition in In2Se3")
        self.assertEqual(strong.evidence_class, EvidenceClass.DIRECT)
        self.assertLessEqual(weak.evidence_confidence, strong.evidence_confidence)
        self.assertLessEqual(strong.evidence_confidence, 1.0)

    def test_rationale_never_contains_unseen_facts_placeholder(self):
        passage = "Zhao et al. reported strain induced transitions [12]."
        result = classify_passage(CLAIM, passage)
        # Rationale must describe the passage, not fabricate paper identity or DOI.
        self.assertNotIn("10.", result.rationale)

    def test_ligature_normalization_matches_plain_ascii(self):
        # Real two-column PDF extraction commonly yields typographic ligatures
        # (U+FB00 "ﬀ") in place of plain ASCII letter pairs; classification must
        # not depend on whether the extractor happened to produce one.
        claim = "strain drives In2Se3 phase transition"
        ascii_passage = ("External strain imposed on In2Se3 exhibits a negligible effect "
                          "during the phase transition process.")
        ligature_passage = ("External strain imposed on In2Se3 exhibits a negligible eﬀect "
                             "during the phase transition process.")
        ascii_result = classify_passage(claim, ascii_passage)
        ligature_result = classify_passage(claim, ligature_passage)
        self.assertEqual(ligature_result.evidence_class, EvidenceClass.LIMITING)
        self.assertEqual(ligature_result.evidence_class, ascii_result.evidence_class)
        self.assertEqual(ligature_result.evidence_confidence, ascii_result.evidence_confidence)


if __name__ == "__main__":
    unittest.main()
