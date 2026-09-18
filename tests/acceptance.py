"""Read-only acceptance check against the real local index and source PDFs."""

import json
from pathlib import Path
import sys
import time

import pymupdf

from app.config import DATABASE_PATH
from app.search.database import Database
from app.search.search_service import SearchService
from app.search.text import normalize


def main() -> int:
    """Verify repeatability, exact source pages and query evidence without Zotero HTTP."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    query = "strain bandgap In2Se3"
    service = SearchService(DATABASE_PATH)
    durations, baseline = [], None
    for _ in range(10):
        start = time.perf_counter()
        results = service.search(query)
        durations.append(round((time.perf_counter() - start) * 1000, 2))
        identity = [(r.zotero_key, r.attachment_key, r.page) for r in results]
        if baseline is None:
            baseline = identity
        assert identity == baseline, "Search order or page identities changed between runs."
    assert results, "The acceptance query returned no pages."
    for result in results:
        with pymupdf.open(result.pdf_path) as doc:
            assert 1 <= result.page <= len(doc)
            original = doc[result.page - 1].get_text("text", sort=True)
        assert result.text == original, f"Cached source changed: {result.zotero_key}, page {result.page}"
        normalized = normalize(original).lower()
        assert all(term in normalized for term in ("strain", "bandgap", "in2se3")), "Missing page-level query evidence."
    with Database(DATABASE_PATH) as db:
        stats = db.stats()
        db.connection.execute("INSERT INTO pages_fts(pages_fts,rank) VALUES('integrity-check',1)")
    print(json.dumps({"query": query, "status": "PASS", "repeat_runs": 10,
        "query_ms": durations, "stats": stats, "matching_pages": len(results),
        "matching_papers": len({r.zotero_key for r in results}),
        "results": [{"title": r.title, "doi": r.doi, "zotero_key": r.zotero_key,
                     "attachment_key": r.attachment_key, "pdf_page": r.page} for r in results]},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
