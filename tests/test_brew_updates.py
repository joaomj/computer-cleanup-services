#!/usr/bin/env python3
"""Black-box checks for Homebrew updates."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from cleanup import find_brew, update_homebrew
from config import SERVICE_NAME, load_config


class BrewUpdatesTest(unittest.TestCase):
    def write_command(self, directory: Path, name: str, content: str) -> None:
        """Create an executable test command."""
        command = directory / name
        command.write_text(content, encoding="utf-8")
        command.chmod(0o755)

    def configure_fake_brew(self, directory: Path, log_path: Path) -> None:
        """Create a fake brew command that logs its arguments."""
        self.write_command(
            directory,
            "brew",
            """#!/bin/sh
set -eu
printf 'brew %s\\n' "$*" >> "$UPDATE_LOG"
if [ \"$1\" = \"update\" ] && [ \"${FAIL_BREW_UPDATE:-0}\" = \"1\" ]; then exit 1; fi
if [ \"$1\" = \"upgrade\" ] && [ \"${FAIL_BREW_UPGRADE:-0}\" = \"1\" ]; then exit 1; fi
""",
        )
        os.environ["PATH"] = f"{directory}:{os.environ['PATH']}"
        os.environ["UPDATE_LOG"] = str(log_path)

    def load_test_config(self, root: Path, extra: str = ""):
        """Load a config rooted at a temporary XDG config home."""
        config_home = root / "config"
        config_dir = config_home / SERVICE_NAME
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "environment").write_text(
            f"COMMAND_TIMEOUT_SECONDS=5\n{extra}", encoding="utf-8"
        )
        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        return load_config(), config_dir

    def run_update(self, extra: str = "", fail_update: bool = False):
        """Run update_homebrew on macOS with a fake brew in PATH."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command_dir = root / "bin"
            command_dir.mkdir()
            log_path = root / "brew.log"
            original = {
                key: os.environ.get(key)
                for key in ("PATH", "UPDATE_LOG", "XDG_CONFIG_HOME", "FAIL_BREW_UPDATE")
            }
            try:
                self.configure_fake_brew(command_dir, log_path)
                if fail_update:
                    os.environ["FAIL_BREW_UPDATE"] = "1"
                config, config_dir = self.load_test_config(root, extra)
                warnings: list[str] = []
                skipped: list[str] = []
                now = datetime.now().astimezone()
                with patch("cleanup.sys.platform", "darwin"):
                    result = update_homebrew(
                        config, now, warnings, skipped, dry_run=False
                    )
                log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
                state_path = Path(config.state_dir) / "brew_upgrade.json"
                state = (
                    state_path.read_text(encoding="utf-8")
                    if state_path.is_file()
                    else ""
                )
                return result, log, warnings, skipped, state
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_source_build_plan_blocks_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            log = root / "brew.log"
            self.write_command(
                binary,
                "brew",
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$UPDATE_LOG"\nif [ "$2" = "--dry-run" ]; then printf \'1 homebrew/core formula that would build from source\\n\'; fi\n',
            )
            with patch.dict(
                os.environ,
                {"PATH": f"{binary}:{os.environ['PATH']}", "UPDATE_LOG": str(log)},
            ):
                config, _ = self.load_test_config(root)
                with patch("cleanup.sys.platform", "darwin"):
                    warnings, skipped = [], []
                    result = update_homebrew(
                        config, datetime.now().astimezone(), warnings, skipped, False
                    )
                self.assertEqual(result, "source-build-skipped")
                self.assertNotIn("--force-bottle", log.read_text())
                self.assertTrue(skipped)
                self.assertFalse((config.state_dir / "brew_upgrade.json").exists())

    def test_update_runs_before_upgrade(self) -> None:
        """Run brew update before upgrade on the first pass and record state."""
        result, log, warnings, skipped, state = self.run_update()
        self.assertEqual(result, "updated")
        self.assertLess(log.index("brew update"), log.index("brew upgrade"))
        self.assertEqual(warnings, [])
        self.assertEqual(
            datetime.fromisoformat(json.loads(state)["last_success"]).date(),
            datetime.now().astimezone().date(),
        )

    def test_upgrade_deferred_inside_window(self) -> None:
        """Refresh metadata but skip the upgrade when the last one was recent."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command_dir = root / "bin"
            command_dir.mkdir()
            log_path = root / "brew.log"
            original = {
                key: os.environ.get(key)
                for key in ("PATH", "UPDATE_LOG", "XDG_CONFIG_HOME", "FAIL_BREW_UPDATE")
            }
            try:
                self.configure_fake_brew(command_dir, log_path)
                config, config_dir = self.load_test_config(root)
                today = datetime.now().astimezone().date().isoformat()
                (config_dir / "brew_upgrade.json").write_text(
                    json.dumps({"last_success": today}), encoding="utf-8"
                )
                now = datetime.now().astimezone()
                with patch("cleanup.sys.platform", "darwin"):
                    result = update_homebrew(config, now, [], [], dry_run=False)
                self.assertEqual(result, "deferred")
                log = log_path.read_text(encoding="utf-8")
                self.assertIn("brew update", log)
                self.assertNotIn("brew upgrade", log)
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_upgrade_runs_after_window(self) -> None:
        """Upgrade again once the last successful upgrade is old enough."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command_dir = root / "bin"
            command_dir.mkdir()
            log_path = root / "brew.log"
            original = {
                key: os.environ.get(key)
                for key in ("PATH", "UPDATE_LOG", "XDG_CONFIG_HOME", "FAIL_BREW_UPDATE")
            }
            try:
                self.configure_fake_brew(command_dir, log_path)
                config, _ = self.load_test_config(root, "BREW_UPGRADE_MIN_AGE_DAYS=7\n")
                old = (
                    (datetime.now().astimezone() - timedelta(days=10))
                    .date()
                    .isoformat()
                )
                state_path = Path(config.state_dir) / "brew_upgrade.json"
                state_path.write_text(
                    json.dumps({"last_success": old}), encoding="utf-8"
                )
                now = datetime.now().astimezone()
                with patch("cleanup.sys.platform", "darwin"):
                    result = update_homebrew(config, now, [], [], dry_run=False)
                self.assertEqual(result, "updated")
                self.assertIn("brew upgrade", log_path.read_text(encoding="utf-8"))
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_disabled_skips_brew(self) -> None:
        """Do not touch Homebrew when updates are disabled."""
        result, log, _, _, _ = self.run_update("BREW_UPDATES_ENABLED=false\n")
        self.assertEqual(result, "disabled")
        self.assertEqual(log, "")

    def test_update_failure_blocks_upgrade(self) -> None:
        """Skip the upgrade when the metadata refresh fails."""
        result, log, warnings, _, _ = self.run_update(fail_update=True)
        self.assertEqual(result, "failed")
        self.assertIn("brew update", log)
        self.assertNotIn("brew upgrade", log)
        self.assertTrue(
            any("Homebrew update failed" in warning for warning in warnings)
        )

    def test_dry_run_touches_nothing(self) -> None:
        """Report without running brew or writing state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = {key: os.environ.get(key) for key in ("XDG_CONFIG_HOME",)}
            try:
                config, config_dir = self.load_test_config(root)
                now = datetime.now().astimezone()
                with patch("cleanup.sys.platform", "darwin"):
                    result = update_homebrew(config, now, [], [], dry_run=True)
                self.assertEqual(result, "dry-run")
                self.assertFalse((config_dir / "brew_upgrade.json").exists())
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_unsupported_off_macos(self) -> None:
        """Report unsupported on other platforms."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = {key: os.environ.get(key) for key in ("XDG_CONFIG_HOME",)}
            try:
                config, _ = self.load_test_config(root)
                with patch("cleanup.sys.platform", "linux"):
                    result = update_homebrew(
                        config, datetime.now().astimezone(), [], [], dry_run=False
                    )
                self.assertEqual(result, "unsupported")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_missing_brew_is_skipped(self) -> None:
        """Record a skip when no Homebrew installation exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = {key: os.environ.get(key) for key in ("XDG_CONFIG_HOME",)}
            try:
                config, _ = self.load_test_config(root)
                skipped: list[str] = []
                with (
                    patch("cleanup.sys.platform", "darwin"),
                    patch("cleanup.find_brew", return_value=None),
                ):
                    result = update_homebrew(
                        config, datetime.now().astimezone(), [], skipped, dry_run=False
                    )
                self.assertEqual(result, "unavailable")
                self.assertEqual(skipped, ["Homebrew updates"])
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_find_brew_falls_back_to_user_prefix(self) -> None:
        """Find a user-local Homebrew that is absent from PATH."""
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            brew = home / "homebrew" / "bin" / "brew"
            brew.parent.mkdir(parents=True)
            brew.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            brew.chmod(0o755)
            with (
                patch("cleanup.shutil.which", return_value=None),
                patch.object(Path, "home", return_value=home),
            ):
                self.assertEqual(find_brew(), str(brew))


if __name__ == "__main__":
    unittest.main()
