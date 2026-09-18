"""Reference parsing and citation-to-primary-source tracing (V0.3 Task 4/5)."""

import unittest

from app.evidence.citation_tracer import CitationStatus, trace_citations
from app.evidence.paper_identity import PaperRecord
from app.evidence.reference_parser import find_citation_markers, parse_bibliography

FULLTEXT = """Strain effects have been widely studied in 2D materials.

References
[29] Someone Else. A prior unrelated paper. Journal of Nothing 2019.
[30] Zhao, W. Strain-Driven Phase Transition in In2Se3. Nature Materials 2020.
[31] Chen, L. Doped structures in TMDs. Advanced Materials 2018. 10.1000/chen.doped
"""

FULLTEXT_WITH_DOI = """Body text here.

References
[29] Someone Else. An unrelated study. Journal of Nothing 2019.
[30] Zhao, W. Strain-Driven Phase Transition in In2Se3. Nature Materials, 2020. https://doi.org/10.1038/s41563-020-0001
"""

# Ample filler so the embedded-heading fallback's "later portion of the
# document" position check behaves like it would on a real, mostly-body paper
# rather than on a document that is almost entirely its own reference list.
_BODY_FILLER = "Strain effects on In2Se3 phase transitions have been widely studied. " * 60

# Reproduces the real V0.3.1 acceptance failure: a two-column PDF extraction
# where "References" lands mid-line (interleaved with the tail of the
# previous column) instead of alone on its own line, and the numbered entries
# themselves alternate between two columns instead of running 1, 2, 3, ...
TWO_COLUMN_FULLTEXT = (
    _BODY_FILLER + "\n\n"
    "Prior work reported several related mechanisms [29] [30] [31] [32] [33].\n\n"
    "Some concluding sentence of an earlier section. Nat. Commun. 2024, 15, 7471.\n"
    "References                                                          [18] Bloggs, A. B. Unrelated older work. Nat. Mater. 2019, 10, 1.\n"
    "[29] Alpha, X. Y.; Beta, C. D. et al. Phase tailoring of In2Se3 toward van der Waals heterostructures. Small Methods 2025, 9, 100.\n"
    "                                                                     [19] Carol, E. F. Another unrelated reference. Science 2020, 5, 55.\n"
    "[30] Delta, G. H.; Epsilon, I. J. et al. Strain-Driven Phase Transition in In2Se3. Nature Materials 2020, 20, 200.\n"
    "                                                                     [20] Zeta, K. L. Yet another unrelated reference. Nature 2021, 6, 66.\n"
    "[31] Eta, M. N. Doped structures in TMDs. Advanced Materials 2018, 30, 300.\n"
    "                                                                     [21] Theta, O. P. A fourth unrelated reference. Cell 2019, 7, 77.\n"
    "[32] Iota, Q. R.; Kappa, S. T. et al. Interlayer reconstruction phase transition in van der Waals materials. Nat. Mater. 2025, 24, 369.\n"
    "                                                                     [22] Lambda, U. V. A fifth unrelated reference. Science 2022, 8, 88.\n"
    "[33] Mu, W. X. Resistive switching in In2Se3 devices. ACS Nano 2025, 19, 400.\n"
    "                                                                     [23] Nu, Y. Z. A sixth unrelated reference. Nature 2023, 9, 99.\n"
)

PROSE_WITH_REFERENCES_WORD = (
    _BODY_FILLER + "\n\n"
    "As previously discussed, references have shown that similar effects occur in related materials [12]. "
    "See references [13, 14] for a broader survey of the field.\n" + _BODY_FILLER
)

MALFORMED_REFERENCES_SECTION = (
    _BODY_FILLER + "\n\n"
    "References                                                          [18] Bloggs, A. B. Only one entry follows, with nothing else nearby. "
    "Nat. Mater. 2019, 10, 1.\n"
)


class MarkerParsingTests(unittest.TestCase):
    def test_single_marker(self):
        markers = find_citation_markers("as shown previously [30].")
        self.assertEqual([m.reference_numbers for m in markers], [(30,)])

    def test_comma_separated_marker(self):
        markers = find_citation_markers("consistent with prior work [30,31].")
        self.assertEqual(markers[0].reference_numbers, (30, 31))

    def test_range_marker(self):
        markers = find_citation_markers("several groups agree [30-33].")
        self.assertEqual(markers[0].reference_numbers, (30, 31, 32, 33))

    def test_non_numeric_bracket_is_ignored(self):
        self.assertEqual(find_citation_markers("see [Figure 1] for details."), [])


class BibliographyParsingTests(unittest.TestCase):
    def test_numbered_reference_maps_to_entry(self):
        bibliography = parse_bibliography(FULLTEXT)
        self.assertIn(30, bibliography)
        self.assertEqual(bibliography[30].year, "2020")
        self.assertIn("In2Se3", bibliography[30].title_guess)

    def test_reference_doi_extracted_when_present(self):
        bibliography = parse_bibliography(FULLTEXT)
        self.assertEqual(bibliography[31].doi, "10.1000/chen.doped")

    def test_no_references_heading_returns_empty(self):
        self.assertEqual(parse_bibliography("Just some body text with [30] in it."), {})


class EmbeddedHeadingFallbackTests(unittest.TestCase):
    """V0.3.1: real two-column PDFs commonly extract with the "References"
    heading landing mid-line instead of alone on its own line."""

    def test_standalone_heading_still_takes_priority(self):
        # The strict, highest-confidence detector must still be used first;
        # this is the same fixture the pre-existing tests rely on.
        bibliography = parse_bibliography(FULLTEXT)
        self.assertIn(30, bibliography)

    def test_embedded_two_column_heading_is_detected(self):
        bibliography = parse_bibliography(TWO_COLUMN_FULLTEXT)
        self.assertIn(30, bibliography)
        self.assertIn(32, bibliography)

    def test_ordinary_prose_mentioning_references_is_not_detected(self):
        self.assertEqual(parse_bibliography(PROSE_WITH_REFERENCES_WORD), {})

    def test_malformed_or_sparse_section_remains_unresolved(self):
        self.assertEqual(parse_bibliography(MALFORMED_REFERENCES_SECTION), {})


class TwoColumnBibliographyParsingTests(unittest.TestCase):
    """V0.3.1: numbered entries interleaved column-by-column must be parsed
    without merging text from the two columns into the wrong entry."""

    def setUp(self):
        self.bibliography = parse_bibliography(TWO_COLUMN_FULLTEXT)

    def test_sequential_numbered_references_parsed_correctly(self):
        for number in (29, 30, 31, 32, 33):
            self.assertIn(number, self.bibliography)

    def test_wrapped_column_entries_are_not_cross_contaminated(self):
        # Entry 30 sits between two right-column entries ([19] and [20]) in
        # the raw extracted text; its own title/year must not pick up their
        # text or vice versa.
        entry_30 = self.bibliography[30]
        self.assertIn("In2Se3", entry_30.title_guess)
        self.assertNotIn("Carol", entry_30.raw_text)
        self.assertNotIn("Zeta", entry_30.raw_text)
        self.assertEqual(entry_30.year, "2020")

        entry_19 = self.bibliography.get(19)
        if entry_19 is not None:
            self.assertNotIn("Delta", entry_19.raw_text)
            self.assertNotIn("Epsilon", entry_19.raw_text)

    def test_multi_author_et_al_entry_title_is_clean(self):
        # Entry 32 uses the common "Surname, Initials.; ... et al. Title."
        # format; the title must not be a fragment cut at an author's own
        # initials.
        entry_32 = self.bibliography[32]
        self.assertIn("Interlayer reconstruction", entry_32.title_guess)
        self.assertEqual(entry_32.year, "2025")


class CitationTracerTests(unittest.TestCase):
    def setUp(self):
        self.local_papers = [
            PaperRecord(1, "PRIMARY", "Strain-Driven Phase Transition in In2Se3", ("Zhao",), "2020",
                        "10.1038/s41563-020-0001"),
            PaperRecord(2, "SECONDARY", "A Review of In2Se3 Phase Engineering", ("Lee",), "2023", ""),
        ]

    def test_doi_reference_resolves_to_local_paper(self):
        passage = "Zhao et al. reported strain-driven phase transitions in In2Se3 [30]."
        traces = trace_citations(passage, FULLTEXT_WITH_DOI, self.local_papers)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].status, CitationStatus.RESOLVED_LOCAL)
        self.assertEqual(traces[0].resolved_paper_id, 1)

    def test_title_author_year_reference_resolves_without_doi(self):
        passage = "Zhao et al. reported strain-driven phase transitions in In2Se3 [30]."
        traces = trace_citations(passage, FULLTEXT, self.local_papers)
        self.assertEqual(traces[0].status, CitationStatus.RESOLVED_LOCAL)
        self.assertEqual(traces[0].resolved_paper_id, 1)

    def test_reference_identified_but_not_in_library(self):
        passage = "Previous work reported unrelated results [29]."
        traces = trace_citations(passage, FULLTEXT, self.local_papers)
        self.assertEqual(traces[0].status, CitationStatus.REFERENCE_IDENTIFIED_BUT_NOT_IN_LIBRARY)
        self.assertIsNone(traces[0].resolved_paper_id)

    def test_unresolved_when_reference_number_has_no_bibliography_entry(self):
        passage = "As previously discussed [99]."
        traces = trace_citations(passage, FULLTEXT, self.local_papers)
        self.assertEqual(traces[0].status, CitationStatus.UNRESOLVED)

    def test_ambiguous_does_not_produce_false_match(self):
        # Two genuinely distinct papers (different DOIs, so paper_identity keeps them
        # separate) that coincidentally share title/author/year. The reference has no
        # DOI, so the title+author+year fallback would match both - it must not guess.
        ambiguous_papers = [
            PaperRecord(1, "P1", "Strain-Driven Phase Transition in In2Se3", ("Zhao",), "2020", "10.1/aaa"),
            PaperRecord(2, "P2", "Strain-Driven Phase Transition in In2Se3", ("Zhao",), "2020", "10.1/bbb"),
        ]
        passage = "Zhao et al. reported strain-driven phase transitions in In2Se3 [30]."
        traces = trace_citations(passage, FULLTEXT, ambiguous_papers)
        self.assertEqual(traces[0].status, CitationStatus.AMBIGUOUS)
        self.assertIsNone(traces[0].resolved_paper_id)

    def test_two_column_bibliography_resolves_correctly(self):
        # End-to-end V0.3.1 regression: a citation to an entry parsed out of
        # the two-column-interleaved fallback bibliography still resolves
        # through the unchanged citation tracer.
        local_papers = [
            PaperRecord(1, "PRIMARY", "Interlayer reconstruction phase transition in van der Waals materials",
                        ("Iota",), "2025", ""),
        ]
        passage = "Prior work reported several related mechanisms [29] [30] [31] [32] [33]."
        traces = trace_citations(passage, TWO_COLUMN_FULLTEXT, local_papers)
        by_number = {t.reference_number: t for t in traces}
        self.assertEqual(by_number[32].status, CitationStatus.RESOLVED_LOCAL)
        self.assertEqual(by_number[32].resolved_paper_id, 1)
        # References with no matching local paper stay conservatively
        # unresolved rather than guessing.
        self.assertEqual(by_number[29].status, CitationStatus.REFERENCE_IDENTIFIED_BUT_NOT_IN_LIBRARY)
        self.assertEqual(by_number[30].status, CitationStatus.REFERENCE_IDENTIFIED_BUT_NOT_IN_LIBRARY)

    def test_no_citation_marker_returns_no_traces(self):
        self.assertEqual(trace_citations("No markers here.", FULLTEXT, self.local_papers), [])

    def test_does_not_match_on_surname_alone(self):
        # Same surname, different paper entirely: title/year differ, so no match.
        other_papers = [
            PaperRecord(1, "P1", "An Unrelated Study of Graphene Oxide", ("Zhao",), "2015", ""),
        ]
        passage = "Zhao et al. reported strain-driven phase transitions in In2Se3 [30]."
        traces = trace_citations(passage, FULLTEXT, other_papers)
        self.assertEqual(traces[0].status, CitationStatus.REFERENCE_IDENTIFIED_BUT_NOT_IN_LIBRARY)

    def test_greek_symbol_title_resolves_real_false_negative_case(self):
        # V0.3.2 regression: real DYWUP9XK [29] case. The bibliography entry
        # (extracted from a PDF) spells the phase symbol out as ASCII
        # "gamma-InSe"; the local Zotero title uses the Greek letter
        # "γ-InSe". No DOI is present on the reference, so resolution
        # depends entirely on the title+author+year fallback matching once
        # the phase symbol is normalized - this must not fall back to fuzzy
        # matching to succeed.
        fulltext = (
            "Body text discussing selenization routes [29].\n\n"
            "References\n"
            "[29] Liu, B. T.; Ge, R. Phase tailoring of In2Se3 toward van der "
            "Waals vertical heterostructures via selenization of gamma-InSe "
            "semiconductor. Small Methods 2025, 9, 2401770.\n"
            "[30] Someone Else. An unrelated study. Journal of Nothing 2019.\n"
        )
        local_papers = [
            PaperRecord(132, "FU9FM3P4",
                        "Phase Tailoring of In2Se3 Toward van der Waals Vertical "
                        "Heterostructures via Selenization of γ-InSe Semiconductor",
                        ("Beituo Liu", "Rui Ge"), "2025", "10.1002/smtd.202401770"),
        ]
        passage = "Selenization routes have been reported for related compounds [29]."
        traces = trace_citations(passage, fulltext, local_papers)
        self.assertEqual(traces[0].status, CitationStatus.RESOLVED_LOCAL)
        self.assertEqual(traces[0].resolved_paper_id, 132)

    def test_greek_symbol_match_does_not_override_conflicting_doi(self):
        # The reference itself carries a DOI that does not match the local
        # paper's DOI: even though titles agree after Greek normalization,
        # the mismatched DOI must not be silently ignored in favor of a
        # title-based guess - it stays unresolved rather than force-matching.
        fulltext = (
            "Body text here.\n\n"
            "References\n"
            "[29] Liu, B. T. Selenization of gamma-InSe semiconductor. "
            "Small Methods 2025, 9, 2401770. https://doi.org/10.1000/different-doi\n"
            "[30] Someone Else. An unrelated study. Journal of Nothing 2019.\n"
        )
        local_papers = [
            PaperRecord(132, "FU9FM3P4", "Selenization of γ-InSe Semiconductor",
                        ("Liu",), "2025", "10.1002/smtd.202401770"),
        ]
        passage = "Prior selenization work has been reported [29]."
        traces = trace_citations(passage, fulltext, local_papers)
        self.assertEqual(traces[0].status, CitationStatus.REFERENCE_IDENTIFIED_BUT_NOT_IN_LIBRARY)
        self.assertIsNone(traces[0].resolved_paper_id)


if __name__ == "__main__":
    unittest.main()
