#!/usr/bin/env python3
"""Black-box checks for APT and Snap updates."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from cleanup import cleanup_homebrew, update_apt, update_snap
from config import SERVICE_NAME, load_config


class SystemUpdatesTest(unittest.TestCase):
    def write_command(self, directory: Path, name: str, content: str) -> None:
        """Create an executable test command."""
        command = directory / name
        command.write_text(content, encoding="utf-8")
        command.chmod(0o755)

    def configure_fake_commands(self, directory: Path, log_path: Path) -> None:
        """Create fake sudo, apt-get, and snap commands for a safe test."""
        self.write_command(
            directory,
            "sudo",
            """#!/bin/sh
set -eu
printf 'sudo %s\\n' "$*" >> "$UPDATE_LOG"
if [ "$1" = "-n" ]; then shift; fi
exec "$@"
""",
        )
        self.write_command(
            directory,
            "apt-get",
            """#!/bin/sh
set -eu
printf 'apt-get %s\\n' "$*" >> "$UPDATE_LOG"
if [ "$1" = "update" ] && [ "${FAIL_APT_UPDATE:-0}" = "1" ]; then exit 1; fi
""",
        )
        self.write_command(
            directory,
            "snap",
            """#!/bin/sh
set -eu
printf 'snap %s\\n' "$*" >> "$UPDATE_LOG"
""",
        )
        os.environ["PATH"] = f"{directory}:{os.environ['PATH']}"
        os.environ["UPDATE_LOG"] = str(log_path)

    def test_updates_run_in_order(self) -> None:
        """Run APT update before upgrade, then refresh Snap packages."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_home = root / "config"
            config_dir = config_home / SERVICE_NAME
            command_dir = root / "bin"
            log_path = root / "updates.log"
            config_dir.mkdir(parents=True)
            command_dir.mkdir()
            (config_dir / "environment").write_text(
                "APT_UPDATES_ENABLED=true\nSNAP_UPDATES_ENABLED=true\nCOMMAND_TIMEOUT_SECONDS=5\n",
                encoding="utf-8",
            )
            original = {
                key: os.environ.get(key)
                for key in ("PATH", "UPDATE_LOG", "XDG_CONFIG_HOME", "FAIL_APT_UPDATE")
            }
            try:
                os.environ["XDG_CONFIG_HOME"] = str(config_home)
                os.environ.pop("FAIL_APT_UPDATE", None)
                self.configure_fake_commands(command_dir, log_path)
                config = load_config()
                warnings: list[str] = []
                skipped: list[str] = []
                self.assertEqual(update_apt(config, warnings, skipped, False), "updated")
                self.assertEqual(update_snap(config, warnings, skipped, False), "updated")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            output = log_path.read_text(encoding="utf-8")
            self.assertLess(output.index("apt-get update"), output.index("apt-get upgrade -y"))
            self.assertLess(output.index("apt-get upgrade -y"), output.index("snap refresh"))
            self.assertEqual(warnings, [])

    def test_apt_failure_skips_upgrade_but_does_not_block_snap(self) -> None:
        """Continue with Snap updates when the APT package-list update fails."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_home = root / "config"
            config_dir = config_home / SERVICE_NAME
            command_dir = root / "bin"
            log_path = root / "updates.log"
            config_dir.mkdir(parents=True)
            command_dir.mkdir()
            (config_dir / "environment").write_text(
                "APT_UPDATES_ENABLED=true\nSNAP_UPDATES_ENABLED=true\nCOMMAND_TIMEOUT_SECONDS=5\n",
                encoding="utf-8",
            )
            original = {
                key: os.environ.get(key)
                for key in ("PATH", "UPDATE_LOG", "XDG_CONFIG_HOME", "FAIL_APT_UPDATE")
            }
            try:
                os.environ["XDG_CONFIG_HOME"] = str(config_home)
                os.environ["FAIL_APT_UPDATE"] = "1"
                self.configure_fake_commands(command_dir, log_path)
                config = load_config()
                warnings: list[str] = []
                skipped: list[str] = []
                self.assertEqual(update_apt(config, warnings, skipped, False), "failed")
                self.assertEqual(update_snap(config, warnings, skipped, False), "updated")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            output = log_path.read_text(encoding="utf-8")
            self.assertIn("apt-get update", output)
            self.assertNotIn("apt-get upgrade -y", output)
            self.assertIn("snap refresh", output)
            self.assertTrue(any("APT package list update failed" in warning for warning in warnings))

    def test_homebrew_cleanup_scrubs_cache_on_macos(self) -> None:
        config = load_config()
        with (
            patch("cleanup.sys.platform", "darwin"),
            patch("cleanup.shutil.which", return_value="/opt/homebrew/bin/brew"),
            patch("cleanup.run_command", return_value=(0, "", "")) as run,
        ):
            result = cleanup_homebrew(config, [], [], dry_run=False)

        self.assertEqual(result, "updated")
        run.assert_called_once_with(["/opt/homebrew/bin/brew", "cleanup", "-s"], config)

    def test_homebrew_cleanup_reports_failure(self) -> None:
        config = load_config()
        warnings: list[str] = []
        with (
            patch("cleanup.sys.platform", "darwin"),
            patch("cleanup.shutil.which", return_value="/opt/homebrew/bin/brew"),
            patch("cleanup.run_command", return_value=(1, "", "cleanup error")),
        ):
            result = cleanup_homebrew(config, warnings, [], dry_run=False)

        self.assertEqual(result, "failed")
        self.assertEqual(warnings, ["Homebrew cleanup failed: cleanup error"])


if __name__ == "__main__":
    unittest.main()
