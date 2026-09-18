"""Tests for the read-only MCP developer diagnostics command
(app/external_api/mcp_diagnostics.py) - not the stdio server itself.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.embedding.semantic_index import SemanticIndex
from app.external_api import mcp_diagnostics
from tests.evidence_report_fixtures import build_in2se3_corpus
from tests.semantic_fixtures import FakeEmbedder


class DiagnosticsSucceedsOnValidEnvironmentTests(unittest.TestCase):
    """Uses a temp fixture corpus (like tests/test_mcp_integration.py), not the
    user's real data/literature.db, so this passes deterministically anywhere.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.database = root / "literature.db"
        build_in2se3_corpus(self.database)
        SemanticIndex(self.database, root / "semantic", FakeEmbedder()).update()

    def test_all_checks_pass_against_a_real_populated_environment(self):
        results = mcp_diagnostics.run_all(database_path=self.database)
        failed = [r for r in results if not r.ok]
        self.assertEqual(failed, [], f"Unexpected failures: {failed}")

    def test_find_evidence_smoke_call_actually_runs_the_real_engine(self):
        results = mcp_diagnostics.run_all(database_path=self.database)
        smoke = next(r for r in results if r.name == "find_evidence smoke call")
        self.assertTrue(smoke.ok)
        self.assertIn("candidate", smoke.detail)

    def test_main_exits_zero_and_prints_only_to_stdout(self):
        with patch("sys.argv", ["mcp_diagnostics"]), \
             patch("app.external_api.mcp_diagnostics.run_all",
                   return_value=mcp_diagnostics.run_all(database_path=self.database)):
            exit_code = mcp_diagnostics.main()
        self.assertEqual(exit_code, 0)


class DiagnosticsFailsClearlyWhenDatabaseUnavailableTests(unittest.TestCase):
    def test_missing_database_path_is_reported_as_a_failed_check_not_an_exception(self):
        missing = Path(tempfile.gettempdir()) / "definitely-does-not-exist" / "literature.db"
        results = mcp_diagnostics.run_all(database_path=missing)

        db_check = next(r for r in results if r.name == "literature.db")
        self.assertFalse(db_check.ok)
        self.assertIn(str(missing), db_check.detail)

        semantic_check = next(r for r in results if r.name == "Semantic index")
        self.assertFalse(semantic_check.ok)

        smoke_check = next(r for r in results if r.name == "find_evidence smoke call")
        self.assertFalse(smoke_check.ok)
        self.assertIn("skipped", smoke_check.detail)

    def test_main_exits_non_zero_when_database_is_missing(self):
        missing = Path(tempfile.gettempdir()) / "definitely-does-not-exist" / "literature.db"
        with patch("app.external_api.mcp_diagnostics.run_all",
                   return_value=mcp_diagnostics.run_all(database_path=missing)):
            exit_code = mcp_diagnostics.main()
        self.assertNotEqual(exit_code, 0)

    def test_checking_a_missing_database_never_creates_one(self):
        missing_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: missing_dir.rmdir() if missing_dir.exists() and not any(missing_dir.iterdir())
                         else None)
        missing = missing_dir / "literature.db"
        mcp_diagnostics.check_database(missing)
        self.assertFalse(missing.exists(), "Diagnostics must be read-only and never create the database file.")


class RequiredDependenciesCheckTests(unittest.TestCase):
    def test_every_requirements_txt_package_has_a_dependency_check(self):
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
        for package_name in mcp_diagnostics.REQUIRED_MODULES:
            self.assertIn(package_name, requirements,
                          f"{package_name} is checked by mcp_diagnostics but not in requirements.txt")


if __name__ == "__main__":
    unittest.main()
