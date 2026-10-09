"""One persistent worker thread for backend tasks; UI objects stay on the GUI thread."""

from PySide6.QtCore import QObject, Signal, Slot
import logging
from threading import Thread


class Worker(QObject):
    finished = Signal(str, object)
    failed = Signal(str, str)
    progress = Signal(str)

    @Slot(str, object)
    def run(self, name: str, task) -> None:
        """Execute a supplied backend operation serially, reporting errors as data."""
        # PyTorch's CPU runtime can stall when first initialized inside a Qt
        # QThread on Windows. A plain Python worker thread avoids that runtime
        # interaction; Qt signals remain safe to emit back to the GUI thread.
        # Index updates also embed new papers for the semantic index.
        if name in {"search", "index"}:
            Thread(target=self._execute, args=(name, task), daemon=True).start()
            return
        self._execute(name, task)

    def _execute(self, name: str, task) -> None:
        try:
            self.finished.emit(name, task(self.progress.emit))
        except Exception as exc:
            logging.exception("Desktop task %s failed", name)
            self.failed.emit(name, str(exc))
