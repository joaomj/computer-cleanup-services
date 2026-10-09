"""Install/uninstall with disposable homes and intercepted service managers."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from terminal_helpers import Terminal

ROOT = Path(__file__).resolve().parents[1]


class InstallationTest(unittest.TestCase):
    def test_platform_installation_preserves_configuration_and_uninstalls_with_consent(
        self,
    ) -> None:
        for platform in ("Darwin", "Linux"):
            with (
                self.subTest(platform=platform),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                home = root / "home with spaces & symbols"
                home.mkdir()
                binary = root / "bin"
                binary.mkdir()
                log = root / "commands.log"
                environment = {
                    "HOME": str(home),
                    "SHELL": "/bin/bash",
                    "PATH": f"{binary}:{os.environ['PATH']}",
                    "COMMAND_LOG": str(log),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "XDG_CONFIG_HOME": str(home / "settings"),
                }
                for name, body in {
                    "uname": f"printf '{platform}\\n'\n",
                    "id": "printf '1000\\n'\n",
                    "launchctl": 'printf "launchctl %s\\n" "$*" >> "$COMMAND_LOG"\n',
                    "systemctl": 'printf "systemctl %s\\n" "$*" >> "$COMMAND_LOG"\n',
                    "sudo": 'printf "sudo %s\\n" "$*" >> "$COMMAND_LOG"\n',
                }.items():
                    path = binary / name
                    path.write_text("#!/bin/sh\nset -eu\n" + body)
                    path.chmod(0o755)
                (binary / "python3").symlink_to(Path(sys.executable).resolve())
                service = (
                    "macos-cleanup-service"
                    if platform == "Darwin"
                    else "linux-cleanup-service"
                )
                config = home / "settings" / service / "environment"
                installed = home / ".local" / "libexec" / service
                home.joinpath(".bashrc").write_text(
                    "# User mentions macos-cleanup-service here; keep this line.\n"
                )

                def install():
                    result = subprocess.run(
                        ["bash", str(ROOT / "scripts/install")],
                        input="",
                        capture_output=True,
                        text=True,
                        env=environment,
                        cwd=ROOT,
                        timeout=20,
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )

                install()
                settings = config.read_text()
                self.assertIn("PROJECT_ARTIFACTS_CLEANUP_ENABLED=false", settings)
                config.write_text(settings + "PROJECT_ARTIFACTS_MAX_AGE_HOURS=72\n")
                install()
                self.assertTrue(
                    config.read_text().endswith("PROJECT_ARTIFACTS_MAX_AGE_HOURS=72\n")
                )
                rc = home.joinpath(".bashrc").read_text()
                self.assertEqual(rc.count(f"# {service}\n"), 1)
                self.assertIn("keep this line", rc)
                if platform == "Darwin":
                    unit = (
                        home
                        / "Library/LaunchAgents/com.user.macos-cleanup-service.plist"
                    )
                    with unit.open("rb") as file:
                        job = plistlib.load(file)
                    arguments = job["ProgramArguments"]
                    self.assertEqual(
                        arguments,
                        [
                            str(installed / "python3"),
                            str(installed / "maintenance.py"),
                            "--updates-only",
                        ],
                    )
                    self.assertEqual(
                        job["EnvironmentVariables"]["XDG_CONFIG_HOME"],
                        str(home / "settings"),
                    )
                else:
                    unit = home / "settings/systemd/user/linux-cleanup.service"
                    self.assertIn("--updates-only", unit.read_text())
                    self.assertNotIn("journalctl --vacuum", log.read_text())
                help_result = subprocess.run(
                    [
                        str(installed / "python3"),
                        str(installed / "maintenance.py"),
                        "--help",
                    ],
                    capture_output=True,
                    text=True,
                    env=environment,
                    timeout=10,
                )
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                refused = subprocess.run(
                    ["bash", str(ROOT / "scripts/uninstall")],
                    input="Y\n",
                    capture_output=True,
                    text=True,
                    env=environment,
                    cwd=ROOT,
                    timeout=10,
                )
                self.assertNotEqual(refused.returncode, 0)
                self.assertTrue(installed.exists())
                terminal = Terminal(
                    ["bash", str(ROOT / "scripts/uninstall")], environment, str(ROOT)
                )
                try:
                    terminal.read_until(b"Remove the cleanup service")
                    code, output = terminal.answer(b"Y\n")
                    self.assertEqual(code, 0, output)
                finally:
                    terminal.close()
                self.assertFalse(installed.exists())
                self.assertFalse(unit.exists())
                self.assertTrue(config.exists())
                self.assertIn("keep this line", home.joinpath(".bashrc").read_text())
                self.assertNotIn(f"# {service}\n", home.joinpath(".bashrc").read_text())


if __name__ == "__main__":
    unittest.main()
