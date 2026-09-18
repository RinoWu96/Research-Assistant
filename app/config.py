"""Application defaults; no Zotero storage path is required."""

from pathlib import Path

ZOTERO_API_URL = "http://localhost:23119/api/"
# Same local service, avoiding slow IPv6 fallback observed on Windows.
ZOTERO_FAST_API_URL = "http://127.0.0.1:23119/api/"
REQUEST_TIMEOUT_SECONDS = 5.0
DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "literature.db"
