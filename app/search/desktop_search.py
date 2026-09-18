"""Qt-safe desktop adapter for the unified local retrieval backend.

Legacy / retired from the production GUI path as of V0.6: `app.ui.main_window`
no longer imports or constructs `DesktopSearchService` - it uses
`app.ui.core_search_adapter.CoreSearchAdapter` (a thin client of the
persistent Research Assistant Core Service, see `app.core_service`)
instead, so retrieval and the semantic model are owned once by the Core
process rather than reloaded by a fresh per-call subprocess here. This
module is kept only because `tests/test_desktop_search.py` still exercises
it; it is not called from anywhere in the production desktop GUI.
"""

from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Lock

from app.config import DATABASE_PATH
from app.embedding.semantic_index import SemanticIndexError
from app.evidence.models import EvidencePack, EvidencePassage, PaperEvidence
from app.search.hybrid_search import HybridResult, UnifiedSearchService


def _console_python() -> Path:
    executable = Path(sys.executable)
    return executable.with_name("python.exe") if executable.name.lower() == "pythonw.exe" else executable


class DesktopSearchService:
    """Run local semantic inference outside the Qt process on Windows."""

    def __init__(self, database_path: Path = DATABASE_PATH) -> None:
        self.database_path = Path(database_path).resolve()
        self.keyword_service = UnifiedSearchService(self.database_path)
        self._lock = Lock()

    def search(self, query: str, **options) -> list[HybridResult]:
        if options.get("mode", "hybrid") == "keyword":
            return self.keyword_service.search(query, **options)
        response = self._invoke({"operation": "search", "database": str(self.database_path),
                                 "query": query, "options": options})
        results = []
        for item in response["results"]:
            item["authors"] = tuple(item["authors"])
            results.append(HybridResult(**item))
        return results

    def build_evidence_pack(self, query: str, **options) -> EvidencePack:
        response = self._invoke({"operation": "evidence", "database": str(self.database_path),
                                 "query": query, "options": options})
        papers = []
        for paper in response["pack"]["papers"]:
            paper["authors"] = tuple(paper["authors"])
            passages = []
            for passage in paper.pop("passages"):
                passage["authors"] = tuple(passage["authors"])
                passages.append(EvidencePassage(**passage))
            papers.append(PaperEvidence(**paper, passages=tuple(passages)))
        payload = response["pack"]
        return EvidencePack(payload["query"], tuple(papers), payload["candidate_count"],
                            payload["duplicate_passages_removed"])

    def _invoke(self, request: dict) -> dict:
        with self._lock, tempfile.TemporaryDirectory(prefix="research-assistant-search-") as temporary:
            directory = Path(temporary)
            request_path = directory / "request.json"
            response_path = directory / "response.json"
            error_path = directory / "error.log"
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with error_path.open("w", encoding="utf-8") as errors:
                completed = subprocess.run(
                    [str(_console_python()), "-m", "app.search.desktop_search",
                     "--request", str(request_path), "--response", str(response_path)],
                    cwd=str(Path(__file__).resolve().parents[2]), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=errors, creationflags=flags, check=False,
                )
            if completed.returncode or not response_path.is_file():
                detail = error_path.read_text(encoding="utf-8", errors="replace").strip()
                raise SemanticIndexError(
                    f"Local semantic search failed (exit {completed.returncode}): "
                    f"{detail or 'no diagnostic output'}")
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SemanticIndexError(f"Local semantic search returned invalid data: {exc}") from exc
        if "error" in response:
            raise SemanticIndexError(response["error"])
        return response

    def close(self) -> None:
        """One-shot helpers have no persistent process to shut down."""


def _run_request(request_path: Path, response_path: Path) -> int:
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        database = Path(request["database"])
        if request.get("operation") == "evidence":
            from app.evidence.evidence_service import EvidenceService
            pack = EvidenceService(database).build_evidence_pack(request["query"], **request["options"])
            response = {"pack": pack.to_dict()}
        else:
            service = UnifiedSearchService(database)
            results = service.search(request["query"], **request["options"])
            response = {"results": [asdict(result) for result in results]}
    except Exception as exc:
        response = {"error": str(exc)}
    temporary = response_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, response_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args()
    return _run_request(args.request, args.response)


if __name__ == "__main__":
    raise SystemExit(main())
