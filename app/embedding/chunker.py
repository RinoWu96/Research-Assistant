"""Deterministic, page-preserving chunks for scientific PDF text."""

from dataclasses import dataclass
import hashlib
import re
from collections.abc import Iterable


@dataclass(frozen=True)
class PageSource:
    """One original PDF page plus its stable source identity."""

    paper_id: int
    zotero_key: str
    attachment_key: str
    title: str
    doi: str
    page_number: int
    text: str
    page_id: int | None = None


@dataclass(frozen=True)
class Chunk:
    """Original source text and the metadata needed to resolve it later."""

    chunk_id: str
    paper_id: int
    zotero_key: str
    attachment_key: str
    title: str
    doi: str
    page_start: int
    page_end: int
    chunk_index: int
    text: str
    page_id: int | None = None
    start_char: int = 0
    end_char: int = 0


def _chunk_id(page: PageSource, chunk_index: int, text: str) -> str:
    identity = f"{page.paper_id}\0{page.attachment_key}\0{page.page_number}\0{chunk_index}\0{text}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def chunk_pages(pages: Iterable[PageSource], *, target_words: int = 350,
                overlap_words: int = 75) -> list[Chunk]:
    """Split each page independently; ordering and IDs are deterministic.

    Chunks never cross a page boundary. Short pages remain one chunk. Whitespace at
    chunk edges is trimmed, while all displayed text remains a direct source slice.
    """
    if not 300 <= target_words <= 500:
        raise ValueError("target_words must be between 300 and 500.")
    if not 50 <= overlap_words <= 100 or overlap_words >= target_words:
        raise ValueError("overlap_words must be 50-100 and smaller than target_words.")
    chunks: list[Chunk] = []
    per_paper_index: dict[int, int] = {}
    ordered = sorted(pages, key=lambda p: (p.paper_id, p.attachment_key, p.page_number))
    for page in ordered:
        words = list(re.finditer(r"\S+", page.text))
        if not words:
            continue
        start_word = 0
        step = target_words - overlap_words
        while start_word < len(words):
            end_word = min(start_word + target_words, len(words))
            start_char = words[start_word].start()
            end_char = words[end_word - 1].end()
            source_text = page.text[start_char:end_char]
            chunk_index = per_paper_index.get(page.paper_id, 0)
            chunks.append(Chunk(
                chunk_id=_chunk_id(page, chunk_index, source_text),
                paper_id=page.paper_id,
                zotero_key=page.zotero_key,
                attachment_key=page.attachment_key,
                title=page.title,
                doi=page.doi,
                page_start=page.page_number,
                page_end=page.page_number,
                chunk_index=chunk_index,
                text=source_text,
                page_id=page.page_id,
                start_char=start_char,
                end_char=end_char,
            ))
            per_paper_index[page.paper_id] = chunk_index + 1
            if end_word == len(words):
                break
            start_word += step
    return chunks
