"""Incremental, read-only Zotero ingestion with exclusive item outcome counts."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from threading import Event

import pymupdf

from app.config import DATABASE_PATH, ZOTERO_FAST_API_URL
from app.parser.pdf_parser import extract_pdf, path_from_file_url
from app.search.database import Database, DatabaseError
from app.search.text import NORMALIZER_VERSION
from app.zotero.client import ZoteroClient

logger = logging.getLogger(__name__)


@dataclass
class IndexSummary:
    papers: int = 0
    indexed: int = 0
    skipped: int = 0
    updated: int = 0
    failed: int = 0
    without_pdf: int = 0
    cancelled: bool = False
    pdfs_parsed: int = 0

    def describe(self, state: str | None = None) -> str:
        """Count each completed paper once; PDFs parsed are a separate metric."""
        return (f"{state or ('Cancelled' if self.cancelled else 'Completed')}: {self.papers} items; "
                f"{self.indexed} indexed, {self.skipped} skipped, {self.updated} updated, "
                f"{self.failed} failed; {self.pdfs_parsed} PDFs parsed; "
                f"{self.without_pdf} items without PDF")


def content_digest(path: Path, cancel: Event) -> str | None:
    """Hash the source read-only, with bounded memory and cancellation checks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel.is_set():
                return None
            digest.update(chunk)
    return None if cancel.is_set() else digest.hexdigest()


class Indexer:
    """Update individual records without clearing or rebuilding the database."""

    def __init__(self, database_path: Path = DATABASE_PATH,
                 base_url: str = ZOTERO_FAST_API_URL) -> None:
        self.database_path, self.base_url = database_path, base_url

    def run(self, collection_key: str | None = None, *, force: bool = False,
            progress: Callable[[str], None] | None = None, cancel: Event | None = None,
            client: ZoteroClient | None = None) -> IndexSummary:
        """Incrementally update a scope; force explicitly re-extracts every PDF in it."""
        report = progress or (lambda message: logger.info(message))
        cancel = cancel or Event()
        if client is None:
            with ZoteroClient(self.base_url) as owned:
                return self.run(collection_key, force=force, progress=report, cancel=cancel, client=owned)
        summary = IndexSummary()
        report("Reading Zotero metadata (read-only)…")
        client.check_connection()
        collections = client.collections()
        collection_keys = {c.key for c in collections}
        if collection_key and collection_key not in collection_keys:
            raise ValueError("Selected collection no longer exists. Refresh Zotero collections.")
        with Database(self.database_path) as db:
            db.bind_source(client.source_identity)
            db.save_collections(collections)
            papers = client.collection_items(collection_key) if collection_key else client.library_items()
            total = len(papers) if isinstance(papers, list) else None
            seen: list[str] = []
            for paper in papers:
                if cancel.is_set():
                    summary.cancelled = True
                    break
                seen.append(paper.key)
                report(f"[{summary.papers + 1}{'/' + str(total) if total is not None else ''}] {paper.title} | "
                       + summary.describe("Progress"))
                cached_paper = db.connection.execute("SELECT * FROM papers WHERE zotero_key=?", (paper.key,)).fetchone()
                is_new = cached_paper is None
                metadata = {"title": paper.title, "authors": json.dumps(paper.authors, ensure_ascii=False),
                            "year": paper.year, "journal": paper.journal, "doi": paper.doi,
                            "tags": json.dumps(sorted(paper.tags), ensure_ascii=False), "item_type": paper.item_type}
                metadata_changed = is_new or any(cached_paper[k] != v for k, v in metadata.items())
                old_memberships = set() if is_new else {r[0] for r in db.connection.execute(
                    "SELECT collection_key FROM paper_collections WHERE paper_id=?", (cached_paper["id"],))}
                memberships = set(paper.collection_keys) & collection_keys
                if collection_key:
                    memberships.add(collection_key)
                changed = metadata_changed or memberships != old_memberships
                with db.transaction():
                    paper_id = db.save_paper(paper) if metadata_changed else cached_paper["id"]
                    db.connection.executemany("DELETE FROM paper_collections WHERE paper_id=? AND collection_key=?",
                        [(paper_id, key) for key in old_memberships - memberships])
                    db.connection.executemany("INSERT INTO paper_collections VALUES(?,?)",
                        [(paper_id, key) for key in memberships - old_memberships])
                item_failed = not paper.attachments_complete
                if item_failed:
                    report(f"Attachment listing failed: {paper.key}; previous PDF cache retained.")
                elif not paper.attachments:
                    summary.without_pdf += 1
                for attachment in paper.attachments if paper.attachments_complete else ():
                    if cancel.is_set():
                        summary.cancelled = True
                        break
                    path = fingerprint = digest = None
                    cached = db.attachment(attachment.key)
                    try:
                        if not attachment.local_url:
                            raise ValueError(attachment.location_error or "No local PDF available.")
                        path = path_from_file_url(attachment.local_url)
                        stat = path.stat()
                        fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}:{pymupdf.VersionBind}:{NORMALIZER_VERSION}"
                        digest = content_digest(path, cancel)
                        if digest is None:
                            summary.cancelled = True
                            break
                        after_hash = path.stat()
                        if (stat.st_size, stat.st_mtime_ns) != (after_hash.st_size, after_hash.st_mtime_ns):
                            raise ValueError("PDF changed during verification. Retry after synchronization finishes.")
                        same_content = bool(cached and (cached["content_hash"] == digest or
                            (cached["content_hash"] is None and cached["fingerprint"] == fingerprint)))
                        same_parser = bool(cached and cached["fingerprint"] and
                            cached["fingerprint"].split(":")[-2:] == fingerprint.split(":")[-2:])
                        if not force and cached and cached["status"] == "OK" and same_content and same_parser:
                            attachment_changed = cached["title"] != attachment.title or cached["pdf_path"] != str(path) or cached["paper_id"] != paper_id
                            changed |= attachment_changed
                            # Legacy hash backfill and timestamp-only changes do not reparse PDF text.
                            if attachment_changed or cached["fingerprint"] != fingerprint or cached["content_hash"] != digest:
                                with db.transaction():
                                    db.connection.execute("""UPDATE attachments SET title=?,pdf_path=?,paper_id=?,
                                        fingerprint=?,content_hash=? WHERE id=?""", (attachment.title, str(path),
                                        paper_id, fingerprint, digest, cached["id"]))
                            continue
                        result = extract_pdf(path, paper_id=str(paper_id), zotero_key=paper.key,
                            attachment_key=attachment.key, title=paper.title, doi=paper.doi, cancelled=cancel.is_set)
                        if result.status == "CANCELLED":
                            summary.cancelled = True
                            break
                        final_stat = path.stat()
                        if (stat.st_size, stat.st_mtime_ns) != (final_stat.st_size, final_stat.st_mtime_ns):
                            raise ValueError("PDF changed during extraction. Retry after synchronization finishes.")
                        db.save_attachment(paper_id, attachment, path, fingerprint, result.status,
                                           result.error, result.pages, content_hash=digest)
                        changed = True
                        summary.pdfs_parsed += 1
                        if result.status != "OK":
                            item_failed = True
                            report(f"{result.status}: {paper.title}: {result.error}")
                    except DatabaseError:
                        raise
                    except Exception as exc:
                        logger.warning("PDF %s failed: %s", attachment.key, exc)
                        db.save_attachment(paper_id, attachment, path, fingerprint, "PDF_UNAVAILABLE", str(exc))
                        item_failed = True
                        report(f"PDF unavailable: {paper.title}: {exc}")
                if summary.cancelled:
                    break
                if paper.attachments_complete:
                    valid = {a.key for a in paper.attachments}
                    obsolete = [r[0] for r in db.connection.execute(
                        "SELECT zotero_key FROM attachments WHERE paper_id=?", (paper_id,)) if r[0] not in valid]
                    if obsolete:
                        changed = True
                        with db.transaction():
                            db.connection.executemany("DELETE FROM attachments WHERE zotero_key=?", [(k,) for k in obsolete])
                outcome = "failed" if item_failed else "indexed" if is_new else "updated" if changed or force else "skipped"
                setattr(summary, outcome, getattr(summary, outcome) + 1)
                summary.papers += 1
                if outcome in {"indexed", "updated"}:
                    with db.transaction():
                        db.connection.execute("UPDATE papers SET last_indexed=? WHERE id=?",
                            (datetime.now(timezone.utc).isoformat(), paper_id))
                report(summary.describe("Progress"))
            summary.cancelled |= cancel.is_set()
            if not summary.cancelled:
                if collection_key:
                    db.reconcile_collection(collection_key, seen)
                else:
                    known = set(seen)
                    with db.transaction():
                        obsolete = [r[0] for r in db.connection.execute("SELECT zotero_key FROM papers") if r[0] not in known]
                        db.connection.executemany("DELETE FROM papers WHERE zotero_key=?", [(k,) for k in obsolete])
                        obsolete_collections = [r[0] for r in db.connection.execute("SELECT key FROM collections") if r[0] not in collection_keys]
                        db.connection.executemany("DELETE FROM collections WHERE key=?", [(k,) for k in obsolete_collections])
        report(summary.describe())
        return summary

    def rebuild(self, **kwargs) -> IndexSummary:
        """Explicitly re-extract the whole personal library, preserving stable row IDs."""
        return self.run(force=True, **kwargs)


@dataclass
class LibraryUpdateSummary:
    """Keyword index outcome plus the semantic index update that follows it."""
    index: IndexSummary
    semantic: object | None = None
    semantic_error: str | None = None

    def describe(self) -> str:
        if self.semantic is not None:
            semantic = "Semantic index · " + self.semantic.describe()
        elif self.semantic_error:
            semantic = "Semantic index update failed: " + self.semantic_error
        else:
            semantic = "Semantic index not updated (keyword update cancelled)"
        return self.index.describe() + "\n" + semantic


def update_library(run_index: Callable[[], IndexSummary], database_path: Path = DATABASE_PATH, *,
                   progress: Callable[[str], None] | None = None,
                   cancel: Event | None = None) -> LibraryUpdateSummary:
    """Run a keyword index update, then bring the semantic index up to date from it."""
    report = progress or (lambda message: logger.info(message))
    cancel = cancel or Event()
    summary = LibraryUpdateSummary(run_index())
    if summary.index.cancelled or cancel.is_set():
        return summary
    # Imported lazily: loading the embedding stack is only needed here.
    from app.embedding.semantic_index import SemanticIndex
    report("Updating semantic index…")
    try:
        summary.semantic = SemanticIndex(database_path).update(progress=report, cancel=cancel)
    except Exception as exc:
        logger.warning("Semantic index update failed", exc_info=True)
        summary.semantic_error = str(exc)
    return summary
