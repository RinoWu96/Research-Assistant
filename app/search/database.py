"""Application-owned SQLite index. No Zotero database access."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from app.config import DATABASE_PATH
from app.parser.pdf_parser import PageText
from app.search.text import normalize
from app.zotero.models import LEGACY_SOURCE_IDENTITY, Attachment, Collection, Paper

SCHEMA_VERSION = 2


class DatabaseError(RuntimeError):
    """A local index failure suitable for presentation to the user."""


class ReadOnlyDatabase:
    """Open an existing application index without schema or journal writes."""

    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.path = Path(path).resolve()
        if self.path.name.lower().startswith("zotero.sqlite"):
            raise DatabaseError("The search index must never be Zotero's database.")
        try:
            self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
            self.connection.row_factory = sqlite3.Row
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise DatabaseError(f"Unsupported index schema {version}.")
        except (sqlite3.Error, OSError, DatabaseError) as exc:
            if hasattr(self, "connection"):
                self.connection.close()
            raise DatabaseError(f"Cannot open search index {self.path}: {exc}") from exc

    def __enter__(self) -> "ReadOnlyDatabase":
        return self

    def __exit__(self, *args: object) -> None:
        self.connection.close()


class Database:
    """One connection per worker; attachment replacements are atomic."""

    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.path = Path(path).resolve()
        if self.path.name.lower().startswith("zotero.sqlite"):
            raise DatabaseError("The search index must never be Zotero's database.")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=10)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise DatabaseError(f"Unsupported index schema {version}.")
            tables = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version == 0 and tables:
                raise DatabaseError("Refusing to initialize a nonempty, unrecognized database.")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS papers(
                    id INTEGER PRIMARY KEY, zotero_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL, authors TEXT NOT NULL, year TEXT,
                    journal TEXT NOT NULL, doi TEXT NOT NULL, last_indexed TEXT);
                CREATE TABLE IF NOT EXISTS collections(
                    key TEXT PRIMARY KEY, name TEXT NOT NULL, parent_key TEXT);
                CREATE TABLE IF NOT EXISTS paper_collections(
                    paper_id INTEGER REFERENCES papers(id) ON DELETE CASCADE,
                    collection_key TEXT REFERENCES collections(key) ON DELETE CASCADE,
                    PRIMARY KEY(paper_id, collection_key));
                CREATE TABLE IF NOT EXISTS attachments(
                    id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    zotero_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL, pdf_path TEXT,
                    fingerprint TEXT, status TEXT NOT NULL, error TEXT, last_indexed TEXT);
                CREATE TABLE IF NOT EXISTS pages(
                    id INTEGER PRIMARY KEY,
                    attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK(page_number > 0),
                    text TEXT NOT NULL, search_text TEXT NOT NULL,
                    UNIQUE(attachment_id, page_number));
                CREATE INDEX IF NOT EXISTS attachments_paper ON attachments(paper_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                    search_text, content='pages', content_rowid='id', tokenize='porter unicode61');
                CREATE TRIGGER IF NOT EXISTS pages_insert AFTER INSERT ON pages BEGIN
                    INSERT INTO pages_fts(rowid, search_text) VALUES(new.id, new.search_text);
                END;
                CREATE TRIGGER IF NOT EXISTS pages_delete AFTER DELETE ON pages BEGIN
                    INSERT INTO pages_fts(pages_fts, rowid, search_text)
                    VALUES('delete', old.id, old.search_text);
                END;
                CREATE TRIGGER IF NOT EXISTS pages_update AFTER UPDATE ON pages BEGIN
                    INSERT INTO pages_fts(pages_fts, rowid, search_text)
                    VALUES('delete', old.id, old.search_text);
                    INSERT INTO pages_fts(rowid, search_text) VALUES(new.id, new.search_text);
                END;
            """)
            # Additive migration: preserve all existing papers, pages and FTS rows.
            with self.connection:
                if "tags" not in {r[1] for r in self.connection.execute("PRAGMA table_info(papers)")}:
                    self.connection.execute("ALTER TABLE papers ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
                    self.connection.execute("ALTER TABLE papers ADD COLUMN item_type TEXT NOT NULL DEFAULT 'unknown'")
                if "content_hash" not in {r[1] for r in self.connection.execute("PRAGMA table_info(attachments)")}:
                    self.connection.execute("ALTER TABLE attachments ADD COLUMN content_hash TEXT")
            self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.connection.commit()
        except (sqlite3.Error, OSError, DatabaseError) as exc:
            if hasattr(self, "connection"):
                self.connection.close()
            raise DatabaseError(f"Cannot open search index {self.path}: {exc}") from exc

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *args: object) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit all local changes, or roll them back on failure."""
        try:
            with self.connection:
                yield
        except sqlite3.Error as exc:
            raise DatabaseError(f"Search index operation failed: {exc}") from exc

    def bind_source(self, identity: str) -> None:
        """Reject accidental reuse of an index for a different Zotero instance."""
        row = self.connection.execute("SELECT value FROM meta WHERE key='source'").fetchone()
        if row and row[0] == LEGACY_SOURCE_IDENTITY and identity != LEGACY_SOURCE_IDENTITY:
            # Older Zotero builds sent no Zotero-Server-ID header, so the index was
            # bound to the generic fallback; adopt the real ID instead of rejecting.
            with self.transaction():
                self.connection.execute("UPDATE meta SET value=? WHERE key='source'", (identity,))
            return
        if row and row[0] != identity:
            raise DatabaseError("Zotero source changed. Use a new application database (--database PATH).")
        with self.transaction():
            self.connection.execute("INSERT OR IGNORE INTO meta VALUES('source', ?)", (identity,))

    def save_collections(self, collections: list[Collection]) -> None:
        """Cache collection metadata for offline filtering."""
        with self.transaction():
            self.connection.executemany(
                "INSERT INTO collections VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET name=excluded.name,parent_key=excluded.parent_key WHERE name IS NOT excluded.name OR parent_key IS NOT excluded.parent_key",
                [(c.key, c.name, c.parent_key) for c in collections])

    def collections(self) -> list[Collection]:
        """Return the cached collection list."""
        return [Collection(*row) for row in self.connection.execute(
            "SELECT key,name,parent_key FROM collections ORDER BY name COLLATE NOCASE,key")]

    def save_paper(self, paper: Paper) -> int:
        """Upsert bibliography without changing its stable local identity."""
        self.connection.execute("""INSERT INTO papers(zotero_key,title,authors,year,journal,doi,tags,item_type)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(zotero_key) DO UPDATE SET title=excluded.title,
            authors=excluded.authors,year=excluded.year,journal=excluded.journal,doi=excluded.doi,
            tags=excluded.tags,item_type=excluded.item_type""",
            (paper.key, paper.title, json.dumps(paper.authors, ensure_ascii=False), paper.year, paper.journal,
             paper.doi, json.dumps(sorted(paper.tags), ensure_ascii=False), paper.item_type))
        return self.connection.execute("SELECT id FROM papers WHERE zotero_key=?", (paper.key,)).fetchone()[0]

    def attachment(self, key: str):
        """Return cached attachment state for incremental indexing."""
        return self.connection.execute("SELECT * FROM attachments WHERE zotero_key=?", (key,)).fetchone()

    def save_attachment(self, paper_id: int, attachment: Attachment, path: Path | None,
                        fingerprint: str | None, status: str, error: str | None,
                        pages: tuple[PageText, ...] = (), content_hash: str | None = None) -> None:
        """Replace page text and FTS rows together; failed refreshes lose stale text."""
        stamp = datetime.now(timezone.utc).isoformat()
        with self.transaction():
            self.connection.execute("""INSERT INTO attachments
                (paper_id,zotero_key,title,pdf_path,fingerprint,status,error,last_indexed,content_hash)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(zotero_key) DO UPDATE SET
                paper_id=excluded.paper_id,title=excluded.title,pdf_path=excluded.pdf_path,
                fingerprint=excluded.fingerprint,status=excluded.status,error=excluded.error,
                last_indexed=excluded.last_indexed,content_hash=excluded.content_hash""",
                (paper_id, attachment.key, attachment.title, str(path) if path else None,
                 fingerprint, status, error, stamp, content_hash))
            attachment_id = self.attachment(attachment.key)["id"]
            self.connection.execute("DELETE FROM pages WHERE attachment_id=?", (attachment_id,))
            self.connection.executemany("INSERT INTO pages(attachment_id,page_number,text,search_text) VALUES(?,?,?,?)",
                                        [(attachment_id, p.page, p.text, normalize(p.text)) for p in pages])
            self.connection.execute("UPDATE papers SET last_indexed=? WHERE id=?", (stamp, paper_id))

    def reconcile_collection(self, collection_key: str, paper_keys: list[str]) -> None:
        """Refresh membership only after a completed collection read."""
        with self.transaction():
            wanted = set(paper_keys)
            current = {r[0] for r in self.connection.execute("""SELECT p.zotero_key FROM papers p
                JOIN paper_collections pc ON pc.paper_id=p.id WHERE pc.collection_key=?""", (collection_key,))}
            self.connection.executemany("""DELETE FROM paper_collections WHERE collection_key=?
                AND paper_id=(SELECT id FROM papers WHERE zotero_key=?)""",
                [(collection_key, key) for key in current - wanted])
            self.connection.executemany("""INSERT OR IGNORE INTO paper_collections
                SELECT id, ? FROM papers WHERE zotero_key=?""", [(collection_key, key) for key in wanted - current])

    def stats(self) -> dict[str, int]:
        """Return cached corpus sizes for the desktop and CLI."""
        return {name: self.connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("papers", "attachments", "pages")}
