"""Tests for app/core_service/discovery.py: the single-instance lock and the
discovery file the MCP proxy (and later the GUI) use to find a running Core
without any manual per-session configuration (V0.5 brief, sections 9-10).
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core_service import discovery


class SingletonLockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock_path = Path(self._tmp.name) / "core.lock"

    def test_first_acquire_succeeds(self):
        lock = discovery.SingletonLock(self.lock_path)
        self.assertTrue(lock.acquire())
        lock.release()

    def test_second_process_cannot_acquire_while_the_first_holds_it(self):
        first = discovery.SingletonLock(self.lock_path)
        second = discovery.SingletonLock(self.lock_path)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.release()

    def test_lock_is_reacquirable_after_release(self):
        lock = discovery.SingletonLock(self.lock_path)
        self.assertTrue(lock.acquire())
        lock.release()
        other = discovery.SingletonLock(self.lock_path)
        self.assertTrue(other.acquire())
        other.release()

    def test_release_without_acquire_is_a_safe_no_op(self):
        discovery.SingletonLock(self.lock_path).release()  # must not raise


class DiscoveryFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "core.discovery.json"

    def test_round_trips_host_port_and_pid(self):
        discovery.write_discovery_file(12345, 999, self.path)
        record = discovery.read_discovery_file(self.path)
        self.assertEqual(record["port"], 12345)
        self.assertEqual(record["pid"], 999)
        self.assertEqual(record["host"], "127.0.0.1")

    def test_missing_file_reads_as_none(self):
        self.assertIsNone(discovery.read_discovery_file(self.path))

    def test_remove_is_safe_when_file_is_already_gone(self):
        discovery.remove_discovery_file(self.path)  # must not raise

    def test_corrupt_file_reads_as_none_not_a_crash(self):
        self.path.write_text("not json", encoding="utf-8")
        self.assertIsNone(discovery.read_discovery_file(self.path))


class DiscoverRunningCoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "core.discovery.json"

    def test_no_file_means_no_running_core(self):
        self.assertIsNone(discovery.discover_running_core(self.path))

    def test_file_present_but_unhealthy_means_no_running_core(self):
        discovery.write_discovery_file(59999, 1, self.path)  # nothing listening there
        self.assertIsNone(discovery.discover_running_core(self.path, timeout=0.2))

    def test_file_present_and_healthy_returns_its_base_url(self):
        discovery.write_discovery_file(8756, 1, self.path)
        with patch("app.core_service.discovery.probe_health", return_value=True):
            self.assertEqual(discovery.discover_running_core(self.path), "http://127.0.0.1:8756")


if __name__ == "__main__":
    unittest.main()
