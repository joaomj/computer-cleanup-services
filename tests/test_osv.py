#!/usr/bin/env python3
"""Dependency coverage: lockfile discovery and parsing."""

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
from osv import collect_dependencies


class LockfileCoverageTest(unittest.TestCase):
    def test_discovers_and_parses_supported_lockfiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            projects = root / "projects"
            projects.mkdir()
            (projects / "package-lock.json").write_text(
                json.dumps(
                    {
                        "packages": {
                            "": {"name": "app"},
                            "node_modules/foo": {"version": "1.0.0"},
                            "node_modules/@scope/bar": {"version": "2.0.0"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (projects / "pnpm-lock.yaml").write_text(
                "lockfileVersion: '9.0'\npackages:\n  '@scope/foo@1.2.3':\n    resolution: {}\n",
                encoding="utf-8",
            )
            (projects / "requirements.txt").write_text(
                "requests==2.31.0\n# comment\n-r other.txt\n",
                encoding="utf-8",
            )
            (projects / "uv.lock").write_text(
                '[[package]]\nname = "pydantic"\nversion = "2.0.0"\n'
                'source = { registry = "https://pypi.org/simple" }\n\n'
                '[[package]]\nname = "local"\nversion = "0.1.0"\n'
                'source = { editable = "." }\n',
                encoding="utf-8",
            )
            (projects / "Cargo.lock").write_text(
                '[[package]]\nname = "serde"\nversion = "1.0.0"\n'
                'source = "registry+https://github.com/rust-lang/crates.io-index"\n',
                encoding="utf-8",
            )
            (projects / "go.sum").write_text(
                "golang.org/x/text v0.14.0 h1:abc\n"
                "golang.org/x/text v0.14.0/go.mod h1:def\n",
                encoding="utf-8",
            )
            (projects / "Gemfile.lock").write_text(
                "GEM\n  remote: https://rubygems.org/\n  specs:\n"
                "    rails (7.0.0)\n"
                "    nokogiri (1.16.0-x86_64-linux)\n\n"
                "PLATFORMS\n  ruby\n",
                encoding="utf-8",
            )
            (projects / "composer.lock").write_text(
                json.dumps(
                    {"packages": [{"name": "vendor/pkg", "version": "v1.2.3"}]}
                ),
                encoding="utf-8",
            )
            (projects / "yarn.lock").write_text("", encoding="utf-8")
            (projects / "Pipfile.lock").write_text("{}", encoding="utf-8")

            config_home = root / "config"
            config_dir = config_home / SERVICE_NAME
            config_dir.mkdir(parents=True)
            (config_dir / "environment").write_text(
                f"PROJECT_ROOT={projects}\nSECURITY_PROJECT_MAX_DEPTH=2\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True),
                patch.object(Path, "home", return_value=root / "home"),
                patch("config.sys.platform", "linux"),
            ):
                config = load_config()
                dependencies, lockfiles, warnings = collect_dependencies(config)

        found = {(item.ecosystem, item.name, item.version) for item in dependencies.values()}
        self.assertEqual(lockfiles, 10)
        self.assertEqual(
            found,
            {
                ("npm", "foo", "1.0.0"),
                ("npm", "@scope/bar", "2.0.0"),
                ("npm", "@scope/foo", "1.2.3"),
                ("PyPI", "requests", "2.31.0"),
                ("PyPI", "pydantic", "2.0.0"),
                ("crates.io", "serde", "1.0.0"),
                ("Go", "golang.org/x/text", "v0.14.0"),
                ("RubyGems", "rails", "7.0.0"),
                ("RubyGems", "nokogiri", "1.16.0"),
                ("Packagist", "vendor/pkg", "1.2.3"),
            },
        )
        self.assertFalse(any(item.name == "local" for item in dependencies.values()))
        self.assertTrue(any("yarn.lock" in warning for warning in warnings))
        self.assertTrue(any("Pipfile.lock" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
