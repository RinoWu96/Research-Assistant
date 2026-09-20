"""GUI-side supervisor for the Research Assistant Core Service (V0.6).

`CoreProcessManager` detects an already-running Core, or starts one if
none exists, reusing the exact discovery/single-instance system introduced
in V0.5 (`app.core_service.discovery`) rather than any GUI-owned locking -
the Core's own OS-level lock is what actually prevents a second model
process, even if two launchers race. This class starts the Core if needed;
it never stops it, and it is not responsible for retrying endlessly on
error. Claude/Codex/other MCP clients may still be using the same Core, so
Core shutdown must stay independent of GUI lifetime.

`CoreMonitor` runs `CoreProcessManager` off the GUI thread and reports
lifecycle state back via a Qt signal, using a plain daemon `Thread` - the
same pattern `app.ui.worker.Worker` already relies on for "search" tasks -
because Qt signal emission from a non-Qt thread is safely queued onto the
receiving (GUI) thread automatically.
"""

import logging
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time

from PySide6.QtCore import QObject, Signal

from app.core_service import discovery
from app.core_service.client import CoreClient

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
START_TIMEOUT_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 1.0

# GUI-local pseudo-state: no Core process could be found or started at all
# (distinct from the Core's own ERROR state, which means a Core process IS
# running but failed to initialize - see app.core_service.state.CoreState).
UNAVAILABLE = "UNAVAILABLE"


def _console_python() -> Path:
    """Same convention as app.search.desktop_search._console_python: a
    pythonw.exe parent (no console window) must still launch a console-
    capable interpreter for a detached child process."""
    executable = Path(sys.executable)
    return executable.with_name("python.exe") if executable.name.lower() == "pythonw.exe" else executable


def _core_launch_command() -> tuple[list[str], Path]:
    """Return the Core command for source or packaged execution."""
    if getattr(sys, "frozen", False):
        distribution = Path(sys.executable).resolve().parent
        return [str(distribution / "ResearchAssistantCore.exe")], distribution
    return [str(_console_python()), "-m", "app.core_service"], PROJECT_ROOT


class CoreProcessManager:
    """Detects, or starts, one Core Service; never stops it."""

    def __init__(self) -> None:
        self.base_url: str | None = None
        self.launched_by_us = False

    def ensure_core_running(self) -> str | None:
        """Return a reachable Core's base URL, starting one if none is running.

        Returns None only if no Core could be found and starting one
        failed outright or never became reachable - never starts a second
        Core if discovery already finds a healthy one (V0.6 brief, section 6).
        """
        existing = discovery.discover_running_core()
        if existing:
            logger.info("Research Assistant Core already running at %s.", existing)
            self.base_url = existing
            return existing

        logger.info("No Research Assistant Core detected; starting one.")
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            command, working_directory = _core_launch_command()
            process = subprocess.Popen(
                command, cwd=str(working_directory),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags)
        except OSError:
            logger.exception("Failed to launch the Research Assistant Core process.")
            return None
        self.launched_by_us = True

        # The Core writes its discovery file only after binding its socket
        # (V0.5 startup order), so poll for it rather than assuming a fixed
        # port or a fixed startup delay.
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            base_url = discovery.discover_running_core()
            if base_url:
                logger.info("Research Assistant Core started (pid %s) at %s.", process.pid, base_url)
                self.base_url = base_url
                return base_url
            time.sleep(0.2)
        logger.error("Research Assistant Core did not become reachable within %.0fs of starting.",
                     START_TIMEOUT_SECONDS)
        return None

    def status(self) -> dict | None:
        """Poll `/status` on the discovered Core; None if it is not reachable at all."""
        if not self.base_url:
            return None
        try:
            return CoreClient(base_url=self.base_url).status()
        except Exception:
            logger.debug("Research Assistant Core status check failed.", exc_info=True)
            return None


class CoreMonitor(QObject):
    """Runs `CoreProcessManager` off the GUI thread, reporting state via a Qt signal."""

    state_changed = Signal(str, dict)

    def __init__(self, manager: CoreProcessManager) -> None:
        super().__init__()
        self._manager = manager
        self._stop = Event()

    def start(self) -> None:
        Thread(target=self._run, daemon=True, name="core-monitor").start()

    def stop(self) -> None:
        """Stop reporting further state changes - does not touch the Core process."""
        self._stop.set()

    def _emit(self, state: str, status: dict) -> None:
        if not self._stop.is_set():
            self.state_changed.emit(state, status)

    def _run(self) -> None:
        base_url = self._manager.ensure_core_running()
        if self._stop.is_set():
            return
        if base_url is None:
            self._emit(UNAVAILABLE, {})
            return
        while not self._stop.is_set():
            status = self._manager.status()
            if status is None:
                self._emit(UNAVAILABLE, {})
                return
            self._emit(status["state"], status)
            if status["state"] in ("READY", "ERROR"):
                return
            self._stop.wait(POLL_INTERVAL_SECONDS)
