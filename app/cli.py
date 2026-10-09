"""Backend verification commands, independent of PySide6."""

import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import sys

from app.config import DATABASE_PATH, ZOTERO_FAST_API_URL
from app.search.database import Database
from app.search.indexer import Indexer, update_library
from app.search.search_service import SearchService
from app.embedding.semantic_index import SemanticIndex
from app.zotero.client import ZoteroClient


def main() -> int:
    """Index local PDFs or search the existing index without a GUI."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("--collection", help="Collection key or exact name; omit to index the whole personal library")
    index.add_argument("--force", action="store_true")
    sub.add_parser("rebuild", help="Explicitly re-extract every PDF in the entire personal library")
    semantic = sub.add_parser("semantic-index", help="Update only the semantic index (index and rebuild already do this)")
    semantic.add_argument("--rebuild", action="store_true", help="Explicitly discard incompatible cached embeddings")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--collection", help="Collection key")
    search.add_argument("--year")
    search.add_argument("--journal")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")
    sub.add_parser("stats")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        if args.command == "semantic-index":
            summary = SemanticIndex(args.database).update(rebuild=args.rebuild, progress=print)
            return 2 if summary.failed_papers else 0
        if args.command == "rebuild":
            summary = update_library(lambda: Indexer(args.database).rebuild(progress=print),
                                     args.database, progress=print)
            print(summary.describe())
            return 2 if summary.index.failed or summary.semantic_error else 0
        if args.command == "index":
            key = None
            if args.collection:
                with ZoteroClient(ZOTERO_FAST_API_URL) as client:
                    collections = client.collections()
                matches = [c for c in collections if c.key == args.collection]
                if not matches:
                    matches = [c for c in collections if c.name == args.collection]
                if len(matches) != 1:
                    raise ValueError("Collection missing or ambiguous; specify its key.")
                key = matches[0].key
            summary = update_library(lambda: Indexer(args.database).run(key, force=args.force, progress=print),
                                     args.database, progress=print)
            print(summary.describe())
            return 2 if summary.index.failed or summary.semantic_error else 0
        if args.command == "stats":
            with Database(args.database) as db:
                print(json.dumps(db.stats()))
            return 0
        results = SearchService(args.database).search(args.query, collection=args.collection,
                    year=args.year, journal=args.journal, limit=args.limit)
        if args.json:
            print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
        else:
            print(f"{len(results)} page matches (limit {args.limit}); all keywords must occur on the same page.")
            for result in results:
                print(f"\n{result.title}\n{result.year or ''} | {result.journal}\n"
                      f"PDF page {result.page} | DOI: {result.doi or '(not provided)'}\n"
                      f"Zotero: {result.zotero_key} | Attachment: {result.attachment_key}\n{result.snippet}\n")
        return 0
    except Exception as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
