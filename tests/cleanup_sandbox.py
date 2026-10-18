"""Disposable home, settings, and CLI runner for end-to-end cleanup checks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from terminal_helpers import Terminal

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src" / "maintenance.py"
SERVICE = "macos-cleanup-service" if sys.platform == "darwin" else "linux-cleanup-service"
# Age limits of zero make freshly created test files old enough to clean.
BASE_SETTINGS = {
    "COMMAND_TIMEOUT_SECONDS": "20",
    "APT_UPDATES_ENABLED": "false",
    "SNAP_UPDATES_ENABLED": "false",
    "BREW_UPDATES_ENABLED": "false",
    "SUDO_TASKS_ENABLED": "false",
    "RUNTIME_UPDATES_ENABLED": "false",
    "UV_CLEANUP_ENABLED": "false",
    "BRAVE_CLEANUP_ENABLED": "false",
    "NPM_CLEANUP_ENABLED": "false",
    "YARN_CLEANUP_ENABLED": "false",
    "PLAYWRIGHT_CLEANUP_ENABLED": "false",
    "ZED_CLEANUP_ENABLED": "false",
    "ZED_SERVER_CLEANUP_ENABLED": "false",
    "HUGGINGFACE_CLEANUP_ENABLED": "false",
    "PROJECT_ARTIFACTS_CLEANUP_ENABLED": "false",
    "DOCKER_PRUNE_ENABLED": "false",
    "TMP_CLEANUP_ENABLED": "false",
    "APT_CACHE_CLEANUP_ENABLED": "false",
    "JOURNAL_CLEANUP_ENABLED": "false",
    "BREW_CLEANUP_ENABLED": "false",
    "CACHE_MAX_AGE_HOURS": "0",
    "PROJECT_ARTIFACTS_MAX_AGE_HOURS": "0",
    "PROJECT_CACHE_MAX_AGE_HOURS": "0",
    # A zero disk threshold means "always under pressure", so reviews prompt.
    "HIGH_RISK_REVIEW_DISK_PERCENT": "0",
    "HIGH_RISK_REVIEW_MEMORY_PERCENT": "0",
    "TMP_MAX_AGE_HOURS": "0",
}


class Sandbox:
    """A user account in a temporary directory with selected actions enabled."""

    def __init__(self, **settings: str) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.home = self.root / "home"
        self.projects = self.home / "projects"
        self.tmp = self.root / "tmp"
        self.cache = self.root / "cache"
        self.state = self.root / "config" / SERVICE
        for directory in (self.projects, self.tmp, self.state):
            directory.mkdir(parents=True)
        values = {
            **BASE_SETTINGS,
            "PROJECT_ROOT": str(self.projects),
            "TMP_DIR": str(self.tmp),
            **settings,
        }
        (self.state / "environment").write_text(
            "".join(f"{key}={value}\n" for key, value in values.items())
        )
        self.environment = {
            "HOME": str(self.home),
            "PATH": os.environ["PATH"],
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_CACHE_HOME": str(self.cache),
            "XDG_DATA_HOME": str(self.root / "data"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def close(self) -> None:
        self._directory.cleanup()

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """Run the CLI without a terminal, as the background service does."""
        result = subprocess.run(
            [sys.executable, str(CLI), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=self.environment,
            cwd="/",
            timeout=120,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        return result

    def daily(self) -> dict:
        """Run the daily pass, ignoring today's record, and return its status."""
        self.run("--force")
        return json.loads((self.state / "status.json").read_text())

    def preview(self) -> dict:
        return json.loads(self.run("--dry-run").stdout)

    def review(self, *answers: bytes) -> str:
        """Answer the review prompts in a terminal and return the transcript."""
        terminal = Terminal(
            [sys.executable, str(CLI), "--review", "--force"], self.environment, "/"
        )
        try:
            for index, answer in enumerate(answers, start=1):
                terminal.read_until(b"[y/N/q]", count=index)
                os.write(terminal.master, answer)
            output = terminal.read_until()
            code = terminal.process.wait(timeout=10)
        finally:
            terminal.close()
        if code != 0:
            raise AssertionError(output)
        return output

    def pending(self) -> list[dict]:
        path = self.state / "pending-review.json"
        if not path.exists():
            return []
        return json.loads(path.read_text())["items"]
