#!/usr/bin/env python3
"""Homebrew findings are read even when brew returns a nonzero status."""

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

from config import SERVICE_NAME, load_config
from homebrew_vulns import scan_homebrew


PAYLOAD = {
    "findings": [
        {
            "formula": "libssh2",
            "version": "1.11.1",
            "vulnerabilities": [
                {
                    "id": "OSV-2025-433",
                    "severity": "HIGH",
                    "summary": "Null-dereference READ in _libssh2_packet_add",
                    "aliases": [],
                    "fixed_versions": [],
                }
            ],
            "patched": [],
        },
        {
            "formula": "openjpeg",
            "version": "2.5.4",
            "vulnerabilities": [],
            "patched": [{"id": "CVE-2026-6192", "severity": "HIGH"}],
        },
    ],
    "skipped_formulae": ["lame"],
    "checked": 2,
    "complete": False,
    "outdated_without_sbom": [],
    "unscanned_formulae": {},
}


class HomebrewScanTest(unittest.TestCase):
    def test_parses_findings_when_exit_status_is_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            brew = bin_dir / "brew"
            brew.write_text(
                "#!/bin/sh\n"
                f"cat <<'JSON'\n{json.dumps(PAYLOAD)}\nJSON\n"
                "exit 1\n",
                encoding="utf-8",
            )
            brew.chmod(0o755)
            config_home = root / "config"
            config_dir = config_home / SERVICE_NAME
            config_dir.mkdir(parents=True)
            (config_dir / "environment").write_text(
                "SECURITY_SCAN_TIMEOUT_SECONDS=5\n", encoding="utf-8"
            )
            with (
                patch.dict(
                    os.environ,
                    {
                        "XDG_CONFIG_HOME": str(config_home),
                        "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    },
                    clear=False,
                ),
                patch.object(Path, "home", return_value=root / "home"),
                patch("config.sys.platform", "linux"),
                patch("homebrew_vulns.sys.platform", "darwin"),
            ):
                config = load_config()
                result = scan_homebrew(config)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["formulae"], 2)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["id"], "OSV-2025-433")
        self.assertEqual(result["findings"][0]["severity"], "high")
        self.assertTrue(any("1 Homebrew formulae" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
