"""Read-only developer diagnostics for the `find_evidence` MCP server.

This is NOT the MCP stdio transport process (see `app.external_api.mcp_server`)
and must never be pointed at by an MCP client config - it prints
human-readable status to stdout, which would corrupt stdio protocol traffic.

Run directly:

    .venv\\Scripts\\python.exe -m app.external_api.mcp_diagnostics

Every check is read-only: it never creates, modifies, or deletes
`data/literature.db`, the semantic index, or any PDF. Exit code is 0 if every
check passes, 1 otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_INDEX_FILES = ("index.faiss", "chunks.sqlite", "index_metadata.json")

# requirements.txt package name -> importable module name.
REQUIRED_MODULES = {
    "mcp": "mcp",
    "httpx": "httpx",
    "PyMuPDF": "fitz",
    "PySide6": "PySide6",
    "sentence-transformers": "sentence_transformers",
    "faiss-cpu": "faiss",
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def check_python() -> CheckResult:
    return CheckResult("Python executable", True, f"{sys.executable} ({sys.version.split()[0]})")


def check_project_root() -> CheckResult:
    app_dir = PROJECT_ROOT / "app"
    ok = app_dir.is_dir()
    detail = str(PROJECT_ROOT) if ok else f"{PROJECT_ROOT} (no 'app' package found here)"
    return CheckResult("Project root", ok, detail)


def check_dependencies() -> list[CheckResult]:
    results = []
    for package_name, module_name in REQUIRED_MODULES.items():
        try:
            module = __import__(module_name)
            version = getattr(module, "__version__", "installed")
            results.append(CheckResult(f"Dependency: {package_name}", True, f"{module_name} {version}"))
        except Exception as exc:
            results.append(CheckResult(f"Dependency: {package_name}", False, f"{module_name}: {exc}"))
    return results


def check_mcp_server_imports() -> CheckResult:
    try:
        import app.external_api.mcp_server  # noqa: F401
    except Exception as exc:
        return CheckResult("app.external_api.mcp_server imports", False, repr(exc))
    return CheckResult("app.external_api.mcp_server imports", True, "ok")


def check_evidence_report_service_imports() -> CheckResult:
    try:
        from app.evidence.report import EvidenceReportService  # noqa: F401
    except Exception as exc:
        return CheckResult("EvidenceReportService imports", False, repr(exc))
    return CheckResult("EvidenceReportService imports", True, "ok")


def check_database(database_path: Path) -> CheckResult:
    if not database_path.exists():
        return CheckResult("literature.db", False, f"not found at {database_path}")
    try:
        # mode=ro: fails outright rather than creating/locking the file, unlike
        # app.search.database.Database's normal read-write connection.
        uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            (paper_count,) = connection.execute("SELECT COUNT(*) FROM papers").fetchone()
    except Exception as exc:
        return CheckResult("literature.db", False, f"found at {database_path} but unreadable: {exc}")
    return CheckResult("literature.db", True, f"{database_path} ({paper_count} papers)")


def check_semantic_index(semantic_dir: Path) -> CheckResult:
    missing = [name for name in SEMANTIC_INDEX_FILES if not (semantic_dir / name).is_file()]
    if missing:
        return CheckResult("Semantic index", False, f"missing {missing} in {semantic_dir}")
    return CheckResult("Semantic index", True, str(semantic_dir))


def check_find_evidence(database_path: Path, database_ok: bool, semantic_ok: bool) -> CheckResult:
    """Run one real (read-only) query through the actual Evidence Engine.

    Uses `EvidenceReportService(database_path)` directly, rather than going
    through `mcp_adapter.find_evidence`, so this always exercises the same
    `database_path` this diagnostic run checked - not whatever
    `app.config.DATABASE_PATH` happens to resolve to.
    """
    if not (database_ok and semantic_ok):
        return CheckResult("find_evidence smoke call", False, "skipped: database or semantic index unavailable")
    try:
        from app.evidence.report import EvidenceReportService
        report = EvidenceReportService(database_path).build_report("diagnostic connectivity check")
    except Exception as exc:
        return CheckResult("find_evidence smoke call", False, repr(exc))
    return CheckResult("find_evidence smoke call", True,
                        f"ran; {report.candidate_count} candidate passage(s) retrieved")


def run_all(*, database_path: Path | None = None) -> list[CheckResult]:
    """Run every diagnostic check and return the results, most fundamental first.

    `database_path` is overridable purely for testing this module's success
    and failure reporting without touching the real local data. The semantic
    index is always looked up next to it, at `database_path.parent / "semantic"`
    - the same convention `app.embedding.semantic_index.SemanticIndex` uses.
    """
    results = [check_python(), check_project_root()]
    results.extend(check_dependencies())
    results.append(check_mcp_server_imports())
    results.append(check_evidence_report_service_imports())

    if database_path is None:
        from app.config import DATABASE_PATH
        database_path = DATABASE_PATH
    semantic_dir = database_path.parent / "semantic"

    database_result = check_database(database_path)
    semantic_result = check_semantic_index(semantic_dir)
    results.append(database_result)
    results.append(semantic_result)
    results.append(check_find_evidence(database_path, database_result.ok, semantic_result.ok))
    return results


def main(argv: list[str] | None = None) -> int:
    results = run_all()
    width = max(len(r.name) for r in results)
    for result in results:
        status = "OK  " if result.ok else "FAIL"
        print(f"[{status}] {result.name.ljust(width)}  {result.detail}")

    failed = [r for r in results if not r.ok]
    print()
    if failed:
        print(f"{len(failed)} of {len(results)} checks failed.")
        return 1
    print(f"All {len(results)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
