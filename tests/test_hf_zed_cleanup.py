#!/usr/bin/env python3
"""Black-box checks for Zed server GC and UV size reporting."""

from __future__ import annotations

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

from cleanup import clean_zed_server, prune_uv_cache
from common import ProcessPaths
from config import SERVICE_NAME, load_config


def make_config(root: Path, home: Path, extra: str = ""):
    """Load an isolated config with home and XDG roots under temp_dir.

    Path.home is patched during load so home-based defaults (zed_server)
    resolve inside the sandbox, never to the real home directory.
    """
    config_home = root / "config"
    config_dir = config_home / SERVICE_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "environment").write_text(
        f"COMMAND_TIMEOUT_SECONDS=5\nHUGGINGFACE_CLEANUP_ENABLED=true\nZED_SERVER_CLEANUP_ENABLED=true\n{extra}",
        encoding="utf-8",
    )
    original = {
        key: os.environ.get(key)
        for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME")
    }
    os.environ["XDG_CONFIG_HOME"] = str(config_home)
    os.environ["XDG_CACHE_HOME"] = str(root / "cache")
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    try:
        with patch.object(Path, "home", return_value=home):
            return load_config(), original
    except Exception:
        restore_env(original)
        raise


def restore_env(original: dict) -> None:
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def backdate(path: Path, days_old: float) -> None:
    """Set a file or directory mtime to a fixed age."""
    stamp = (datetime.now().astimezone() - timedelta(days=days_old)).timestamp()
    os.utime(path, (stamp, stamp))


class ZedServerTest(unittest.TestCase):
    def build_server(self, server: Path, names_ages: list[tuple[str, float]]) -> None:
        """Create fake versioned server installs with staggered mtimes."""
        server.mkdir(parents=True, exist_ok=True)
        for name, days_old in names_ages:
            install = server / name
            install.mkdir(exist_ok=True)
            (install / "server").write_bytes(b"s" * 100)
            backdate(install, days_old)

    def test_gc_keeps_two_newest_and_deletes_old_rest(self) -> None:
        """Remove superseded installs older than 24h, keep the newest two."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, original = make_config(root, root / "home")
            try:
                server = Path(config.zed_server)
                self.build_server(
                    server,
                    [
                        ("zed-remote-server-stable-1", 10),
                        ("zed-remote-server-stable-2", 5),
                        ("zed-remote-server-stable-3", 2),
                        ("zed-remote-server-stable-4", 0.01),
                    ],
                )
                (server / "notes.txt").write_text("keep me", encoding="utf-8")
                with patch("cache_cleanup.ProcessPaths.capture", return_value=ProcessPaths(set(), False)):
                    result = clean_zed_server(
                        config, datetime.now().astimezone(), False, [], []
                    )
                self.assertFalse((server / "zed-remote-server-stable-1").exists())
                self.assertFalse((server / "zed-remote-server-stable-2").exists())
                self.assertTrue((server / "zed-remote-server-stable-3").exists())
                self.assertTrue((server / "zed-remote-server-stable-4").exists())
                self.assertTrue((server / "notes.txt").exists())
                self.assertEqual(result["deleted"], 2)
                self.assertEqual(result["kept"], 3)
            finally:
                restore_env(original)

    def test_fresh_superseded_installs_are_kept(self) -> None:
        """Do not delete old versions until they pass the 24h rule."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, original = make_config(root, root / "home")
            try:
                self.build_server(
                    Path(config.zed_server),
                    [
                        ("zed-remote-server-stable-1", 0.1),
                        ("zed-remote-server-stable-2", 0.05),
                        ("zed-remote-server-stable-3", 0.01),
                    ],
                )
                with patch("cache_cleanup.ProcessPaths.capture", return_value=ProcessPaths(set(), False)):
                    result = clean_zed_server(
                        config, datetime.now().astimezone(), False, [], []
                    )
                self.assertEqual(result["deleted"], 0)
                self.assertEqual(result["kept"], 3)
            finally:
                restore_env(original)

    def test_disabled_skips_server_gc(self) -> None:
        """Honor the kill switch."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, original = make_config(
                root, root / "home", "ZED_SERVER_CLEANUP_ENABLED=false\n"
            )
            try:
                now = datetime.now().astimezone()
                self.assertEqual(
                    clean_zed_server(config, now, False, [], []),
                    {"deleted": 0, "deleted_bytes": 0, "kept": 0},
                )
            finally:
                restore_env(original)

    def test_gc_handles_versioned_files(self) -> None:
        """Treat versioned server binaries (files, not dirs) as installs."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, original = make_config(root, root / "home")
            try:
                server = Path(config.zed_server)
                server.mkdir(parents=True, exist_ok=True)
                for name, days_old in [
                    ("zed-remote-server-stable-1.18.1+stable.340.aaa", 10),
                    ("zed-remote-server-stable-1.18.1+stable.364.bbb", 5),
                    ("zed-remote-server-stable-1.18.1+stable.365.ccc", 0.01),
                ]:
                    entry = server / name
                    entry.write_bytes(b"b" * 2048)
                    backdate(entry, days_old)
                with patch("cache_cleanup.ProcessPaths.capture", return_value=ProcessPaths(set(), False)):
                    result = clean_zed_server(
                        config, datetime.now().astimezone(), False, [], []
                    )
                self.assertFalse(
                    (server / "zed-remote-server-stable-1.18.1+stable.340.aaa").exists()
                )
                self.assertTrue(
                    (server / "zed-remote-server-stable-1.18.1+stable.364.bbb").exists()
                )
                self.assertTrue(
                    (server / "zed-remote-server-stable-1.18.1+stable.365.ccc").exists()
                )
                self.assertEqual(result["deleted"], 1)
                self.assertEqual(result["kept"], 2)
            finally:
                restore_env(original)


class UvPruneReportingTest(unittest.TestCase):
    def test_missing_binary_reports_real_size(self) -> None:
        """A missing uv binary records a skip but never reports 0 bytes."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            config, original = make_config(root, home)
            try:
                cache = Path(config.uv_cache)
                cache.mkdir(parents=True)
                (cache / "wheel.bin").write_bytes(b"w" * 4096)
                with (
                    patch.object(Path, "home", return_value=home),
                    patch("cache_cleanup.shutil.which", return_value=None),
                ):
                    skipped: list[str] = []
                    result = prune_uv_cache(config, [], skipped, dry_run=False)
                self.assertGreaterEqual(result, 4096)
                self.assertEqual(skipped, ["UV cache prune"])
            finally:
                restore_env(original)

    def test_configured_cleaners_use_sandbox_paths(self) -> None:
        """Explicitly selected cleaners use sandbox paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            config, original = make_config(root, home)
            try:
                self.assertEqual(
                    config.huggingface_cache, root / "cache" / "huggingface"
                )
                self.assertEqual(config.zed_server, home / ".zed_server")
                self.assertTrue(config.huggingface_cleanup_enabled)
                self.assertTrue(config.zed_server_cleanup_enabled)
                self.assertEqual(config.zed_server_keep_count, 2)
            finally:
                restore_env(original)


if __name__ == "__main__":
    unittest.main()
