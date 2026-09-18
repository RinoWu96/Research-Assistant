"""Read-only, page-preserving PDF extraction without OCR or HTTP access."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
import re
from collections.abc import Callable

import pymupdf


@dataclass(frozen=True)
class PageText:
    paper_id: str
    zotero_key: str
    attachment_key: str
    title: str
    doi: str
    page: int
    text: str


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    pages: tuple[PageText, ...] = ()
    error: str | None = None


def path_from_file_url(value: str) -> Path:
    """Decode an API file URL, including Windows drives, spaces and Unicode."""
    url = urlsplit(value)
    if url.scheme != "file" or url.query or url.fragment:
        raise ValueError("Expected an unambiguous file:// attachment URL.")
    if url.netloc not in {"", "localhost"}:
        raise ValueError("Network file URLs are not supported in V0.1.")
    path = unquote(url.path)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    if not path or "\x00" in path:
        raise ValueError("Attachment file URL has no valid path.")
    result = Path(path)
    if not result.is_absolute():
        raise ValueError("Attachment path must be absolute.")
    return result


def extract_pdf(path: Path, *, paper_id: str, zotero_key: str,
                attachment_key: str, title: str, doi: str,
                cancelled: Callable[[], bool] | None = None) -> ExtractionResult:
    """Extract physical pages numbered from 1; return explicit per-file failures."""
    try:
        if not path.is_file():
            return ExtractionResult("PDF_MISSING", error=f"PDF not found: {path}")
        with pymupdf.open(path) as document:
            if not document.is_pdf:
                return ExtractionResult("PDF_OPEN_FAILED", error="Attachment is not a PDF.")
            if document.needs_pass:
                return ExtractionResult("PDF_ENCRYPTED", error="PDF requires a password.")
            extracted = []
            for index, page in enumerate(document):
                if cancelled and cancelled():
                    return ExtractionResult("CANCELLED", error="Extraction cancelled.")
                extracted.append(PageText(paper_id, zotero_key, attachment_key, title, doi,
                                         index + 1, page.get_text("text", sort=True)))
            pages = tuple(extracted)
        if not any(page.text.strip() for page in pages):
            return ExtractionResult("TEXT_EXTRACTION_FAILED", pages, "PDF contains no extractable text; OCR is disabled.")
        return ExtractionResult("OK", pages)
    except (OSError, RuntimeError, ValueError) as exc:
        return ExtractionResult("PDF_OPEN_FAILED", error=str(exc))


def render_page(path: Path, page_number: int, expected_text: str | None = None) -> bytes:
    """Render one physical PDF page as PNG without changing the document."""
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ValueError("PDF requires a password.")
        if not 1 <= page_number <= len(document):
            raise ValueError("The PDF changed: this page number no longer exists.")
        if expected_text is not None and document[page_number - 1].get_text("text", sort=True) != expected_text:
            raise ValueError("PDF content changed since indexing. Update the index before previewing this page.")
        return document[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4), alpha=False).tobytes("png")
