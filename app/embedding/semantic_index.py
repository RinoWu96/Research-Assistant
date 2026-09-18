"""Persistent cached embeddings plus an atomically replaced FAISS search index."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Event
import uuid

from app.config import DATABASE_PATH
from app.embedding.chunker import PageSource, chunk_pages
from app.embedding.embedder import DEFAULT_MODEL, LocalEmbedder

CHUNKING_VERSION = "page-words-v1"
DEFAULT_TARGET_WORDS = 350
DEFAULT_OVERLAP_WORDS = 75
SEMANTIC_SCHEMA_VERSION = 1


class SemanticIndexError(RuntimeError):
    """Persistent semantic-index error suitable for CLI or later GUI display."""


class SemanticRebuildRequired(SemanticIndexError):
    """The configured model/chunker cannot safely use existing embeddings."""


@dataclass
class SemanticUpdateSummary:
    papers: int = 0
    new_chunks: int = 0
    updated_chunks: int = 0
    skipped_chunks: int = 0
    failed_chunks: int = 0
    removed_chunks: int = 0
    new_papers: int = 0
    updated_papers: int = 0
    metadata_only_papers: int = 0
    skipped_papers: int = 0
    failed_papers: int = 0
    removed_papers: int = 0
    cancelled: bool = False
    faiss_rebuilt: bool = False

    def describe(self) -> str:
        return (f"{'Cancelled' if self.cancelled else 'Completed'}: {self.papers} papers; "
                f"chunks: {self.new_chunks} new, {self.updated_chunks} updated, "
                f"{self.skipped_chunks} skipped, {self.failed_chunks} failed, "
                f"{self.removed_chunks} removed; papers: {self.new_papers} new, "
                f"{self.updated_papers} content-updated, {self.metadata_only_papers} metadata-only, "
                f"{self.skipped_papers} skipped, {self.failed_papers} failed, "
                f"{self.removed_papers} removed")


class SemanticIndex:
    """Incrementally cache embeddings and rebuild FAISS from cached valid vectors."""

    def __init__(self, literature_db: Path = DATABASE_PATH, semantic_dir: Path | None = None,
                 embedder: LocalEmbedder | None = None, *, target_words: int = DEFAULT_TARGET_WORDS,
                 overlap_words: int = DEFAULT_OVERLAP_WORDS) -> None:
        self.literature_db = Path(literature_db).resolve()
        self.directory = Path(semantic_dir or self.literature_db.parent / "semantic").resolve()
        self.cache_path = self.directory / "chunks.sqlite"
        self.index_path = self.directory / "index.faiss"
        self.metadata_path = self.directory / "index_metadata.json"
        self.embedder = embedder or LocalEmbedder()
        self.target_words = target_words
        self.overlap_words = overlap_words

    @property
    def expected_configuration(self) -> dict[str, object]:
        return {
            "embedding_model": self.embedder.model_name,
            "embedding_model_version": self.embedder.model_version,
            "embedding_dimension": self.embedder.dimension,
            "chunking_version": CHUNKING_VERSION,
            "target_words": self.target_words,
            "overlap_words": self.overlap_words,
        }

    def _connect_cache(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.cache_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_state(
                paper_id INTEGER PRIMARY KEY,content_signature TEXT NOT NULL,
                metadata_signature TEXT NOT NULL,status TEXT NOT NULL,error TEXT,
                updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY,paper_id INTEGER NOT NULL,page_id INTEGER NOT NULL,
                attachment_id INTEGER NOT NULL,chunk_index INTEGER NOT NULL,
                page_start INTEGER NOT NULL,page_end INTEGER NOT NULL,
                start_char INTEGER NOT NULL,end_char INTEGER NOT NULL,
                chunk_hash TEXT NOT NULL,embedding BLOB NOT NULL,
                UNIQUE(paper_id,chunk_index));
            CREATE INDEX IF NOT EXISTS chunks_paper ON chunks(paper_id);
            PRAGMA user_version=1;
        """)
        return connection

    @staticmethod
    def _config(connection: sqlite3.Connection) -> dict[str, object]:
        return {row[0]: json.loads(row[1]) for row in connection.execute("SELECT key,value FROM config")}

    @staticmethod
    def _mark_dirty(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT OR REPLACE INTO config VALUES('generation',?)", (json.dumps("dirty"),))

    def _check_or_initialize(self, connection: sqlite3.Connection, *, rebuild: bool) -> dict[str, object]:
        expected = self.expected_configuration
        actual = self._config(connection)
        incompatible = {key: (actual.get(key), value) for key, value in expected.items()
                        if key in actual and actual[key] != value}
        has_vectors = connection.execute("SELECT EXISTS(SELECT 1 FROM chunks)").fetchone()[0]
        if incompatible and has_vectors and not rebuild:
            details = ", ".join(f"{key}: {old!r} -> {new!r}" for key, (old, new) in incompatible.items())
            raise SemanticRebuildRequired(f"Semantic settings changed ({details}). Run a semantic rebuild.")
        if rebuild:
            with connection:
                connection.execute("DELETE FROM chunks")
                connection.execute("DELETE FROM paper_state")
                connection.execute("DELETE FROM config")
        with connection:
            connection.executemany("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)",
                                   [(key, json.dumps(value)) for key, value in expected.items()])
            connection.execute("INSERT OR IGNORE INTO config VALUES('created_at',?)",
                               (json.dumps(datetime.now(timezone.utc).isoformat()),))
        return expected

    @staticmethod
    def _signatures(connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return connection.execute("""SELECT p.id,p.zotero_key,p.title,p.authors,p.year,p.journal,p.doi,
            p.tags,p.item_type,
            COALESCE((SELECT group_concat(signature,'|') FROM
                (SELECT zotero_key || ':' || COALESCE(content_hash,fingerprint,'') || ':' || status AS signature
                 FROM attachments WHERE paper_id=p.id ORDER BY zotero_key)),'') AS content_parts,
            COALESCE((SELECT group_concat(collection_key,'|') FROM
                (SELECT collection_key FROM paper_collections WHERE paper_id=p.id ORDER BY collection_key)),'') AS collections
            FROM papers p ORDER BY p.id""").fetchall()

    @staticmethod
    def _digest(parts: list[object]) -> str:
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _pages(connection: sqlite3.Connection, paper_id: int) -> list[PageSource]:
        rows = connection.execute("""SELECT g.id AS page_id,p.id AS paper_id,p.zotero_key,
            a.zotero_key AS attachment_key,p.title,p.doi,g.page_number,g.text
            FROM papers p JOIN attachments a ON a.paper_id=p.id AND a.status='OK'
            JOIN pages g ON g.attachment_id=a.id WHERE p.id=?
            ORDER BY a.zotero_key,g.page_number""", (paper_id,)).fetchall()
        return [PageSource(r["paper_id"], r["zotero_key"], r["attachment_key"], r["title"],
                           r["doi"], r["page_number"], r["text"], r["page_id"]) for r in rows]

    def update(self, *, rebuild: bool = False, progress: Callable[[str], None] | None = None,
               cancel: Event | None = None) -> SemanticUpdateSummary:
        """Recompute only new/content-changed paper embeddings, then replace FAISS safely."""
        report = progress or (lambda message: None)
        cancel = cancel or Event()
        summary = SemanticUpdateSummary()
        if not self.literature_db.is_file():
            raise SemanticIndexError(f"Literature database not found: {self.literature_db}")
        source = sqlite3.connect(f"file:{self.literature_db.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        cache = self._connect_cache()
        vector_membership_changed = rebuild
        try:
            self._check_or_initialize(cache, rebuild=rebuild)
            rows = self._signatures(source)
            current_ids = {row["id"] for row in rows}
            cached_ids = {row[0] for row in cache.execute("SELECT paper_id FROM paper_state")}
            removed_ids = cached_ids - current_ids
            if removed_ids:
                marks = ",".join("?" for _ in removed_ids)
                removed_chunks = cache.execute(f"SELECT COUNT(*) FROM chunks WHERE paper_id IN ({marks})", tuple(removed_ids)).fetchone()[0]
                with cache:
                    cache.execute(f"DELETE FROM chunks WHERE paper_id IN ({marks})", tuple(removed_ids))
                    cache.execute(f"DELETE FROM paper_state WHERE paper_id IN ({marks})", tuple(removed_ids))
                    self._mark_dirty(cache)
                summary.removed_papers = len(removed_ids)
                summary.removed_chunks = removed_chunks
                vector_membership_changed = True
            total = len(rows)
            for position, paper in enumerate(rows, 1):
                if cancel.is_set():
                    summary.cancelled = True
                    break
                content_signature = self._digest([paper["content_parts"]])
                metadata_signature = self._digest([paper[k] for k in
                    ("zotero_key", "title", "authors", "year", "journal", "doi", "tags", "item_type", "collections")])
                state = cache.execute("SELECT * FROM paper_state WHERE paper_id=?", (paper["id"],)).fetchone()
                old_count = cache.execute("SELECT COUNT(*) FROM chunks WHERE paper_id=?", (paper["id"],)).fetchone()[0]
                # Classification happens below; this message must not imply that
                # unchanged papers are being embedded again.
                report(f"Checking [{position}/{total}] {paper['title']}")
                if state and state["content_signature"] == content_signature and state["status"] == "OK":
                    if state["metadata_signature"] != metadata_signature:
                        with cache:
                            cache.execute("UPDATE paper_state SET metadata_signature=?,updated_at=? WHERE paper_id=?",
                                (metadata_signature, datetime.now(timezone.utc).isoformat(), paper["id"]))
                        summary.metadata_only_papers += 1
                    else:
                        summary.skipped_papers += 1
                    summary.skipped_chunks += old_count
                    summary.papers += 1
                    continue
                is_new = state is None
                try:
                    chunks = chunk_pages(self._pages(source, paper["id"]), target_words=self.target_words,
                                         overlap_words=self.overlap_words)
                    vectors = self.embedder.embed_texts([chunk.text for chunk in chunks])
                    if cancel.is_set():
                        summary.cancelled = True
                        break
                    attachment_ids = {r["zotero_key"]: r["id"] for r in source.execute(
                        "SELECT id,zotero_key FROM attachments WHERE paper_id=?", (paper["id"],))}
                    records = [(paper["id"], int(chunk.page_id), attachment_ids[chunk.attachment_key],
                                chunk.chunk_index, chunk.page_start, chunk.page_end, chunk.start_char,
                                chunk.end_char, chunk.chunk_id, vectors[index].tobytes())
                               for index, chunk in enumerate(chunks)]
                    now = datetime.now(timezone.utc).isoformat()
                    with cache:
                        cache.execute("DELETE FROM chunks WHERE paper_id=?", (paper["id"],))
                        cache.executemany("""INSERT INTO chunks(paper_id,page_id,attachment_id,chunk_index,
                            page_start,page_end,start_char,end_char,chunk_hash,embedding)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""", records)
                        cache.execute("""INSERT INTO paper_state VALUES(?,?,?,?,?,?)
                            ON CONFLICT(paper_id) DO UPDATE SET content_signature=excluded.content_signature,
                            metadata_signature=excluded.metadata_signature,status='OK',error=NULL,
                            updated_at=excluded.updated_at""",
                            (paper["id"], content_signature, metadata_signature, "OK", None, now))
                        self._mark_dirty(cache)
                    if is_new:
                        summary.new_papers += 1
                        summary.new_chunks += len(chunks)
                    else:
                        summary.updated_papers += 1
                        summary.updated_chunks += len(chunks)
                        summary.removed_chunks += old_count
                    summary.papers += 1
                    vector_membership_changed = True
                except Exception as exc:
                    with cache:
                        cache.execute("DELETE FROM chunks WHERE paper_id=?", (paper["id"],))
                        cache.execute("""INSERT INTO paper_state VALUES(?,?,?,?,?,?)
                            ON CONFLICT(paper_id) DO UPDATE SET content_signature=excluded.content_signature,
                            metadata_signature=excluded.metadata_signature,status='FAILED',error=excluded.error,
                            updated_at=excluded.updated_at""", (paper["id"], content_signature,
                            metadata_signature, "FAILED", str(exc), datetime.now(timezone.utc).isoformat()))
                        if old_count:
                            self._mark_dirty(cache)
                    summary.failed_papers += 1
                    summary.failed_chunks += max(1, old_count)
                    summary.papers += 1
                    vector_membership_changed |= bool(old_count)
                    report(f"Embedding failed for {paper['title']}: {exc}")
            if vector_membership_changed or not self.index_path.exists():
                self._rebuild_faiss(cache, summary)
                summary.faiss_rebuilt = True
            report(summary.describe())
            return summary
        finally:
            source.close()
            cache.close()

    def _rebuild_faiss(self, cache: sqlite3.Connection, summary: SemanticUpdateSummary) -> None:
        """Build from cached vectors and atomically replace FAISS plus JSON metadata."""
        import faiss
        import numpy as np
        configuration = self.expected_configuration
        rows = cache.execute("SELECT id,embedding FROM chunks ORDER BY id").fetchall()
        dimension = int(configuration["embedding_dimension"])
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
        if rows:
            vectors = np.vstack([np.frombuffer(row["embedding"], dtype="float32") for row in rows])
            identifiers = np.asarray([row["id"] for row in rows], dtype="int64")
            index.add_with_ids(vectors, identifiers)
        token = uuid.uuid4().hex
        temporary_index = self.directory / f"index.{token}.tmp"
        temporary_metadata = self.directory / f"metadata.{token}.tmp"
        faiss.write_index(index, str(temporary_index))
        old_metadata = self.read_metadata(required=False) or {}
        metadata = {
            **configuration,
            "index_creation_timestamp": old_metadata.get("index_creation_timestamp", datetime.now(timezone.utc).isoformat()),
            "index_update_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_papers": cache.execute("SELECT COUNT(*) FROM paper_state WHERE status='OK'").fetchone()[0],
            "total_chunks": len(rows),
            "generation": token,
        }
        temporary_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary_index, self.index_path)
        os.replace(temporary_metadata, self.metadata_path)
        with cache:
            cache.execute("INSERT OR REPLACE INTO config VALUES('generation',?)", (json.dumps(token),))

    def read_metadata(self, *, required: bool = True) -> dict[str, object] | None:
        try:
            return json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if required:
                raise SemanticIndexError("Semantic index has not been built. Run semantic-index update.")
            return None
        except (OSError, ValueError) as exc:
            raise SemanticIndexError(f"Semantic index metadata is unreadable: {exc}") from exc

    def validate_for_search(self) -> dict[str, object]:
        metadata = self.read_metadata()
        expected = self.expected_configuration
        incompatible = [key for key, value in expected.items() if metadata.get(key) != value]
        if incompatible:
            raise SemanticRebuildRequired("Semantic index is incompatible; rebuild required: " + ", ".join(incompatible))
        if not self.index_path.is_file() or not self.cache_path.is_file():
            raise SemanticIndexError("Semantic FAISS index or chunk cache is missing.")
        try:
            connection = sqlite3.connect(self.cache_path)
            try:
                generation = connection.execute("SELECT value FROM config WHERE key='generation'").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise SemanticIndexError(f"Semantic chunk cache is corrupted or unreadable: {exc}") from exc
        if not generation or json.loads(generation[0]) != metadata.get("generation"):
            raise SemanticIndexError("Semantic index files are out of sync. Run semantic-index update.")
        return metadata
