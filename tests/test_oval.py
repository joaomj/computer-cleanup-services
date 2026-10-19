#!/usr/bin/env python3
"""Ubuntu OVAL evaluation: CVE metadata and matched installed packages."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import oval
from config import SERVICE_NAME, load_config


DEFINITIONS = """<?xml version="1.0"?>
<oval_definitions>
  <definitions>
    <definition id="def-1" class="vulnerability">
      <metadata>
        <title>CVE-2026-99999 on Ubuntu 26.04 LTS</title>
        <advisory>
          <cve cvss3="9.3" impact="medium">CVE-2026-99999</cve>
        </advisory>
      </metadata>
      <criteria>
        <criterion test_ref="tst-1" comment="linux source package in resolute, is affected and has been fixed."/>
        <criterion test_ref="tst-2" comment="Is package 'linux-aws' installed?"/>
      </criteria>
    </definition>
    <definition id="def-2" class="vulnerability">
      <metadata>
        <title>CVE-2026-11111 on Ubuntu 26.04 LTS</title>
        <advisory>
          <cve cvss3="7.5" impact="high">CVE-2026-11111</cve>
        </advisory>
      </metadata>
      <criteria>
        <criterion test_ref="tst-3" comment="Is package 'foo' installed?"/>
      </criteria>
    </definition>
  </definitions>
</oval_definitions>
"""

RESULTS = """<?xml version="1.0"?>
<oval_results>
  <results>
    <definitions>
      <definition definition_id="def-1" class="vulnerability" result="true"/>
      <definition definition_id="def-2" class="vulnerability" result="false"/>
    </definitions>
  </results>
</oval_results>
"""


class OvalEvaluationTest(unittest.TestCase):
    def test_reports_only_true_cves_with_installed_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_home = root / "config"
            config_dir = config_home / SERVICE_NAME
            config_dir.mkdir(parents=True)
            (config_dir / "environment").write_text("", encoding="utf-8")
            with (
                patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True),
                patch.object(Path, "home", return_value=root / "home"),
                patch("config.sys.platform", "linux"),
            ):
                config = load_config()
            state_dir = root / "state"
            oval_dir = state_dir / "oval"
            oval_dir.mkdir(parents=True)
            (oval_dir / "com.ubuntu.resolute.cve.oval.xml").write_text(
                DEFINITIONS, encoding="utf-8"
            )
            oscap = root / "oscap"
            oscap.write_text(
                "#!/bin/sh\n"
                'cat > "$4" <<\'XML\'\n'
                f"{RESULTS}"
                "XML\n",
                encoding="utf-8",
            )
            oscap.chmod(0o755)
            config = replace(config, state_dir=state_dir)
            with (
                patch("oval.sys.platform", "linux"),
                patch("oval.ubuntu_release", return_value=("ubuntu", "resolute")),
                patch("oval.shutil.which", return_value=str(oscap)),
                patch("oval._download_feed", return_value=None),
                patch("oval._installed_source_packages", return_value={"linux"}),
            ):
                result = oval.scan_ubuntu(config)

        self.assertEqual(result["status"], "findings")
        [finding] = result["findings"]
        self.assertEqual(finding["id"], "CVE-2026-99999")
        self.assertEqual(finding["severity"], "critical")
        self.assertEqual(finding["cvss"], 9.3)
        self.assertEqual(finding["priority"], "medium")
        self.assertEqual(finding["packages"], ["linux"])
        self.assertEqual(result["evaluated"], 1)


if __name__ == "__main__":
    unittest.main()
