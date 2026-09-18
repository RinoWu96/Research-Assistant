"""Compare keyword, semantic and RRF hybrid retrieval before GUI integration."""

import argparse
from pathlib import Path
import sqlite3
import statistics
import sys
import time

from app.config import DATABASE_PATH
from app.search.hybrid_search import UnifiedSearchService

QUERIES = (
    "curvature induced strain",
    "bandgap modulation under tensile strain",
    "bending electronic structure",
    "local lattice distortion",
)


def print_chunks(label: str, results) -> None:
    print(f"\n{label}")
    for rank, result in enumerate(results, 1):
        evidence = []
        if result.keyword_rank is not None:
            evidence.append(f"keyword #{result.keyword_rank}")
        if result.semantic_rank is not None:
            evidence.append(f"semantic #{result.semantic_rank} ({result.semantic_score:.4f})")
        print(f"{rank}. {result.title}\n   PDF page {result.page_start} | hybrid {result.hybrid_score:.6f} | "
              f"{', '.join(evidence)}\n   {(' '.join(result.text.split()))[:300]}")


def resolve_collection(database: Path, value: str | None) -> str | None:
    if not value:
        return None
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT key FROM collections WHERE key=? OR name=?", (value, value)).fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        raise ValueError("Collection is missing or ambiguous; use its Zotero key.")
    return rows[0][0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--collection", help="Optional exact collection name or key")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    try:
        collection = resolve_collection(args.database, args.collection)
        service = UnifiedSearchService(args.database)
        timings = {mode: [] for mode in ("keyword", "semantic", "hybrid")}
        for query in QUERIES:
            print(f"\n{'=' * 80}\nQUERY: {query}\nFILTER: {args.collection or 'full indexed library'}")
            for mode in ("keyword", "semantic", "hybrid"):
                started = time.perf_counter()
                results = service.search(query, mode=mode, top_k=args.top_k, collection=collection)
                timings[mode].append((time.perf_counter() - started) * 1000)
                print_chunks(f"{mode.upper()} RESULTS", results)
            papers = service.search(query, mode="hybrid", top_k=args.top_k, collection=collection,
                                    result_level="paper")
            print("\nHYBRID PAPER RESULTS")
            for rank, paper in enumerate(papers, 1):
                print(f"{rank}. {paper.title}\n   score {paper.paper_score:.6f} | best page {paper.best_page} | "
                      f"keyword={paper.keyword_evidence} semantic={paper.semantic_evidence}")
        print("\nLATENCY (ms, median across four queries)")
        for mode, values in timings.items():
            print(f"{mode}: {statistics.median(values):.2f} ms ({', '.join(f'{v:.2f}' for v in values)})")
        return 0
    except Exception as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
