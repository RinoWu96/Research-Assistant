"""Packaged Windows tray host for the persistent Research Assistant Core."""

import logging
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from app.core_service import discovery
from app.core_service.server import run
from app.version import __version__


class CoreTrayController(QObject):
    core_finished = Signal(int)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.stop_event = Event()
        self._quitting = False

        icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip(f"Research Assistant Core {__version__} · starting")
        menu = QMenu()
        self.open_action = QAction("打开 Research Assistant", menu)
        self.open_action.triggered.connect(self.open_gui)
        menu.addAction(self.open_action)
        self.status_action = QAction("Core · starting", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()
        quit_action = QAction("退出 Core", menu)
        quit_action.triggered.connect(self.quit_core)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()

        self.core_finished.connect(self._core_finished)
        Thread(target=self._run_core, daemon=True, name="persistent-core").start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(1000)

    def _run_core(self) -> None:
        code = run(stop_event=self.stop_event, install_signal_handlers=False)
        self.core_finished.emit(code)

    def _refresh_status(self) -> None:
        record = discovery.read_discovery_file()
        state = "READY" if discovery.discover_running_core(timeout=0.2) else "STARTING"
        if record and state == "READY":
            label = f"Core · READY · PID {record.get('pid', '?')}"
        else:
            label = "Core · starting"
        self.status_action.setText(label)
        self.tray.setToolTip(f"Research Assistant {label}")

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_gui()

    def open_gui(self) -> None:
        executable = Path(sys.executable).resolve().with_name("ResearchAssistant.exe")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen([str(executable)], cwd=str(executable.parent),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError:
            logging.exception("Could not start Research Assistant GUI.")

    def quit_core(self) -> None:
        self._quitting = True
        self.status_action.setText("Core · stopping")
        self.stop_event.set()
        QTimer.singleShot(5000, self.app.quit)

    def _core_finished(self, exit_code: int) -> None:
        if not self._quitting:
            self.tray.showMessage("Research Assistant Core", "Core 已停止。",
                                  QSystemTrayIcon.MessageIcon.Warning, 3000)
        self.tray.hide()
        self.app.quit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("Research Assistant Core")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        raise SystemExit(run())
    CoreTrayController(app)
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
