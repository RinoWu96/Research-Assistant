"""Process entry point for the Research Assistant Core Service.

Startup order (see docs/v0.5-core-service.md):
  1. acquire the single-instance lock; if another Core already holds it,
     report the existing Core (if discoverable) and exit cleanly - never
     launch a second BGE process.
  2. bind the localhost HTTP socket (default port, falling back to an
     OS-assigned port on collision) so `/health` is reachable immediately -
     this never waits on database/index/model initialization.
  3. write the discovery file so callers (the MCP proxy, later the GUI) can
     find the bound port without any manual per-session configuration.
  4. start background resource initialization (state LOADING -> READY/ERROR).
  5. serve until interrupted, then release the lock, discovery file and
     server socket cleanly.
"""

from collections.abc import Callable
import logging
import os
from pathlib import Path
import signal
import sys
from threading import Event, Thread

from app.config import CORE_SERVICE_DEFAULT_PORT, CORE_SERVICE_HOST, DATABASE_PATH
from app.core_service import discovery
from app.core_service.http_api import CoreHTTPServer
from app.core_service.resource_manager import ResourceManager
from app.search.hybrid_search import UnifiedSearchService

logger = logging.getLogger(__name__)


def create_server(database_path: Path = DATABASE_PATH, port: int = CORE_SERVICE_DEFAULT_PORT, *,
                   search_service_factory: Callable[[], UnifiedSearchService] | None = None) -> CoreHTTPServer:
    """Build a bound-but-not-yet-serving Core HTTP server.

    Binds `port` on loopback if available, otherwise an OS-assigned port -
    a collision on the configured default is not treated as "another Core
    is already running" (that is `discovery`'s job, checked separately);
    it just means something else is using that port right now.

    `search_service_factory` is overridable purely for tests - see
    `ResourceManager`.
    """
    resource_manager = ResourceManager(database_path, search_service_factory=search_service_factory)
    try:
        return CoreHTTPServer((CORE_SERVICE_HOST, port), resource_manager)
    except OSError:
        logger.warning("Port %d unavailable on %s; binding an OS-assigned port instead.",
                        port, CORE_SERVICE_HOST)
        return CoreHTTPServer((CORE_SERVICE_HOST, 0), resource_manager)


def run(database_path: Path = DATABASE_PATH, *, stop_event: Event | None = None,
        install_signal_handlers: bool = True) -> int:
    lock = discovery.SingletonLock()
    if not lock.acquire():
        existing = discovery.discover_running_core()
        if existing:
            print(f"Research Assistant Core is already running at {existing}; not starting a second one.")
        else:
            print("Another Research Assistant Core process is starting or already running "
                  "(lock held); not starting a second one.")
        return 0

    server = None
    try:
        server = create_server(database_path)
        actual_port = server.server_address[1]
        discovery.write_discovery_file(actual_port, os.getpid())
        print(f"Research Assistant Core listening on http://{CORE_SERVICE_HOST}:{actual_port}")

        server.resource_manager.start_background_init()

        if install_signal_handlers:
            def _request_shutdown(signum, frame):
                raise KeyboardInterrupt

            signal.signal(signal.SIGINT, _request_shutdown)
            try:
                signal.signal(signal.SIGTERM, _request_shutdown)
            except (AttributeError, ValueError):
                pass
            if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, _request_shutdown)

        if stop_event is not None:
            def _watch_for_stop() -> None:
                stop_event.wait()
                if stop_event.is_set():
                    server.shutdown()

            Thread(target=_watch_for_stop, daemon=True, name="core-stop-watcher").start()

        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            print("Research Assistant Core shutting down.")
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        discovery.remove_discovery_file()
        lock.release()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    sys.exit(run())


if __name__ == "__main__":
    main()
