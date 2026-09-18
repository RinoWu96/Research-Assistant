"""Ranked page-level keyword search over the application's SQLite index."""

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from app.config import DATABASE_PATH
from app.search.database import DatabaseError, ReadOnlyDatabase
from app.search.text import query_terms, source_snippet


@dataclass(frozen=True)
class SearchResult:
    page_id: int
    zotero_key: str
    attachment_key: str
    title: str
    authors: tuple[str, ...]
    year: str | None
    journal: str
    doi: str
    page: int
    snippet: str
    text: str
    pdf_path: str
    score: float
    paper_id: int = 0
    attachment_id: int = 0


class SearchService:
    """Open a fresh connection per request so the service is worker-safe."""

    def __init__(self, database_path: Path = DATABASE_PATH) -> None:
        self.database_path = database_path

    def search(self, query: str, *, collection: str | None = None,
               year: str | None = None, year_min: int | None = None,
               year_max: int | None = None, journal: str | None = None,
               paper_id: int | None = None,
               limit: int = 100, offset: int = 0) -> list[SearchResult]:
        """Require every literal keyword on a page; rank by BM25 with stable ties."""
        terms = query_terms(query)
        if not terms:
            return []
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Invalid search result range.")
        expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        where = ["pages_fts MATCH ?", "a.status='OK'"]
        params: list[object] = [expression]
        if collection:
            where.append("EXISTS(SELECT 1 FROM paper_collections pc WHERE pc.paper_id=p.id AND pc.collection_key=?)")
            params.append(collection)
        if year:
            where.append("p.year=?")
            params.append(year)
        if year_min is not None:
            where.append("CAST(p.year AS INTEGER)>=?")
            params.append(year_min)
        if year_max is not None:
            where.append("CAST(p.year AS INTEGER)<=?")
            params.append(year_max)
        if journal:
            where.append("p.journal=? COLLATE NOCASE")
            params.append(journal)
        if paper_id is not None:
            where.append("p.id=?")
            params.append(paper_id)
        params.extend((limit, offset))
        sql = """SELECT g.id AS page_id,p.id AS paper_id,a.id AS attachment_id,p.zotero_key,a.zotero_key AS attachment_key,
            p.title,p.authors,p.year,p.journal,p.doi,g.page_number,g.text,a.pdf_path,
            bm25(pages_fts) AS score FROM pages_fts
            JOIN pages g ON g.id=pages_fts.rowid JOIN attachments a ON a.id=g.attachment_id
            JOIN papers p ON p.id=a.paper_id WHERE """ + " AND ".join(where) + " ORDER BY score,p.zotero_key,a.zotero_key,g.page_number LIMIT ? OFFSET ?"
        try:
            with ReadOnlyDatabase(self.database_path) as db:
                rows = db.connection.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Search failed: {exc}") from exc
        return [SearchResult(r["page_id"], r["zotero_key"], r["attachment_key"], r["title"],
                             tuple(json.loads(r["authors"])), r["year"], r["journal"], r["doi"],
                             r["page_number"], source_snippet(r["text"], terms), r["text"],
                             r["pdf_path"], r["score"], r["paper_id"], r["attachment_id"]) for r in rows]
