"""Application defaults; no Zotero storage path is required.

`APP_DATA_DIR` is the one seam for a future packaged install: development
defaults to the project's own `data/` directory, but `RESEARCH_ASSISTANT_DATA_DIR`
overrides it without any production code needing a developer-specific path.
"""

import os
from pathlib import Path
import sys

ZOTERO_API_URL = "http://localhost:23119/api/"
# Same local service, avoiding slow IPv6 fallback observed on Windows.
ZOTERO_FAST_API_URL = "http://127.0.0.1:23119/api/"
REQUEST_TIMEOUT_SECONDS = 5.0
# Zotero resolves attachment files while listing them; with storage on a
# cloud-synced drive a 100-item page can take well over a minute.
READ_TIMEOUT_SECONDS = 300.0

def _default_app_data_dir() -> Path:
    """Return a stable writable data directory for source and frozen builds.

    A PyInstaller bundle is extracted below ``_MEIPASS`` at runtime, so paths
    derived from ``__file__`` are temporary and must never own the literature
    database. Development keeps the existing repository-local default.
    """
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "ResearchAssistant" / "data"
    return Path(__file__).resolve().parents[1] / "data"


APP_DATA_DIR = Path(os.environ.get("RESEARCH_ASSISTANT_DATA_DIR") or _default_app_data_dir())
DATABASE_PATH = APP_DATA_DIR / "literature.db"

# V0.5 Research Assistant Core Service: localhost-only HTTP API in front of
# the Evidence Engine (see app.core_service). The port is a configurable
# default, not a hard requirement - app.core_service.discovery falls back to
# an OS-assigned port and records whichever one actually got bound, so the
# MCP proxy never needs this value hard-coded either.
CORE_SERVICE_HOST = "127.0.0.1"
CORE_SERVICE_DEFAULT_PORT = int(os.environ.get("RESEARCH_ASSISTANT_CORE_PORT", "8756"))
CORE_DISCOVERY_FILE = APP_DATA_DIR / "core_service.discovery.json"
CORE_LOCK_FILE = APP_DATA_DIR / "core_service.lock"
