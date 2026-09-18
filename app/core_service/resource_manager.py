"""Process-wide owner of the heavy Evidence Engine resources.

The Research Assistant Core Service exists so that the local database
connection pattern, the semantic index, and above all the BGE embedder are
each initialized once per Core process and then reused by every
`find_evidence` call - never once per call, and never once per caller
(MCP, GUI, future local-LLM integrations).

`ResourceManager` is the single place that owns that lifecycle:
  - construction is cheap and never touches the database, index or model;
  - `start_background_init()` runs the (possibly slow) checks on a daemon
    thread and returns immediately, so the HTTP API can start serving
    `/health` before initialization finishes (see docs/v0.5-core-service.md);
  - a second `start_background_init()` call is a no-op, so concurrent
    callers can never trigger two loads;
  - `search_service` and `state` are safe to read from any thread, at any
    time, including while initialization is still running elsewhere.
"""

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading

from app.config import DATABASE_PATH
from app.core_service.state import CoreState
from app.core_service.report_cache import ReportCache
from app.search.database import Database
from app.search.hybrid_search import UnifiedSearchService

logger = logging.getLogger(__name__)

SEMANTIC_INDEX_FILES = ("index.faiss", "chunks.sqlite", "index_metadata.json")


@dataclass(frozen=True)
class ResourceStatus:
    state: CoreState
    database_ready: bool
    semantic_index_ready: bool
    embedding_model_ready: bool
    paper_count: int | None
    error: str | None


class ResourceManager:
    def __init__(self, database_path: Path = DATABASE_PATH, *,
                 search_service_factory: Callable[[], UnifiedSearchService] | None = None) -> None:
        self.database_path = Path(database_path)
        # Overridable purely for tests, the same way UnifiedSearchService and
        # EvidenceReportService elsewhere in this codebase accept an injected
        # collaborator - lets a test wire in a `FakeEmbedder`-backed
        # `SemanticIndex` instead of loading the real BGE model.
        self._search_service_factory = search_service_factory or (
            lambda: UnifiedSearchService(self.database_path))
        self._lock = threading.Lock()
        self._state = CoreState.STARTING
        self._init_started = False
        self._database_ready = False
        self._semantic_index_ready = False
        self._embedding_model_ready = False
        self._paper_count: int | None = None
        self._error: str | None = None
        self._search_service: UnifiedSearchService | None = None
        self.report_cache = ReportCache(
            ttl_seconds=float(os.environ.get("RESEARCH_ASSISTANT_REPORT_TTL_SECONDS", "3600")),
            max_reports=int(os.environ.get("RESEARCH_ASSISTANT_REPORT_MAX_COUNT", "100")),
        )

    @property
    def state(self) -> CoreState:
        with self._lock:
            return self._state

    @property
    def search_service(self) -> UnifiedSearchService | None:
        """The shared `UnifiedSearchService`, or `None` until `state` is READY."""
        with self._lock:
            return self._search_service

    def status(self) -> ResourceStatus:
        with self._lock:
            return ResourceStatus(self._state, self._database_ready, self._semantic_index_ready,
                                   self._embedding_model_ready, self._paper_count, self._error)

    def start_background_init(self) -> threading.Thread | None:
        """Start the one-time load on a daemon thread; a second call is a no-op."""
        with self._lock:
            if self._init_started:
                return None
            self._init_started = True
            self._state = CoreState.LOADING
        thread = threading.Thread(target=self._run_init, name="core-service-init", daemon=True)
        thread.start()
        return thread

    def _run_init(self) -> None:
        # Exception details (which may include local filesystem paths) go
        # only to the log; `/status` gets a short, path-free summary - this
        # is a local API, but section 12 still asks not to expose filesystem
        # details unnecessarily.
        try:
            try:
                with Database(self.database_path) as db:
                    (paper_count,) = db.connection.execute("SELECT COUNT(*) FROM papers").fetchone()
            except Exception as exc:
                raise RuntimeError("Local literature database is unavailable.") from exc
            with self._lock:
                self._database_ready = True
                self._paper_count = paper_count

            semantic_dir = self.database_path.parent / "semantic"
            if not all((semantic_dir / name).is_file() for name in SEMANTIC_INDEX_FILES):
                raise RuntimeError("Semantic index is missing or incomplete.")
            with self._lock:
                self._semantic_index_ready = True

            try:
                search_service = self._search_service_factory()
                # The expensive, highly variable step (see
                # docs/v0.5-core-service.md): touching `.dimension` is what
                # actually triggers the local sentence-transformers model
                # load, done here exactly once.
                search_service.semantic.index.embedder.dimension
            except Exception as exc:
                raise RuntimeError("Embedding model failed to load.") from exc
            with self._lock:
                self._embedding_model_ready = True
                self._search_service = search_service
                self._state = CoreState.READY
        except Exception as exc:
            logger.error("Research Assistant Core Service initialization failed.", exc_info=True)
            with self._lock:
                self._error = str(exc)
                self._state = CoreState.ERROR
