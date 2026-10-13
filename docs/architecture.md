# Research Assistant V0.7.2 architecture

## Runtime topology

```text
Desktop GUI ────────────┐
Codex / Claude MCP ─────┼── HTTP loopback ──> Persistent Core
future local clients ───┘                         │
                                                  ├─ Evidence Engine
                                                  ├─ hybrid retrieval + BGE
                                                  ├─ report-handle cache
                                                  └─ application literature index
                                                           │
                                                           └─ local Zotero PDFs
```

The persistent Core is the only process that owns the Evidence Engine, semantic
index, embedding model, and report cache. GUI and MCP are lightweight clients.
Closing or restarting either client does not unload BGE or invalidate report
handles. Restarting the Core does invalidate its in-memory handles.

## Read-only evidence boundary

Zotero access is GET-only through its loopback Local API. The application never
opens `zotero.sqlite` and never modifies Zotero items or PDFs. Scientific tools
read the application-owned `literature.db` and semantic index without mutating
them. Indexing is an explicit user action and writes only application-owned data.

The public scientific operations are `find_evidence`,
`find_evidence_for_claim`, `get_source`, and `verify_citation`. Source lookup
resolves cached provenance; callers cannot supply arbitrary paths. Verification
checks one exact claim against one stored passage and performs no new search.

## Persistence and identity

`literature.db` contains paper metadata, attachment identity, physical PDF pages,
original text, normalized searchable text, collection membership, and FTS5 rows.
`data/semantic/` contains the FAISS index, embedding cache, and configuration
metadata. Paper identity prefers DOI and conservatively falls back to normalized
title, author, and year; conflicting DOI values never merge.

## Packaged Windows processes

The portable distribution contains separate GUI, Core, and MCP executables. The
Core remains a distinct process and exposes a system-tray menu for opening the GUI
or explicitly stopping the Core. The MCP executable uses stdio and proxies to the
same discovered Core. Packaged mutable data lives under
`%LOCALAPPDATA%\ResearchAssistant\data` unless overridden.

See the version-specific documents in `docs/` for detailed contracts and
acceptance records.
