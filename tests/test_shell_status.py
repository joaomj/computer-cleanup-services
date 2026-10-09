#!/usr/bin/env python3
"""Shell status is quiet unless the last pass failed, and OS-filtered."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import status
from cleanup import execute
from config import SERVICE_NAME, load_config


class ShellStatusTest(unittest.TestCase):
    def load_test_config(self, root: Path, extra: str = ""):
        """Load a config rooted at a temporary XDG config home."""
        config_home = root / "config"
        config_dir = config_home / SERVICE_NAME
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "environment").write_text(
            f"COMMAND_TIMEOUT_SECONDS=5\n{extra}", encoding="utf-8"
        )
        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        return load_config()

    def render_with(self, record: dict | None) -> str | None:
        """Render render_shell against a temporary status record."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = {key: os.environ.get(key) for key in ("XDG_CONFIG_HOME",)}
            try:
                config = self.load_test_config(root)
                if record is not None:
                    (Path(config.state_dir) / "status.json").write_text(
                        json.dumps(record), encoding="utf-8"
                    )
                with patch.object(status, "service_is_active", return_value=True):
                    return status.render_shell(config)
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_quiet_unless_failed(self) -> None:
        """Success, warning, running, and missing records print nothing."""
        self.assertIsNone(self.render_with(None))
        self.assertIsNone(self.render_with({"status": "success"}))
        self.assertIsNone(
            self.render_with({"status": "warning", "warnings": ["something aged"]})
        )
        self.assertIsNone(self.render_with({"status": "running"}))
        failed = self.render_with({"status": "failed", "errors": ["disk blew up"]})
        self.assertIsNotNone(failed)
        self.assertIn("failed", failed)
        self.assertIn("disk blew up", failed)

    def test_failed_line_hides_other_os_tasks(self) -> None:
        """A macOS failure never mentions APT/Snap/journal, and vice versa."""
        record = {
            "status": "failed",
            "finished_at": "2026-09-21T00:36:41-03:00",
            "homebrew_updates": "deferred",
            "homebrew_cleanup": "updated",
            "apt_updates": "unavailable",
            "snap_updates": "unavailable",
            "system_journal_bytes": 123,
            "skipped": ["APT updates", "Snap updates", "Brave cache"],
            "errors": ["disk blew up"],
        }
        with patch("status.sys.platform", "darwin"):
            line = self.render_with(record)
        self.assertIsNotNone(line)
        self.assertIn("Homebrew", line)
        self.assertNotIn("APT", line)
        self.assertNotIn("Snap", line)
        self.assertNotIn("journal", line)
        with patch("status.sys.platform", "linux"):
            line = self.render_with(record)
        self.assertIsNotNone(line)
        self.assertIn("APT", line)
        self.assertNotIn("Homebrew", line)

    def run_execute(self, platform: str) -> dict:
        """Run a dry-run pass isolated from the real home and state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            projects = home / "projects"
            projects.mkdir(parents=True)
            original = {
                key: os.environ.get(key)
                for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME")
            }
            try:
                config_home = root / "config"
                (config_home / SERVICE_NAME).mkdir(parents=True)
                (config_home / SERVICE_NAME / "environment").write_text(
                    "COMMAND_TIMEOUT_SECONDS=5\n"
                    "RUNTIME_UPDATES_ENABLED=false\n"
                    f"PROJECT_ROOT={projects}\n",
                    encoding="utf-8",
                )
                os.environ["XDG_CONFIG_HOME"] = str(config_home)
                os.environ["XDG_CACHE_HOME"] = str(root / "cache")
                os.environ["XDG_DATA_HOME"] = str(root / "data")
                with (
                    patch.object(Path, "home", return_value=home),
                    patch("cleanup.sys.platform", platform),
                ):
                    return execute(load_config(), dry_run=True)
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_execute_records_only_current_os_tasks(self) -> None:
        """macOS passes carry no APT/Snap keys; Linux passes carry no Homebrew keys."""
        macos = self.run_execute("darwin")
        self.assertIn("homebrew_updates", macos)
        self.assertNotIn("apt_updates", macos)
        self.assertNotIn("snap_updates", macos)
        self.assertNotIn("apt_cache", macos)
        self.assertFalse(any("APT" in item or "Snap" in item for item in macos["skipped"]))
        linux = self.run_execute("linux")
        self.assertIn("apt_updates", linux)
        self.assertIn("snap_updates", linux)
        self.assertNotIn("homebrew_updates", linux)
        self.assertNotIn("homebrew_cleanup", linux)


if __name__ == "__main__":
    unittest.main()
