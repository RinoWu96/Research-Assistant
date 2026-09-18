"""Paper-level identity resolution: DOI/title/author/year deduplication (V0.3 Task 1)."""

import unittest

from app.evidence.paper_identity import PaperRecord, normalize_title, resolve_identities


def record(paper_id, zotero_key, title, authors, year, doi):
    return PaperRecord(paper_id, zotero_key, title, tuple(authors), year, doi)


class PaperIdentityTests(unittest.TestCase):
    def test_same_doi_different_attachments_is_one_paper(self):
        # Two Zotero items pointing at the same DOI (e.g. duplicate import with a
        # different attachment) must resolve to a single logical paper.
        records = [
            record(1, "AAAA", "Selective Growth of Ferroelectric In2Se3", ("Zhao",), "2024",
                   "10.1002/adma.202516548"),
            record(2, "BBBB", "Selective Growth of Ferroelectric In2Se3", ("Zhao",), "2024",
                   "10.1002/adma.202516548"),
        ]
        identities = resolve_identities(records)
        self.assertEqual(identities[1].canonical_id, identities[2].canonical_id)
        self.assertEqual(set(identities[1].paper_ids), {1, 2})
        self.assertEqual(set(identities[1].zotero_item_keys), {"AAAA", "BBBB"})

    def test_doi_capitalization_differences_merge(self):
        records = [
            record(1, "AAAA", "Paper", ("A",), "2024", "10.1002/ADMA.202516548"),
            record(2, "BBBB", "Paper", ("A",), "2024", "10.1002/adma.202516548"),
        ]
        identities = resolve_identities(records)
        self.assertEqual(identities[1].canonical_id, identities[2].canonical_id)

    def test_same_title_different_doi_stays_separate(self):
        # Titles can coincidentally match; a differing DOI must never be overridden.
        records = [
            record(1, "AAAA", "Phase transitions in In2Se3", ("Zhao",), "2023", "10.1000/one"),
            record(2, "BBBB", "Phase transitions in In2Se3", ("Chen",), "2024", "10.1000/two"),
        ]
        identities = resolve_identities(records)
        self.assertNotEqual(identities[1].canonical_id, identities[2].canonical_id)

    def test_missing_doi_falls_back_to_title_author_year(self):
        records = [
            record(1, "AAAA", "Strain engineering of In2Se3", ("Zhao Wei", "Chen"), "2022", ""),
            record(2, "BBBB", "Strain Engineering of In2Se3", ("Zhao Wei",), "2022", ""),
        ]
        identities = resolve_identities(records)
        self.assertEqual(identities[1].canonical_id, identities[2].canonical_id)

    def test_missing_doi_title_author_year_merge_survives_surname_ligature(self):
        # V0.3.1: a PDF-extracted surname with a typographic ligature
        # (U+FB00 "ﬀ") must still merge with the clean Zotero-sourced form,
        # matching the ligature-safety already required of title matching.
        records = [
            record(1, "AAAA", "Strain engineering of In2Se3", ("Zhao Eﬀendi",), "2022", ""),
            record(2, "BBBB", "Strain Engineering of In2Se3", ("Zhao Effendi",), "2022", ""),
        ]
        identities = resolve_identities(records)
        self.assertEqual(identities[1].canonical_id, identities[2].canonical_id)

    def test_missing_doi_and_missing_year_does_not_merge_different_papers(self):
        records = [
            record(1, "AAAA", "Strain engineering of In2Se3", ("Zhao",), None, ""),
            record(2, "BBBB", "Curvature effects on graphene", ("Chen",), None, ""),
        ]
        identities = resolve_identities(records)
        self.assertNotEqual(identities[1].canonical_id, identities[2].canonical_id)

    def test_year_mismatch_with_same_doi_still_merges(self):
        records = [
            record(1, "AAAA", "Paper", ("A",), "2023", "10.1002/adma.202516548"),
            record(2, "BBBB", "Paper", ("A",), "2024", "10.1002/adma.202516548"),
        ]
        identities = resolve_identities(records)
        self.assertEqual(identities[1].canonical_id, identities[2].canonical_id)
        self.assertEqual(identities[1].doi, "10.1002/adma.202516548")

    def test_untitled_records_never_merge_on_placeholder_title(self):
        records = [
            record(1, "AAAA", "(Untitled)", (), None, ""),
            record(2, "BBBB", "(Untitled)", (), None, ""),
        ]
        identities = resolve_identities(records)
        self.assertNotEqual(identities[1].canonical_id, identities[2].canonical_id)

    def test_missing_doi_greek_symbol_title_merges_with_spelled_out_form(self):
        # V0.3.2: the real false negative - a cited reference's ASCII
        # transliteration ("gamma-InSe") must merge with the local Zotero
        # title's Greek letter ("γ-InSe") via deterministic symbol
        # normalization, not fuzzy matching.
        records = [
            record(1, "AAAA", "Selenization of γ-InSe Semiconductor", ("Liu",), "2025", ""),
            record(2, "BBBB", "Selenization of gamma-InSe Semiconductor", ("Liu",), "2025", ""),
        ]
        identities = resolve_identities(records)
        self.assertEqual(identities[1].canonical_id, identities[2].canonical_id)

    def test_different_doi_greek_symbol_title_match_stays_separate(self):
        # Requirement 5/6: DOI is the highest-priority identity key. Even when
        # the only textual difference is gamma/γ, two distinct DOIs must
        # never be merged by title normalization alone.
        records = [
            record(1, "AAAA", "Selenization of γ-InSe Semiconductor", ("Liu",), "2025", "10.1/one"),
            record(2, "BBBB", "Selenization of gamma-InSe Semiconductor", ("Liu",), "2025", "10.1/two"),
        ]
        identities = resolve_identities(records)
        self.assertNotEqual(identities[1].canonical_id, identities[2].canonical_id)


class GreekSymbolTitleNormalizationTests(unittest.TestCase):
    def test_gamma_symbol_equals_spelled_out_form(self):
        self.assertEqual(normalize_title("γ-InSe"), normalize_title("gamma-InSe"))

    def test_beta_symbol_equals_spelled_out_form(self):
        self.assertEqual(normalize_title("β-In2Se3"), normalize_title("beta-In2Se3"))

    def test_alpha_delta_kappa_symbols_equal_spelled_out_forms(self):
        self.assertEqual(normalize_title("α-phase"), normalize_title("alpha-phase"))
        self.assertEqual(normalize_title("δ-phase"), normalize_title("delta-phase"))
        self.assertEqual(normalize_title("κ-phase"), normalize_title("kappa-phase"))

    def test_unicode_case_folding_combined_with_greek_normalization(self):
        # Capital Greek letter, casefolded, must still normalize the same as
        # the ASCII spelled-out form in a different case.
        self.assertEqual(normalize_title("Γ-InSe"), normalize_title("GAMMA-InSe"))

    def test_greek_letter_fused_into_larger_token_is_left_untouched(self):
        # Token-aware: a Greek letter with no separator from surrounding
        # letters is part of one larger token, not its own token, and must
        # not be substituted.
        self.assertNotEqual(normalize_title("γInSe phase"), normalize_title("gammaInSe phase"))

    def test_unrelated_word_containing_gamma_substring_is_not_modified(self):
        # "Gammaglobulin" must not be treated as containing the token "gamma".
        self.assertIn("gammaglobulin", normalize_title("Gammaglobulin levels in serum"))

    def test_unrelated_titles_still_do_not_normalize_equal(self):
        self.assertNotEqual(
            normalize_title("γ-InSe Semiconductor Phase Transition"),
            normalize_title("Graphene Oxide Reduction Mechanisms"),
        )


if __name__ == "__main__":
    unittest.main()
