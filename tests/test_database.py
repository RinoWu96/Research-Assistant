"""Real SQLite/FTS5 tests against isolated temporary application databases."""

from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from app.parser.pdf_parser import PageText
from app.search.database import Database, DatabaseError
from app.zotero.models import Attachment, Paper


def example_paper(key="P1"):
    return Paper(key, "Strain paper", ("A. Author",), "2024", "Journal", "10.test/1", (), "journalArticle")


def example_attachment(key="A1"):
    return Attachment(key, "PDF", "application/pdf", "paper.pdf", "imported_file", None)


def example_page(text="strain bandgap In₂Se₃", number=1):
    return PageText("P1", "P1", "A1", "Strain paper", "10.test/1", number, text)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "literature.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_fts_replacement_and_failure(self):
        with Database(self.path) as db:
            db.bind_source("test")
            with db.transaction():
                paper_id = db.save_paper(example_paper())
            db.save_attachment(paper_id, example_attachment(), Path("test.pdf"), "1", "OK", None, (example_page(),))
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM pages_fts WHERE pages_fts MATCH 'In2Se3'").fetchone()[0], 1)
            db.save_attachment(paper_id, example_attachment(), Path("test.pdf"), "2", "OK", None, (example_page("replacement", 2),))
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM pages_fts WHERE pages_fts MATCH 'strain'").fetchone()[0], 0)
            self.assertEqual(db.stats()["pages"], 1)
        with Database(self.path) as db:
            self.assertEqual(db.stats()["pages"], 1)
            with self.assertRaises(DatabaseError):
                db.bind_source("different")
            db.save_attachment(paper_id, example_attachment(), None, None, "PDF_MISSING", "Missing")
            self.assertEqual(db.stats()["pages"], 0)
            db.connection.execute("INSERT INTO pages_fts(pages_fts,rank) VALUES('integrity-check',1)")

    def test_atomic_replacement_and_foreign_keys(self):
        with Database(self.path) as db:
            with db.transaction():
                paper_id = db.save_paper(example_paper())
            db.save_attachment(paper_id, example_attachment(), None, "1", "OK", None, (example_page(),))
            with self.assertRaises(DatabaseError):
                db.save_attachment(paper_id, example_attachment(), None, "2", "OK", None,
                                   (example_page("bad", 1), example_page("duplicate", 1)))
            self.assertEqual(db.attachment("A1")["fingerprint"], "1")
            self.assertEqual(db.connection.execute("SELECT text FROM pages").fetchone()[0], "strain bandgap In₂Se₃")
            with self.assertRaises(DatabaseError):
                with db.transaction():
                    db.connection.execute("INSERT INTO pages(attachment_id,page_number,text,search_text) VALUES(999,1,'x','x')")

    def test_reject_zotero_and_unrecognized_database(self):
        with self.assertRaises(DatabaseError):
            Database(Path(self.temp.name) / "zotero.sqlite")
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("CREATE TABLE unrelated(value)")
        with self.assertRaises(DatabaseError):
            Database(self.path)

    def test_v1_migration_preserves_index_rows(self):
        with Database(self.path) as db:
            with db.transaction():
                paper_id = db.save_paper(example_paper())
            db.save_attachment(paper_id, example_attachment(), None, "legacy", "OK", None, (example_page(),))
            with db.transaction():
                db.connection.execute("ALTER TABLE papers DROP COLUMN tags")
                db.connection.execute("ALTER TABLE papers DROP COLUMN item_type")
                db.connection.execute("ALTER TABLE attachments DROP COLUMN content_hash")
                db.connection.execute("PRAGMA user_version=1")
        with Database(self.path) as db:
            self.assertEqual(db.stats(), {"papers": 1, "attachments": 1, "pages": 1})
            self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM pages_fts WHERE pages_fts MATCH 'strain'").fetchone()[0], 1)
            self.assertIsNone(db.attachment("A1")["content_hash"])
