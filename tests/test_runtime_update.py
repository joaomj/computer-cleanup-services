#!/usr/bin/env python3
"""Black-box checks for verified runtime update decisions."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from config import SERVICE_NAME, load_config
from runtime_update import (
    Release,
    RuntimeUpdateError,
    Version,
    _cleartext_body,
    _manifest_hash,
    _runtime_plan,
    _safe_tar_path,
    parse_release_timestamp,
    parse_version,
    release_is_eligible,
    update_runtimes,
)


class RuntimeUpdateTest(unittest.TestCase):
    def release(self, version: str, published_at: str) -> Release:
        parsed = parse_version(version)
        return Release(
            version=parsed,
            published_at=parse_release_timestamp(published_at),
            archive_name="runtime.zip",
            archive_url="https://example.test/runtime.zip",
            manifest_url="https://example.test/SHASUMS256.txt.asc",
            kind="bun",
        )

    def test_version_parser_rejects_prerelease_and_partial_versions(self) -> None:
        self.assertEqual(parse_version("v24.19.0"), Version(24, 19, 0))
        with self.assertRaises(RuntimeUpdateError):
            parse_version("24.19")
        with self.assertRaises(RuntimeUpdateError):
            parse_version("24.19.0-rc.1")

    def test_release_age_requires_full_seven_days(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)
        eligible = self.release("1.4.0", "2026-08-15T12:00:00Z")
        young = self.release("1.4.1", "2026-08-15T12:00:01Z")
        self.assertTrue(release_is_eligible(eligible, now, 7))
        self.assertFalse(release_is_eligible(young, now, 7))

    def test_runtime_plan_blocks_major_changes_before_age_or_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir) / "config"
            config_dir = config_home / SERVICE_NAME
            config_dir.mkdir(parents=True)
            (config_dir / "environment").write_text(
                "RUNTIME_NODE_MAJOR=24\nRUNTIME_MIN_RELEASE_AGE_DAYS=7\n", encoding="utf-8"
            )
            original = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = str(config_home)
            try:
                config = load_config()
            finally:
                if original is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original
        plan = _runtime_plan(
            self.release("25.0.0", "2026-08-01T00:00:00Z"),
            Version(24, 19, 0),
            config,
            datetime.now(timezone.utc),
        )
        self.assertEqual(plan["status"], "blocked-major")

    def test_manifest_hash_uses_the_signed_archive_entry(self) -> None:
        digest = "a" * 64
        manifest = (
            "-----BEGIN PGP SIGNED MESSAGE-----\n"
            "Hash: SHA256\n"
            "\n"
            f"{digest}  runtime.zip\n"
            "-----BEGIN PGP SIGNATURE-----\n"
            "not-used-in-this-parser\n"
            "-----END PGP SIGNATURE-----\n"
        ).encode()
        self.assertEqual(_manifest_hash(manifest, "runtime.zip"), digest)
        self.assertEqual(_cleartext_body(manifest), f"{digest}  runtime.zip\n")

    def test_manifest_hash_rejects_missing_archive(self) -> None:
        manifest = (
            "-----BEGIN PGP SIGNED MESSAGE-----\n"
            "Hash: SHA256\n\n"
            f"{'a' * 64}  another.zip\n"
            "-----BEGIN PGP SIGNATURE-----\n"
            "signature\n"
            "-----END PGP SIGNATURE-----\n"
        ).encode()
        with self.assertRaises(RuntimeUpdateError):
            _manifest_hash(manifest, "runtime.zip")

    def test_archive_symlink_may_use_safe_parent_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = _safe_tar_path(root, "node-v24.19.0/bin/npm", "../lib/node_modules/npm/bin/npm-cli.js")
            self.assertEqual(target, (root / "node-v24.19.0/bin/npm").resolve())
            with self.assertRaises(RuntimeUpdateError):
                _safe_tar_path(root, "node-v24.19.0/bin/npm", "../../../../outside")

    def test_runtime_updates_are_disabled_outside_macos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir) / "config"
            config_dir = config_home / SERVICE_NAME
            config_dir.mkdir(parents=True)
            original = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = str(config_home)
            try:
                result = update_runtimes(load_config(), datetime.now(timezone.utc), dry_run=False)
            finally:
                if original is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original
        if sys.platform == "darwin":
            self.assertNotEqual(result["status"], "disabled")
        else:
            self.assertEqual(result["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
