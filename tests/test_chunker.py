"""Deterministic page-boundary chunking tests."""

import unittest

from app.embedding.chunker import PageSource, chunk_pages


class ChunkerTests(unittest.TestCase):
    def page(self, number: int, words: int, paper: int = 1) -> PageSource:
        return PageSource(paper, f"P{paper}", "A1", "Paper", "10.test", number,
                          " ".join(f"p{number}w{i}" for i in range(words)))

    def test_deterministic_overlap_and_page_boundaries(self):
        pages = [self.page(2, 100), self.page(1, 700)]
        first = chunk_pages(pages, target_words=350, overlap_words=75)
        second = chunk_pages(reversed(pages), target_words=350, overlap_words=75)
        self.assertEqual(first, second)
        self.assertEqual([(c.page_start, c.page_end) for c in first], [(1, 1), (1, 1), (1, 1), (2, 2)])
        self.assertEqual([c.chunk_index for c in first], [0, 1, 2, 3])
        self.assertEqual(first[0].text.split()[-75:], first[1].text.split()[:75])
        self.assertTrue(all(c.text in pages[0].text or c.text in pages[1].text for c in first))

    def test_papers_never_merge_and_empty_pages_skip(self):
        chunks = chunk_pages([self.page(1, 310, 2), PageSource(1, "P1", "A", "P", "", 1, "   ")])
        self.assertEqual(len(chunks), 1)
        self.assertEqual((chunks[0].paper_id, chunks[0].chunk_index), (2, 0))

    def test_validates_configuration(self):
        with self.assertRaises(ValueError):
            chunk_pages([], target_words=299)
        with self.assertRaises(ValueError):
            chunk_pages([], overlap_words=49)
