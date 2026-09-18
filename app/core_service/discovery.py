"""Service discovery and single-instance locking for the Core Service.

Two independent pieces of local-only state under `app.config.APP_DATA_DIR`:

  - a lock file (`CORE_LOCK_FILE`), whose OS-level exclusive lock is the
    actual single-instance guard. Locking is atomic, so it closes the race
    a plain health-check-then-bind sequence cannot: two processes starting
    at the same instant cannot both win this lock, even though both could
    otherwise pass a "no live Core found" check before either binds a port.
    The OS releases the lock automatically if the process dies, so a crash
    never leaves a stale lock behind.
  - a discovery file (`CORE_DISCOVERY_FILE`), a small JSON record of which
    host/port/pid the current Core actually bound. This is what lets a
    dynamic (OS-assigned) port work at all, and is how the MCP proxy (or a
    future GUI/local-LLM caller) finds the running Core without any manual
    per-session configuration.

Only loopback addresses are ever written or read here - see
`app.config.CORE_SERVICE_HOST`.
"""

import json
import sys
import time
from pathlib import Path

import httpx

from app.config import CORE_DISCOVERY_FILE, CORE_LOCK_FILE, CORE_SERVICE_HOST


class SingletonLock:
    """An OS-level exclusive lock on `CORE_LOCK_FILE`, held for the process's lifetime."""

    def __init__(self, path: Path = CORE_LOCK_FILE) -> None:
        self.path = Path(path)
        self._file = None

    def acquire(self) -> bool:
        """Try to become the one Core process. Returns False if another already is.

        On Windows, a byte range locked by another handle rejects not just
        `msvcrt.locking()` but a plain `read()`/`write()` of that range too
        (`PermissionError`), so the whole sequence - not just the locking
        call - has to be treated as "someone else holds this lock".
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._file is None:
            return
        try:
            self._file.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._file.close()
            self._file = None


def write_discovery_file(port: int, pid: int, path: Path = CORE_DISCOVERY_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"host": CORE_SERVICE_HOST, "port": port, "pid": pid, "started_at": time.time()}
    path.write_text(json.dumps(payload), encoding="utf-8")


def remove_discovery_file(path: Path = CORE_DISCOVERY_FILE) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_discovery_file(path: Path = CORE_DISCOVERY_FILE) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def base_url_from_record(record: dict) -> str:
    return f"http://{record.get('host', CORE_SERVICE_HOST)}:{record['port']}"


def probe_health(base_url: str, timeout: float = 0.5) -> bool:
    try:
        response = httpx.get(f"{base_url}/health", timeout=timeout)
        return response.status_code == 200 and response.json().get("ok") is True
    except Exception:
        return False


def discover_running_core(path: Path = CORE_DISCOVERY_FILE, timeout: float = 0.5) -> str | None:
    """Return the base URL of a live, healthy Core, or None if none is discoverable."""
    record = read_discovery_file(path)
    if record is None:
        return None
    base_url = base_url_from_record(record)
    return base_url if probe_health(base_url, timeout=timeout) else None
