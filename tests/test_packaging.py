"""Release-packaging seams; does not invoke PyInstaller."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from app import config
from app.version import __version__


class FrozenDataDirectoryTests(unittest.TestCase):
    def test_release_version(self):
        self.assertEqual(__version__, "0.7.1")

    def test_frozen_default_uses_local_app_data_not_bundle_directory(self):
        with patch.object(config.sys, "frozen", True, create=True), \
             patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}):
            result = config._default_app_data_dir()
        self.assertEqual(result, Path(r"C:\Users\test\AppData\Local\ResearchAssistant\data"))

    def test_source_default_stays_repository_local(self):
        with patch.object(config.sys, "frozen", False, create=True):
            result = config._default_app_data_dir()
        self.assertEqual(result, Path(config.__file__).resolve().parents[1] / "data")


if __name__ == "__main__":
    unittest.main()
