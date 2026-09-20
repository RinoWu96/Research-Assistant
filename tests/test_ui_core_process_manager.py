"""Unit tests for app/ui/core_process_manager.py: CoreProcessManager detects
or starts a Core, and CoreMonitor reports lifecycle state off the GUI
thread (V0.6 brief, sections 6-9). Mocks `app.core_service.discovery` and
`subprocess.Popen` throughout - no real Core process, and no real BGE
model, is ever started by these tests.
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from app.ui.core_process_manager import (
    UNAVAILABLE, CoreMonitor, CoreProcessManager, _core_launch_command,
)


class CoreLaunchCommandTests(unittest.TestCase):
    def test_source_build_uses_python_module(self):
        with patch("app.ui.core_process_manager.sys.frozen", False, create=True):
            command, cwd = _core_launch_command()
        self.assertEqual(command[1:], ["-m", "app.core_service"])
        self.assertTrue(command[0].lower().endswith("python.exe"))
        self.assertTrue((cwd / "app").is_dir())

    def test_frozen_build_uses_sibling_core_executable(self):
        executable = r"C:\Portable\ResearchAssistant\ResearchAssistant.exe"
        with patch("app.ui.core_process_manager.sys.frozen", True, create=True), \
             patch("app.ui.core_process_manager.sys.executable", executable):
            command, cwd = _core_launch_command()
        self.assertEqual(command, [r"C:\Portable\ResearchAssistant\ResearchAssistantCore.exe"])
        self.assertEqual(cwd, Path(r"C:\Portable\ResearchAssistant"))


class DetectExistingCoreTests(unittest.TestCase):
    def test_reuses_an_already_running_core_without_starting_one(self):
        manager = CoreProcessManager()
        with patch("app.ui.core_process_manager.discovery.discover_running_core",
                  return_value="http://127.0.0.1:8756") as discover, \
             patch("app.ui.core_process_manager.subprocess.Popen") as popen:
            result = manager.ensure_core_running()
        self.assertEqual(result, "http://127.0.0.1:8756")
        self.assertEqual(manager.base_url, "http://127.0.0.1:8756")
        self.assertFalse(manager.launched_by_us)
        discover.assert_called_once()
        popen.assert_not_called()


class StartCoreWhenAbsentTests(unittest.TestCase):
    def test_starts_a_core_process_when_none_is_discoverable(self):
        manager = CoreProcessManager()
        discover_calls = {"count": 0}

        def discover(*args, **kwargs):
            discover_calls["count"] += 1
            # First call (before starting): none running. Later calls
            # (polling after Popen): the newly started Core is up.
            return None if discover_calls["count"] == 1 else "http://127.0.0.1:8756"

        fake_process = MagicMock(pid=4242)
        with patch("app.ui.core_process_manager.discovery.discover_running_core", side_effect=discover), \
             patch("app.ui.core_process_manager.subprocess.Popen", return_value=fake_process) as popen:
            result = manager.ensure_core_running()
        self.assertEqual(result, "http://127.0.0.1:8756")
        self.assertTrue(manager.launched_by_us)
        popen.assert_called_once()
        args = popen.call_args.args[0]
        self.assertIn("-m", args)
        self.assertIn("app.core_service", args)

    def test_only_one_core_process_is_ever_started(self):
        """Guards against a launcher bug spawning a second BGE process -
        the real single-instance guard is the Core's own OS-level lock
        (V0.5), but the launcher itself must not be sloppy either."""
        manager = CoreProcessManager()
        with patch("app.ui.core_process_manager.discovery.discover_running_core", return_value=None), \
             patch("app.ui.core_process_manager.subprocess.Popen",
                  return_value=MagicMock(pid=1)) as popen, \
             patch("app.ui.core_process_manager.time.sleep"), \
             patch("app.ui.core_process_manager.START_TIMEOUT_SECONDS", 0.05):
            manager.ensure_core_running()
        self.assertEqual(popen.call_count, 1)

    def test_returns_none_and_does_not_raise_if_the_process_fails_to_launch(self):
        manager = CoreProcessManager()
        with patch("app.ui.core_process_manager.discovery.discover_running_core", return_value=None), \
             patch("app.ui.core_process_manager.subprocess.Popen", side_effect=OSError("no such file")):
            result = manager.ensure_core_running()
        self.assertIsNone(result)
        self.assertIsNone(manager.base_url)

    def test_returns_none_if_the_process_never_becomes_reachable(self):
        manager = CoreProcessManager()
        with patch("app.ui.core_process_manager.discovery.discover_running_core", return_value=None), \
             patch("app.ui.core_process_manager.subprocess.Popen", return_value=MagicMock(pid=1)), \
             patch("app.ui.core_process_manager.time.sleep"), \
             patch("app.ui.core_process_manager.time.monotonic", side_effect=[0, 0, 100]):
            result = manager.ensure_core_running()
        self.assertIsNone(result)
        self.assertTrue(manager.launched_by_us)  # it did try - just never became reachable


class StatusTests(unittest.TestCase):
    def test_none_before_a_core_is_discovered(self):
        self.assertIsNone(CoreProcessManager().status())

    def test_delegates_to_core_client_once_discovered(self):
        manager = CoreProcessManager()
        manager.base_url = "http://127.0.0.1:8756"
        with patch("app.ui.core_process_manager.CoreClient") as client_cls:
            client_cls.return_value.status.return_value = {"state": "READY"}
            self.assertEqual(manager.status(), {"state": "READY"})
        client_cls.assert_called_once_with(base_url="http://127.0.0.1:8756")

    def test_none_if_the_status_call_fails(self):
        manager = CoreProcessManager()
        manager.base_url = "http://127.0.0.1:8756"
        with patch("app.ui.core_process_manager.CoreClient") as client_cls:
            client_cls.return_value.status.side_effect = RuntimeError("unreachable")
            self.assertIsNone(manager.status())


class CoreMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _wait_for(self, predicate, timeout=5.0):
        import time
        start = time.monotonic()
        while not predicate() and time.monotonic() - start < timeout:
            QTest.qWait(10)
        self.assertTrue(predicate(), "condition never became true")

    def test_unavailable_when_core_cannot_be_started(self):
        manager = MagicMock()
        manager.ensure_core_running.return_value = None
        monitor = CoreMonitor(manager)
        seen = []
        monitor.state_changed.connect(lambda state, status: seen.append((state, status)))
        monitor.start()
        self._wait_for(lambda: seen)
        self.assertEqual(seen[-1], (UNAVAILABLE, {}))

    def test_reports_loading_then_ready(self):
        manager = MagicMock()
        manager.ensure_core_running.return_value = "http://127.0.0.1:8756"
        manager.status.side_effect = [
            {"state": "LOADING"},
            {"state": "READY", "paper_count": 201},
        ]
        monitor = CoreMonitor(manager)
        seen = []
        monitor.state_changed.connect(lambda state, status: seen.append((state, status)))
        monitor.start()
        self._wait_for(lambda: seen and seen[-1][0] == "READY")
        self.assertEqual([entry[0] for entry in seen], ["LOADING", "READY"])

    def test_stop_prevents_further_signal_emission(self):
        manager = MagicMock()
        manager.ensure_core_running.return_value = "http://127.0.0.1:8756"
        manager.status.return_value = {"state": "LOADING"}
        monitor = CoreMonitor(manager)
        seen = []
        monitor.state_changed.connect(lambda state, status: seen.append((state, status)))
        monitor.start()
        self._wait_for(lambda: seen)
        monitor.stop()
        count_after_stop = len(seen)
        QTest.qWait(1200)  # longer than one poll interval
        self.assertEqual(len(seen), count_after_stop)


if __name__ == "__main__":
    unittest.main()
