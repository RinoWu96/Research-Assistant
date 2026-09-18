"""Desktop helper-process protocol regression tests."""

from pathlib import Path
import unittest
from unittest.mock import patch

from app.search.desktop_search import _console_python


class DesktopSearchProcessTests(unittest.TestCase):
    def test_pythonw_parent_uses_console_python(self):
        with patch("app.search.desktop_search.sys.executable", r"C:\env\Scripts\pythonw.exe"):
            self.assertEqual(_console_python(), Path(r"C:\env\Scripts\python.exe"))


if __name__ == "__main__":
    unittest.main()
