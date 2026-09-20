"""Packaged stdio entry point for the lightweight Research Assistant MCP proxy."""

import multiprocessing

from app.external_api.mcp_server import main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
