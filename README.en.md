# Research Assistant V0.7.2

[中文](README.md) | **English**

A local, read-only, traceable research-evidence assistant for Zotero on Windows.
It retrieves original text from your local PDFs and keeps the Zotero item,
attachment, DOI and physical page number for every passage. Evidence is
classified as DIRECT, SECONDARY, LIMITING, CONTEXTUAL or UNCERTAIN.

The stable V0.7.2 architecture:

```text
GUI / Codex MCP / future clients -> Persistent Core -> Evidence Engine -> local literature
```

The Persistent Core is the only owner of the Evidence Engine, the FAISS index,
the BGE embedding model and the Evidence Report handle cache. The GUI and MCP
server are thin clients. Every scientific query is read-only.

## Features

- GET-only access through the Zotero Local API. `zotero.sqlite` is never
  opened or modified.
- Per-physical-page PDF extraction, original-text display and page tracing.
- SQLite FTS5 keyword search, BGE semantic search and RRF hybrid retrieval.
- Incremental indexing with content hashing, an explicit Full Rebuild, failure
  isolation and cancellation. The keyword and semantic indexes are updated in
  one step.
- Supporting-information PDFs: an SI saved into a paper's Zotero storage
  folder is indexed with that paper even if it is not registered in Zotero. SI
  passages are flagged as `supplementary` in evidence output.
- Evidence Packs and classified Evidence Reports.
- Citation tracing, conservative paper-identity merging and source
  deduplication.
- `find_evidence_for_claim`, `get_source` and `verify_citation`.
- PySide6 desktop GUI, Persistent Core, and a Claude/Codex MCP interface.
- Windows x64 portable EXE. The Core stays in the system tray. Right-click it
  to open the GUI or exit explicitly.

Research Assistant judges how local evidence relates to a claim. It does not
judge scientific truth, and it never uses web search or a general-purpose LLM
to fill in missing facts.

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.ps1
```

Or start each component separately:

```powershell
.\.venv\Scripts\python.exe -m app.core_service
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m app.external_api.mcp_server
```

In Zotero, enable Settings → Advanced →
**Allow other applications on this computer to communicate with Zotero**.
Zotero must be running while the index is built or updated. Local search
afterwards does not need the Zotero process.

## Windows portable build

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\packaging\build-windows.ps1
# Code-only changes with unchanged dependencies: replace just the three EXEs
.\packaging\build-incremental.ps1
```

The build writes `release/ResearchAssistant-0.7.2-windows-x64.zip`. A prebuilt
ZIP is also attached to the [GitHub release](../../releases/latest). Unzip it
and run `ResearchAssistant.exe`. After the GUI is closed, the Core keeps
running in the Windows notification area. Double-click the tray icon, or
right-click it and choose “打开 Research Assistant” (Open Research Assistant),
to reopen the GUI.

Portable data directory:

```text
%LOCALAPPDATA%\ResearchAssistant\data
```

The environment variable `RESEARCH_ASSISTANT_DATA_DIR` overrides the default
data directory for both the source and portable builds. Keep the EXEs, the
`_internal` folder and the bundled files together in the same unzipped folder.
See the [V0.7.1 release notes](docs/v0.7.1-release.md) and the
[V0.7.2 release notes](docs/v0.7.2-release.md).

## Codex MCP

From source:

```powershell
codex mcp add research-assistant-evidence -- `
  "C:\path\to\research-assistant\.venv\Scripts\python.exe" `
  "C:\path\to\research-assistant\app\external_api\mcp_server.py"
```

Portable build:

```powershell
codex mcp add research-assistant-evidence -- `
  "C:\path\to\ResearchAssistantMCP.exe"
```

The MCP server exposes four tools: `find_evidence`, `find_evidence_for_claim`,
`get_source` and `verify_citation`. The MCP proxy does not load the Evidence
Engine or BGE itself, so a discoverable Core must be running.

## CLI

```powershell
# Incrementally index the whole personal library or one collection
# (also updates the semantic index, as do the GUI incremental update and Full Rebuild)
.\.venv\Scripts\python.exe -m app.cli index
.\.venv\Scripts\python.exe -m app.cli index --collection Strain

# Explicitly re-extract the whole personal library
.\.venv\Scripts\python.exe -m app.cli rebuild

# Search and statistics
.\.venv\Scripts\python.exe -m app.cli search "strain bandgap In2Se3"
.\.venv\Scripts\python.exe -m app.cli stats

# Semantic index only (for example --rebuild after changing the model)
.\.venv\Scripts\python.exe -m app.cli semantic-index
.\.venv\Scripts\python.exe -m app.cli semantic-index --rebuild
```

## Tests and acceptance

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.acceptance
.\.venv\Scripts\python.exe -m tests.desktop_acceptance
.\.venv\Scripts\python.exe -m app.external_api.mcp_diagnostics
```

V0.7.0 release acceptance verified all of the following:

- 253 tests;
- a real 201-paper local library;
- an external Codex MCP workflow;
- the handle lifecycle;
- Core PID stability;
- unchanged SHA-256 of the database and semantic index.

## Data and security boundaries

- The Zotero API is restricted to loopback GET requests, with no environment
  proxy and no cloud fallback.
- PDFs are read-only. Missing, corrupted, encrypted and text-less PDFs are
  reported explicitly.
- `literature.db` and `data/semantic/` are the application's own data, not
  Zotero's database.
- `get_source` only accepts report/evidence handles returned by the Core,
  never arbitrary file paths.
- `verify_citation` checks one known passage. It does not run a new
  literature search.
- The portable ZIP contains no user database, PDFs, model cache, credentials
  or MCP client configuration.

See [docs/architecture.md](docs/architecture.md) for the architecture and
[docs/v0.7-scientific-evidence-tools.md](docs/v0.7-scientific-evidence-tools.md)
for the scientific tool contracts.
