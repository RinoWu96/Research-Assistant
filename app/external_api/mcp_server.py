"""V0.4-A: MCP server exposing the existing Research Assistant Evidence Engine
as one external tool, `find_evidence`, for MCP-compatible agents (Claude Code,
Codex). Runnable independently of the PySide6 desktop GUI.

Read-only: `find_evidence` never modifies Zotero, `data/literature.db`, the
semantic index, or any PDF - see `app.external_api.mcp_adapter`, which is the
only module this server calls into for evidence.

Run directly:

    .venv\\Scripts\\python.exe -m app.external_api.mcp_server
"""

import logging
import sys
from pathlib import Path

# Defensive path bootstrap: the installed Claude Code MCP config schema has no
# `cwd` field (only command/args/env), so this process's working directory is
# whatever the client happens to launch it from - not necessarily the project
# root. `-m app.external_api.mcp_server` already needs `app` importable before
# this file runs at all (via cwd or PYTHONPATH), but a direct script
# invocation (`python .../mcp_server.py`) does not put the project root on
# sys.path. Cover that case deterministically from this file's own location.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP

from app.config import DATABASE_PATH
from app.external_api import mcp_adapter
from app.external_api.mcp_schemas import EvidenceTypeName, FindEvidenceResult

SEMANTIC_INDEX_FILES = ("index.faiss", "chunks.sqlite", "index_metadata.json")

mcp = FastMCP(
    name="research-assistant-evidence",
    instructions=(
        "Find evidence for a scientific claim in the user's local Zotero library via the "
        "existing Research Assistant Evidence Engine: hybrid retrieval, passage and "
        "paper-identity deduplication, evidence classification, citation tracing and "
        "ranking. Read-only - never modifies Zotero, the local index, or any PDF."
    ),
)


@mcp.tool()
def find_evidence(
    query: str,
    top_k: int = 10,
    evidence_types: list[EvidenceTypeName] | None = None,
) -> FindEvidenceResult:
    """Find classified, source-traced evidence for a scientific claim in the local Zotero library.

    Runs the existing Research Assistant Evidence Engine (hybrid retrieval, passage and
    paper-identity deduplication, evidence classification, citation tracing, and ranking)
    and returns up to `top_k` ranked evidence items. If `evidence_types` is given, the
    already-classified, already-ranked evidence is filtered to those classes
    (DIRECT, SECONDARY, LIMITING, CONTEXTUAL, UNCERTAIN) before truncating to `top_k`.
    Read-only against the local literature index; never modifies Zotero or any PDF.
    """
    return mcp_adapter.find_evidence(query, top_k=top_k, evidence_types=evidence_types)


def _startup_environment_warnings() -> list[str]:
    """Non-fatal environment checks, reported to stderr only.

    The server still starts (and still exposes `find_evidence` over MCP) even
    when these fail - a missing/incomplete local index is a `find_evidence`-time
    `DatabaseUnavailableError`, not a reason to refuse to start.
    """
    warnings = []
    if not DATABASE_PATH.exists():
        warnings.append(f"literature.db not found at {DATABASE_PATH}; find_evidence will fail until it exists.")
    semantic_dir = DATABASE_PATH.parent / "semantic"
    if not all((semantic_dir / name).is_file() for name in SEMANTIC_INDEX_FILES):
        warnings.append(f"semantic index incomplete at {semantic_dir}; find_evidence will fail until it is built.")
    return warnings


def main() -> None:
    # stdio transport uses stdout for protocol messages; logging must go to stderr only.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s", stream=sys.stderr)
    for warning in _startup_environment_warnings():
        logging.warning(warning)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
