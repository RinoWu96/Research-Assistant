"""Temporary FAISS proof of concept over a small existing SQLite subset."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from app.config import DATABASE_PATH
from app.embedding.chunker import Chunk, PageSource, chunk_pages
from app.embedding.embedder import DEFAULT_MODEL, LocalEmbedder


def select_pages(database: Path, paper_limit: int) -> list[PageSource]:
    """Select 5-10 relevant papers deterministically without changing SQLite."""
    if not 5 <= paper_limit <= 10:
        raise ValueError("paper_limit must be between 5 and 10.")
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        papers = connection.execute("""SELECT p.id FROM papers p
            JOIN attachments a ON a.paper_id=p.id AND a.status='OK'
            JOIN pages g ON g.attachment_id=a.id
            GROUP BY p.id
            ORDER BY
              CASE WHEN lower(p.title) LIKE '%curvature%' THEN 0
                   WHEN lower(p.title) LIKE '%bend%' THEN 1
                   WHEN lower(p.title) LIKE '%strain%' THEN 2 ELSE 3 END,
              p.zotero_key
            LIMIT ?""", (paper_limit,)).fetchall()
        ids = [row[0] for row in papers]
        if len(ids) < 5:
            raise RuntimeError("The literature index has fewer than five readable papers.")
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(f"""SELECT p.id AS paper_id,p.zotero_key,a.zotero_key AS attachment_key,
            p.title,p.doi,g.page_number,g.text FROM papers p
            JOIN attachments a ON a.paper_id=p.id AND a.status='OK'
            JOIN pages g ON g.attachment_id=a.id
            WHERE p.id IN ({placeholders}) ORDER BY p.id,a.zotero_key,g.page_number""", ids).fetchall()
        return [PageSource(row["paper_id"], row["zotero_key"], row["attachment_key"],
                           row["title"], row["doi"], row["page_number"], row["text"])
                for row in rows]
    finally:
        connection.close()


def build_temporary_index(chunks: list[Chunk], embedder: LocalEmbedder, directory: Path) -> tuple[Path, Path]:
    """Embed source chunks and persist a disposable cosine-similarity FAISS index."""
    import faiss
    vectors = embedder.embed_texts([chunk.text for chunk in chunks])
    index = faiss.IndexFlatIP(embedder.dimension)
    index.add(vectors)
    index_path = directory / "index.faiss"
    metadata_path = directory / "metadata.json"
    faiss.write_index(index, str(index_path))
    metadata_path.write_text(json.dumps({
        "temporary": True,
        "model_name": embedder.model_name,
        "model_version": embedder.model_version,
        "dimension": embedder.dimension,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "chunks": [{
            "chunk_id": c.chunk_id, "paper_id": c.paper_id, "zotero_key": c.zotero_key,
            "attachment_key": c.attachment_key, "title": c.title, "doi": c.doi,
            "page_start": c.page_start, "page_end": c.page_end,
            "chunk_index": c.chunk_index,
        } for c in chunks],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_path, metadata_path


def search(index_path: Path, chunks: list[Chunk], embedder: LocalEmbedder,
           query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
    """Retrieve original chunks; FAISS supplies IDs and scores only."""
    import faiss
    index = faiss.read_index(str(index_path))
    scores, identifiers = index.search(embedder.embed_text(query, is_query=True)[None, :], top_k)
    return [(chunks[int(identifier)], float(score)) for identifier, score in zip(identifiers[0], scores[0])
            if identifier >= 0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="curvature induced strain")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--papers", type=int, default=10, choices=range(5, 11))
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    try:
        pages = select_pages(args.database, args.papers)
        chunks = chunk_pages(pages)
        print(f"Selected {len({p.paper_id for p in pages})} papers, {len(pages)} pages, {len(chunks)} chunks.")
        print(f"Loading local CPU model on demand: {DEFAULT_MODEL}")
        embedder = LocalEmbedder(batch_size=args.batch_size)
        with tempfile.TemporaryDirectory(prefix="research-assistant-semantic-poc-") as temp:
            index_path, metadata_path = build_temporary_index(chunks, embedder, Path(temp))
            print(f"Temporary FAISS index: {index_path}")
            print(f"Temporary metadata: {metadata_path}")
            print(f"\nQuery: {args.query}\n")
            for rank, (chunk, score) in enumerate(search(index_path, chunks, embedder, args.query), 1):
                pages_label = str(chunk.page_start) if chunk.page_start == chunk.page_end else f"{chunk.page_start}-{chunk.page_end}"
                excerpt = " ".join(chunk.text.split())
                print(f"[{rank}] {chunk.title}\nPDF page: {pages_label} | score: {score:.4f}\n"
                      f"Zotero: {chunk.zotero_key} | DOI: {chunk.doi or '(not provided)'}\n{excerpt}\n")
        print("Temporary index removed after the proof of concept.")
        return 0
    except Exception as exc:
        print(f"Semantic proof of concept failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
