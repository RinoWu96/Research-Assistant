"""External integration boundary.

V0.4-A: a read-only MCP server (`mcp_server`) exposes the existing Evidence
Engine as a `find_evidence` tool for external agents (Claude Code, Codex),
via the `mcp_adapter` boundary. No retrieval, ranking, classification or
citation-tracing logic lives here - only translation to/from the public
MCP schema in `mcp_schemas`.
"""
