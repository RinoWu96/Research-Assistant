"""Application defaults; no Zotero storage path is required.

`APP_DATA_DIR` is the one seam for a future packaged install: development
defaults to the project's own `data/` directory, but `RESEARCH_ASSISTANT_DATA_DIR`
overrides it without any production code needing a developer-specific path.
"""

import os
from pathlib import Path

ZOTERO_API_URL = "http://localhost:23119/api/"
# Same local service, avoiding slow IPv6 fallback observed on Windows.
ZOTERO_FAST_API_URL = "http://127.0.0.1:23119/api/"
REQUEST_TIMEOUT_SECONDS = 5.0

APP_DATA_DIR = Path(os.environ.get("RESEARCH_ASSISTANT_DATA_DIR")
                     or Path(__file__).resolve().parents[1] / "data")
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
