"""Persistent FAISS retrieval resolved back to authoritative SQLite source text."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3

from app.config import DATABASE_PATH
from app.embedding.embedder import LocalEmbedder
from app.embedding.semantic_index import SemanticIndex, SemanticIndexError


@dataclass(frozen=True)
class SemanticResult:
    chunk_id: int
    paper_id: int
    page_id: int
    zotero_key: str
    attachment_key: str
    title: str
    authors: tuple[str, ...]
    year: str | None
    journal: str
    doi: str
    page_start: int
    page_end: int
    chunk_index: int
    text: str
    pdf_path: str
    semantic_score: float


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.lower()))


def _near_duplicate(left: SemanticResult, right: SemanticResult) -> bool:
    if left.paper_id != right.paper_id or left.page_start != right.page_start:
        return False
    a, b = _tokens(left.text), _tokens(right.text)
    return bool(a and b and len(a & b) / len(a | b) >= 0.85)


def diversify(results: list[SemanticResult], top_k: int, max_chunks_per_paper: int) -> list[SemanticResult]:
    """Remove near duplicates, then round-robin papers before second chunks."""
    unique: list[SemanticResult] = []
    for result in results:
        if not any(_near_duplicate(result, previous) for previous in unique):
            unique.append(result)
    groups: dict[int, list[SemanticResult]] = {}
    paper_order: list[int] = []
    for result in unique:
        if result.paper_id not in groups:
            groups[result.paper_id] = []
            paper_order.append(result.paper_id)
        if len(groups[result.paper_id]) < max_chunks_per_paper:
            groups[result.paper_id].append(result)
    output: list[SemanticResult] = []
    for depth in range(max_chunks_per_paper):
        for paper_id in paper_order:
            if depth < len(groups[paper_id]):
                output.append(groups[paper_id][depth])
                if len(output) == top_k:
                    return output
    return output


class SemanticSearchService:
    def __init__(self, literature_db: Path = DATABASE_PATH, semantic_dir: Path | None = None,
                 semantic_index: SemanticIndex | None = None) -> None:
        self.literature_db = Path(literature_db).resolve()
        # A built semantic index implies the model is already cached. Searches
        # must remain local and must not wait for Hugging Face network checks.
        self.index = semantic_index or SemanticIndex(
            self.literature_db, semantic_dir, LocalEmbedder(local_files_only=True))

    @staticmethod
    def _paper_filter_sql(collection: str | None, year_min: int | None,
                          year_max: int | None, journal: str | None,
                          paper_id: int | None = None) -> tuple[str, list[object]]:
        clauses, parameters = [], []
        if collection:
            clauses.append("EXISTS(SELECT 1 FROM paper_collections pc WHERE pc.paper_id=p.id AND pc.collection_key=?)")
            parameters.append(collection)
        if year_min is not None:
            clauses.append("CAST(p.year AS INTEGER)>=?")
            parameters.append(year_min)
        if year_max is not None:
            clauses.append("CAST(p.year AS INTEGER)<=?")
            parameters.append(year_max)
        if journal:
            clauses.append("p.journal=? COLLATE NOCASE")
            parameters.append(journal)
        if paper_id is not None:
            clauses.append("p.id=?")
            parameters.append(paper_id)
        return (" AND " + " AND ".join(clauses) if clauses else ""), parameters

    def search(self, query: str, *, top_k: int = 20, collection: str | None = None,
               year_min: int | None = None, year_max: int | None = None,
               journal: str | None = None, paper_id: int | None = None,
               max_chunks_per_paper: int = 2,
               diversify_results: bool = True) -> list[SemanticResult]:
        if not query.strip():
            return []
        if not 1 <= top_k <= 500 or not 1 <= max_chunks_per_paper <= 20:
            raise ValueError("Invalid semantic result limits.")
        self.index.validate_for_search()
        import faiss
        import numpy as np
        source = sqlite3.connect(f"file:{self.literature_db.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            cache = sqlite3.connect(f"file:{self.index.cache_path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            source.close()
            raise SemanticIndexError(f"Semantic chunk cache is unreadable: {exc}") from exc
        cache.row_factory = sqlite3.Row
        try:
            filter_sql, parameters = self._paper_filter_sql(collection, year_min, year_max, journal, paper_id)
            allowed = [r[0] for r in source.execute("SELECT p.id FROM papers p WHERE 1=1" + filter_sql, parameters)]
            if not allowed:
                return []
            filtered = any(value is not None and value != ""
                          for value in (collection, year_min, year_max, journal, paper_id))
            query_vector = self.index.embedder.embed_text(query, is_query=True)[None, :]
            if filtered:
                marks = ",".join("?" for _ in allowed)
                vector_rows = cache.execute(f"SELECT id,embedding FROM chunks WHERE paper_id IN ({marks}) ORDER BY id", allowed).fetchall()
                if not vector_rows:
                    return []
                local = faiss.IndexIDMap2(faiss.IndexFlatIP(query_vector.shape[1]))
                vectors = np.vstack([np.frombuffer(row["embedding"], dtype="float32") for row in vector_rows])
                local.add_with_ids(vectors, np.asarray([row["id"] for row in vector_rows], dtype="int64"))
                faiss_index = local
            else:
                try:
                    faiss_index = faiss.read_index(str(self.index.index_path))
                except Exception as exc:
                    raise SemanticIndexError(f"FAISS index is corrupted or unreadable: {exc}") from exc
            candidate_count = min(faiss_index.ntotal, max(top_k * max_chunks_per_paper * 8, top_k))
            scores, ids = faiss_index.search(query_vector, candidate_count)
            candidates: list[SemanticResult] = []
            for identifier, score in zip(ids[0], scores[0]):
                if identifier < 0:
                    continue
                chunk = cache.execute("SELECT * FROM chunks WHERE id=?", (int(identifier),)).fetchone()
                if not chunk:
                    continue
                row = source.execute("""SELECT p.zotero_key,a.zotero_key AS attachment_key,p.title,p.authors,
                    p.year,p.journal,p.doi,g.text,a.pdf_path,g.page_number
                    FROM pages g JOIN attachments a ON a.id=g.attachment_id
                    JOIN papers p ON p.id=a.paper_id WHERE g.id=? AND p.id=?""",
                    (chunk["page_id"], chunk["paper_id"])).fetchone()
                if not row:
                    continue
                original = row["text"][chunk["start_char"]:chunk["end_char"]]
                candidates.append(SemanticResult(int(identifier), chunk["paper_id"], chunk["page_id"],
                    row["zotero_key"], row["attachment_key"], row["title"], tuple(json.loads(row["authors"])), row["year"],
                    row["journal"], row["doi"], chunk["page_start"], chunk["page_end"],
                    chunk["chunk_index"], original, row["pdf_path"], float(score)))
            return (diversify(candidates, top_k, max_chunks_per_paper)
                    if diversify_results else candidates[:top_k])
        finally:
            cache.close()
            source.close()
