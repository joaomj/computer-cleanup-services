"""CLI-level checks for deletion consent, daily decisions, and mutual exclusion."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from terminal_helpers import Terminal

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src" / "maintenance.py"
PROMPT = b"Proceed with cleanup deletions?"


class MaintenanceCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.service = (
            "macos-cleanup-service"
            if sys.platform == "darwin"
            else "linux-cleanup-service"
        )
        self.state = self.root / "config" / self.service
        self.state.mkdir(parents=True)
        self.projects = self.home / "projects"
        self.candidate = self.projects / "example" / "node_modules"
        self.other = self.home / ".zed_server" / "zed-remote-server-unused"
        self.other.mkdir(parents=True)
        (self.other / "keep").write_text("Unselected application data")
        self.seed_candidate()
        (self.state / "environment").write_text(
            "PROJECT_ARTIFACTS_CLEANUP_ENABLED=true\n"
            f"PROJECT_ROOT={self.projects}\n"
            "COMMAND_TIMEOUT_SECONDS=5\n"
            "APT_UPDATES_ENABLED=false\nSNAP_UPDATES_ENABLED=false\nBREW_UPDATES_ENABLED=false\n"
        )
        self.environment = {
            "HOME": str(self.home),
            "PATH": os.environ["PATH"],
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def seed_candidate(self) -> None:
        self.candidate.mkdir(parents=True, exist_ok=True)
        (self.candidate / "delete-after-consent").write_text("Disposable test data")
        stamp = time.time() - 3 * 86400
        os.utime(self.candidate, (stamp, stamp))

    def terminal(self, *arguments: str) -> Terminal:
        terminal = Terminal(
            [sys.executable, str(CLI), *arguments], self.environment, str(ROOT)
        )
        self.addCleanup(terminal.close)
        return terminal

    def run_cli(
        self, *arguments: str, answer: str = "Y\n"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *arguments],
            input=answer,
            capture_output=True,
            text=True,
            env=self.environment,
            cwd=ROOT,
            timeout=20,
        )

    def test_refusal_empty_eof_and_interrupt_preserve_files(self) -> None:
        for reply in (b"N\n", b"\n", b"\x04", b"interrupt"):
            with self.subTest(reply=reply):
                terminal = self.terminal("--cleanup-only", "--force")
                preview = terminal.read_until(PROMPT)
                self.assertIn('"deleted": 1', preview)
                self.assertTrue(self.candidate.exists())
                if reply == b"interrupt":
                    terminal.process.send_signal(signal.SIGINT)
                    output = terminal.read_until()
                    code = terminal.process.wait(timeout=5)
                else:
                    code, output = terminal.answer(reply)
                self.assertEqual(code, 0, output)
                self.assertTrue(self.candidate.exists())
                record = json.loads((self.state / "cleanup-status.json").read_text())
                self.assertEqual(record["decision"], "declined")
                self.assertFalse(record["deletions_approved"])

    def test_missing_terminal_then_yes_daily_gate_and_forced_retry(self) -> None:
        result = self.run_cli("--cleanup-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.candidate.exists())
        self.assertFalse((self.state / "cleanup-status.json").exists())
        terminal = self.terminal("--cleanup-only")
        terminal.read_until(PROMPT)
        code, output = terminal.answer(b"Y\n")
        self.assertEqual(code, 0, output)
        self.assertFalse(self.candidate.exists())
        self.assertTrue((self.other / "keep").exists())
        record = json.loads((self.state / "cleanup-status.json").read_text())
        self.assertEqual(record["decision"], "approved")
        self.assertEqual(record["project_artifacts"]["deleted"], 1)
        self.seed_candidate()
        result = self.run_cli("--cleanup-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.candidate.exists())
        terminal = self.terminal("--cleanup-only", "--force")
        terminal.read_until(PROMPT)
        code, output = terminal.answer(b"N\n")
        self.assertEqual(code, 0, output)
        self.assertTrue(self.candidate.exists())

    def test_update_and_preview_modes_do_not_delete_selected_data(self) -> None:
        for arguments in (
            ("--updates-only", "--force"),
            ("--cleanup-only", "--dry-run", "--force"),
            ("--dry-run", "--force"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self.candidate.exists())
                self.assertFalse((self.state / "cleanup-status.json").exists())
        updates = json.loads((self.state / "status.json").read_text())
        self.assertFalse(updates["deletions_approved"])

    def test_second_process_cannot_bypass_a_pending_decision(self) -> None:
        terminal = self.terminal("--cleanup-only", "--force")
        terminal.read_until(PROMPT)
        second = self.run_cli("--cleanup-only", "--force")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(self.candidate.exists())
        self.assertFalse((self.state / "cleanup-status.json").exists())
        code, output = terminal.answer(b"N\n")
        self.assertEqual(code, 0, output)
        self.assertTrue(self.candidate.exists())


if __name__ == "__main__":
    unittest.main()
