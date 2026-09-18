"""MCP server exposing the Research Assistant Core Service as one external
tool, `find_evidence`, for MCP-compatible agents (Claude Code, Codex).
Runnable independently of the PySide6 desktop GUI.

V0.5: this process is a lightweight proxy - it never constructs or imports
the Evidence Engine, the semantic index, or the embedder. Those are owned by
the separate, persistent Research Assistant Core Service (`app.core_service`);
this server only forwards `find_evidence` calls to it over HTTP, via
`app.external_api.mcp_adapter`. Restarting this process never unloads the
embedding model, because the model lives in the Core process, not here.

Read-only: `find_evidence` never modifies Zotero, `data/literature.db`, the
semantic index, or any PDF.

Run directly (the Core Service must be started separately - see
`app.core_service`):

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

from app.core_service import discovery
from app.external_api import mcp_adapter
from app.external_api.mcp_schemas import EvidenceTypeName, FindEvidenceResult

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
    when the Core Service is not currently reachable - that is a
    `find_evidence`-time `ResearchAssistantUnavailableError`, not a reason to
    refuse to start. This probe is a fast, lightweight HTTP health check
    (`app.core_service.discovery`); it never imports the Evidence Engine.
    """
    if discovery.discover_running_core() is None:
        return ["Research Assistant Core Service not detected; find_evidence will fail until it is "
                "started ('.venv\\Scripts\\python.exe -m app.core_service')."]
    return []


def main() -> None:
    # stdio transport uses stdout for protocol messages; logging must go to stderr only.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s", stream=sys.stderr)
    for warning in _startup_environment_warnings():
        logging.warning(warning)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
