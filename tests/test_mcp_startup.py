"""Realistic, subprocess-level startup and path-resolution tests for the MCP
server (app/external_api/mcp_server.py).

Unlike tests/test_mcp_server.py (in-memory transport, same process),
these spawn the actual venv Python as a separate process the way an MCP
client (Claude Code) does, over the real stdio transport - this is what
Task 6 of the V0.4.1 brief calls the "server startup smoke test", and what
proves the process doesn't depend on being launched from exactly the
project root (Task 4/12.2/12.3).
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
MCP_SERVER_FILE = PROJECT_ROOT / "app" / "external_api" / "mcp_server.py"


async def _handshake_and_list_tools(params: StdioServerParameters) -> list[str]:
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [tool.name for tool in tools.tools]


class RealStdioSubprocessTests(unittest.TestCase):
    """Spawns the real server subprocess and speaks real MCP-over-stdio to it."""

    def test_full_handshake_from_project_root_lists_find_evidence(self):
        params = StdioServerParameters(
            command=str(PYTHON), args=["-m", "app.external_api.mcp_server"], cwd=str(PROJECT_ROOT))
        tool_names = anyio.run(_handshake_and_list_tools, params)
        self.assertEqual(tool_names, ["find_evidence"])

    def test_full_handshake_via_direct_script_path_from_an_unrelated_cwd(self):
        """No `-m`, and cwd is neither the project root nor a parent of it -
        only the sys.path bootstrap in mcp_server.py should make this work."""
        with tempfile.TemporaryDirectory() as elsewhere:
            params = StdioServerParameters(
                command=str(PYTHON), args=[str(MCP_SERVER_FILE)], cwd=elsewhere)
            tool_names = anyio.run(_handshake_and_list_tools, params)
        self.assertEqual(tool_names, ["find_evidence"])

    def test_module_invocation_with_pythonpath_works_from_an_unrelated_cwd(self):
        """The documented `.mcp.json` fix: no `cwd` field exists in the installed
        Claude Code MCP schema, so `env.PYTHONPATH` is what makes `-m` resolve
        `app` regardless of the client's actual launch directory."""
        with tempfile.TemporaryDirectory() as elsewhere:
            params = StdioServerParameters(
                command=str(PYTHON), args=["-m", "app.external_api.mcp_server"],
                cwd=elsewhere, env={"PYTHONPATH": str(PROJECT_ROOT)})
            tool_names = anyio.run(_handshake_and_list_tools, params)
        self.assertEqual(tool_names, ["find_evidence"])


class ModuleInvocationWithoutProjectRootFailsClearlyTests(unittest.TestCase):
    """Documents the one case nothing in-process can fix: `-m` needs `app` on
    sys.path *before* mcp_server.py's own code (including its bootstrap) runs
    at all. This must fail fast with a clear stderr error, not hang or crash
    with an unrelated traceback - never assume `.mcp.json`'s `cwd` field
    (which is unsupported here) is a substitute for `env.PYTHONPATH`.
    """

    def test_wrong_cwd_and_no_pythonpath_fails_fast_with_a_clear_stderr_message(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            result = subprocess.run(
                [str(PYTHON), "-m", "app.external_api.mcp_server"],
                cwd=elsewhere, input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"No module named 'app'", result.stderr)


class StdioContaminationTests(unittest.TestCase):
    """Task 3/12.4: stdout must carry only MCP JSON-RPC traffic. Checked at the
    OS pipe level (real subprocess stdout), not by patching Python's
    `sys.stdout` - that would miss output written by C extensions/threads that
    bypass the `sys.stdout` object entirely.
    """

    def test_stdout_on_eof_is_empty_no_banners_progress_bars_or_debug_prints(self):
        result = subprocess.run(
            [str(PYTHON), "-m", "app.external_api.mcp_server"],
            cwd=str(PROJECT_ROOT), input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"", f"Unexpected stdout contamination: {result.stdout!r}")

    def test_every_stdout_line_during_a_real_session_is_valid_json_rpc(self):
        # stdio_client owns its pipes internally and only exposes parsed
        # messages; raw byte-level inspection needs a subprocess we drive
        # ourselves, sending the handshake by hand.
        proc = subprocess.Popen(
            [str(PYTHON), "-m", "app.external_api.mcp_server"], cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            init_request = (
                b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{'
                b'"protocolVersion":"2025-06-18","capabilities":{},'
                b'"clientInfo":{"name":"test","version":"0"}}}\n')
            proc.stdin.write(init_request)
            proc.stdin.flush()
            first_line = proc.stdout.readline()
            proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            proc.stdin.flush()

            list_tools_request = b'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n'
            proc.stdin.write(list_tools_request)
            proc.stdin.flush()
            second_line = proc.stdout.readline()
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        import json
        for raw_line in (first_line, second_line):
            self.assertTrue(raw_line, "Expected a non-empty JSON-RPC response line.")
            parsed = json.loads(raw_line)  # raises if any stray text shares the line
            self.assertEqual(parsed.get("jsonrpc"), "2.0")
        self.assertIn(b'"find_evidence"', second_line)


if __name__ == "__main__":
    unittest.main()
