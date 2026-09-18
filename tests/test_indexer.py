"""Incremental indexing against temporary PDFs and a fake read-only client."""

from dataclasses import replace
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import patch
import os

import pymupdf

from app.search.database import Database
from app.search.indexer import Indexer
from app.search.search_service import SearchService
from app.zotero.models import Collection
from tests.test_database import example_paper, example_attachment


class FakeClient:
    source_identity = "test"

    def __init__(self, papers):
        self.papers = papers

    def check_connection(self):
        pass

    def collections(self):
        return [Collection("C1", "Strain")]

    def collection_items(self, key):
        return self.papers

    def library_items(self):
        return iter(self.papers)


class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "paper.pdf"
        self.write_pdf("strain bandgap In2Se3")
        attachment = replace(example_attachment(), local_url=self.pdf.as_uri())
        self.paper = replace(example_paper(), attachments=(attachment,), collection_keys=("C1",))
        self.client = FakeClient([self.paper])
        self.indexer = Indexer(self.root / "index.db")

    def tearDown(self):
        self.temp.cleanup()

    def write_pdf(self, text):
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), text)
            doc.save(self.pdf)

    def test_index_skip_replace_and_remove(self):
        self.assertEqual(self.indexer.run(client=self.client).indexed, 1)
        self.assertEqual(self.indexer.run(client=self.client).skipped, 1)
        self.write_pdf("entirely different replacement text")
        self.assertEqual(self.indexer.run(client=self.client).updated, 1)
        self.assertEqual(SearchService(self.root / "index.db").search("strain"), [])
        self.client.papers = []
        self.indexer.run(client=self.client)
        with Database(self.root / "index.db") as db:
            self.assertEqual(db.stats()["pages"], 0)

    def test_bad_pdf_isolated_and_cancel_retains_previous_index(self):
        bad = replace(example_attachment("BAD"), local_url=(self.root / "missing.pdf").as_uri())
        self.client.papers = [replace(self.paper, attachments=(bad, *self.paper.attachments))]
        summary = self.indexer.run(client=self.client)
        self.assertEqual((summary.indexed, summary.failed, summary.pdfs_parsed), (0, 1, 1))
        cancel = Event()
        cancel.set()
        summary = self.indexer.run(client=self.client, cancel=cancel)
        self.assertTrue(summary.cancelled)
        self.assertEqual(len(SearchService(self.root / "index.db").search("strain")), 1)

    def test_multiple_attachments_and_membership(self):
        second = replace(self.paper.attachments[0], key="A2")
        self.client.papers = [replace(self.paper, attachments=(*self.paper.attachments, second))]
        result = self.indexer.run("C1", client=self.client)
        self.assertEqual((result.indexed, result.pdfs_parsed), (1, 2))
        hits = SearchService(self.root / "index.db").search("strain", collection="C1")
        self.assertEqual({r.attachment_key for r in hits}, {"A1", "A2"})

    def test_new_items_and_exclusive_progress_counts(self):
        self.indexer.run(client=self.client)
        self.client.papers.append(replace(self.paper, key="P2", attachments=()))
        messages = []
        result = self.indexer.run(client=self.client, progress=messages.append)
        self.assertEqual((result.indexed, result.skipped, result.updated, result.failed), (1, 1, 0, 0))
        self.assertEqual(result.papers, result.indexed + result.skipped + result.updated + result.failed)
        self.assertIn("1 indexed, 1 skipped, 0 updated, 0 failed", messages[-1])

    def test_metadata_only_and_unchanged_do_not_parse_or_replace_pages(self):
        self.indexer.run(client=self.client)
        with Database(self.root / "index.db") as db:
            before = [tuple(r) for r in db.connection.execute("SELECT * FROM pages")]
        self.client.papers = [replace(self.paper, title="Updated title", tags=("new tag",), doi="10.new/doi")]
        with patch("app.search.indexer.extract_pdf", side_effect=AssertionError("Unexpected PDF parsing")):
            result = self.indexer.run(client=self.client)
            self.assertEqual((result.updated, result.pdfs_parsed), (1, 0))
            result = self.indexer.run(client=self.client)
            self.assertEqual((result.skipped, result.updated), (1, 0))
        with Database(self.root / "index.db") as db:
            self.assertEqual(before, [tuple(r) for r in db.connection.execute("SELECT * FROM pages")])
        self.assertEqual(SearchService(self.root / "index.db").search("strain")[0].doi, "10.new/doi")

    def test_content_hash_detects_same_size_and_mtime_change(self):
        self.indexer.run(client=self.client)
        old = self.pdf.stat()
        data = self.pdf.read_bytes()
        self.assertIn(b"73747261696e", data)  # Hex-encoded 'strain' in this fixture.
        self.pdf.write_bytes(data.replace(b"73747261696e", b"737472656573"))
        os.utime(self.pdf, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(self.pdf.stat().st_size, old.st_size)
        result = self.indexer.run(client=self.client)
        self.assertEqual((result.updated, result.pdfs_parsed), (1, 1))
        self.assertEqual(SearchService(self.root / "index.db").search("strain"), [])

    def test_attachment_metadata_and_removal_update_without_reparse(self):
        self.indexer.run(client=self.client)
        self.client.papers = [replace(self.paper, attachments=(replace(self.paper.attachments[0], title="Renamed attachment"),))]
        with patch("app.search.indexer.extract_pdf", side_effect=AssertionError("Unexpected PDF parsing")):
            self.assertEqual(self.indexer.run(client=self.client).updated, 1)
            self.client.papers = [replace(self.paper, attachments=())]
            self.assertEqual(self.indexer.run(client=self.client).updated, 1)
        self.assertEqual(SearchService(self.root / "index.db").search("strain"), [])

    def test_explicit_full_rebuild_reparses_and_preserves_database_identity(self):
        self.indexer.run(client=self.client)
        with Database(self.root / "index.db") as db:
            before = db.attachment("A1")["id"]
        result = self.indexer.rebuild(client=self.client)
        self.assertEqual((result.updated, result.pdfs_parsed, result.skipped), (1, 1, 0))
        with Database(self.root / "index.db") as db:
            self.assertEqual(db.attachment("A1")["id"], before)

    def test_legacy_hash_backfill_never_reparses_unchanged_pdf(self):
        self.indexer.run(client=self.client)
        with Database(self.root / "index.db") as db:
            with db.transaction():
                db.connection.execute("UPDATE attachments SET content_hash=NULL")
        with patch("app.search.indexer.extract_pdf", side_effect=AssertionError("Unexpected legacy reparse")):
            result = self.indexer.run(client=self.client)
            self.assertEqual((result.skipped, result.pdfs_parsed), (1, 0))
