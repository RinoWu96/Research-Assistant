"""Offline parser tests and explicit live PDF smoke test."""

import argparse
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import pymupdf

from app.parser.pdf_parser import extract_pdf, path_from_file_url, render_page
from app.zotero.client import ZoteroClient, ZoteroError


class ParserTests(unittest.TestCase):
    def extract(self, path: Path):
        return extract_pdf(path, paper_id="local:TEST", zotero_key="TEST",
                           attachment_key="PDF", title="Test paper", doi="10.test/pdf")

    def test_pages_metadata_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test 文献.pdf"
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72, 72), "First page strain")
                doc.new_page()
                doc.new_page().insert_text((72, 72), "Third page bandgap")
                doc.save(path)
            before = path.read_bytes()
            result = self.extract(path)
            self.assertEqual(result.status, "OK")
            self.assertEqual([p.page for p in result.pages], [1, 2, 3])
            self.assertEqual(result.pages[1].text, "")
            self.assertIn("Third page", result.pages[2].text)
            for page in result.pages:
                self.assertEqual((page.paper_id, page.zotero_key, page.attachment_key,
                                  page.title, page.doi),
                                 ("local:TEST", "TEST", "PDF", "Test paper", "10.test/pdf"))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path_from_file_url(path.as_uri()), path)
            self.assertTrue(render_page(path, 1, result.pages[0].text).startswith(b"\x89PNG"))
            with self.assertRaisesRegex(ValueError, "content changed"):
                render_page(path, 1, "outdated text")

    def test_missing_corrupt_empty_and_encrypted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.pdf"
            self.assertEqual(self.extract(path).status, "PDF_MISSING")
            path.write_bytes(b"not a PDF")
            self.assertEqual(self.extract(path).status, "PDF_OPEN_FAILED")
            with pymupdf.open() as doc:
                doc.new_page()
                doc.save(path)
            result = self.extract(path)
            self.assertEqual(result.status, "TEXT_EXTRACTION_FAILED")
            self.assertEqual(result.pages[0].page, 1)
            with pymupdf.open() as doc:
                doc.new_page()
                doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                         owner_pw="owner", user_pw="secret")
            self.assertEqual(self.extract(path).status, "PDF_ENCRYPTED")

    def test_reject_nonlocal_or_relative_urls(self):
        for url in ("https://example.org/a.pdf", "file:relative.pdf", "file://server/a.pdf"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                path_from_file_url(url)


def main() -> int:
    """Read one real PDF selected through Zotero and print its first two pages."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="Strain", help="Collection name or key")
    parser.add_argument("--item", help="Optional Zotero paper key")
    parser.add_argument("--base-url", default="http://localhost:23119/api/")
    args = parser.parse_args()
    try:
        with ZoteroClient(args.base_url) as client:
            client.check_connection()
            collections = client.collections()
            matches = [c for c in collections if c.key == args.collection]
            if not matches:
                matches = [c for c in collections if c.name == args.collection]
            if len(matches) != 1:
                raise ValueError("Collection missing or ambiguous; supply its key.")
            papers = client.collection_items(matches[0].key)
        for paper in papers:
            if args.item and paper.key != args.item:
                continue
            for attachment in paper.attachments:
                if not attachment.local_url:
                    continue
                path = path_from_file_url(attachment.local_url)
                if not path.is_file():
                    print(f"PDF_MISSING: {attachment.key}")
                    continue
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                result = extract_pdf(path, paper_id=f"users/0:{paper.key}",
                                     zotero_key=paper.key, attachment_key=attachment.key,
                                     title=paper.title, doi=paper.doi)
                after = hashlib.sha256(path.read_bytes()).hexdigest()
                if before != after:
                    raise ValueError("Source file changed during verification.")
                print(f"Paper: {paper.title}\nZotero key: {paper.key}\nDOI: {paper.doi}\n"
                      f"Attachment: {attachment.key}\nStatus: {result.status}\n"
                      f"Pages: {len(result.pages)}\nSource SHA-256 unchanged: Yes")
                for page in result.pages[:2]:
                    print(f"\nPage {page.page}:\n{page.text}")
                if result.status == "OK":
                    return 0
                print(result.error)
        print("No readable PDF found in the selected items.", file=sys.stderr)
        return 1
    except (ZoteroError, ValueError, OSError) as exc:
        print(f"PDF test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
