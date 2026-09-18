"""V0.5 Research Assistant Core Service.

A persistent, localhost-only process that owns the heavy Evidence Engine
resources (semantic index, BGE embedder, database access) so they are
loaded once and reused across every caller - MCP, the desktop GUI, and
future local-LLM integrations - instead of each caller constructing its
own. See docs/v0.5-core-service.md for the full architecture.
"""
