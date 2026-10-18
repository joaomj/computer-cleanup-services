"""The daily pass cleans low-risk data alone and asks once about high-risk data."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cleanup_sandbox import Sandbox


class DailyCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Sandbox(TMP_CLEANUP_ENABLED="true", PROJECT_ARTIFACTS_CLEANUP_ENABLED="true")
        self.addCleanup(self.box.close)

    def test_low_risk_is_automatic_and_high_risk_decisions_are_remembered(self) -> None:
        box = self.box
        download = box.tmp / "old-download"
        download.write_bytes(b"d" * 100)
        secret = box.tmp / "deploy-token.txt"
        secret.write_text("never delete")
        modules = box.projects / "web" / "node_modules"
        (modules / "pkg").mkdir(parents=True)
        (modules / "pkg" / "index.js").write_bytes(b"m" * 1000)
        api_modules = box.projects / "api" / "node_modules"
        api_modules.mkdir(parents=True)
        (api_modules / "index.js").write_bytes(b"a" * 1000)
        venv = box.projects / "api" / ".venv"
        venv.mkdir(parents=True)
        (venv / "lib.py").write_bytes(b"v" * 1000)
        ruff = box.projects / "web" / ".ruff_cache"
        ruff.mkdir(parents=True)
        (ruff / "cache").write_bytes(b"r" * 100)

        status = box.daily()
        self.assertFalse(download.exists(), "low-risk temp file was not cleaned")
        self.assertTrue(secret.exists())
        self.assertFalse(venv.exists(), "tool caches are cleaned without a prompt")
        self.assertFalse(ruff.exists(), "tool caches are cleaned without a prompt")
        self.assertTrue(
            modules.exists() and api_modules.exists(),
            "high-risk data deleted without a decision",
        )
        self.assertEqual(status["pending_review"], 2)
        self.assertEqual(len(box.pending()), 2)

        # Items are sorted by project: api first, then web.
        output = box.review(b"y\n", b"n\n")
        self.assertIn("~/projects/api", output)
        self.assertFalse(api_modules.exists())
        self.assertTrue(modules.exists())
        self.assertEqual(box.pending(), [])

        # A kept item is not offered again while it stays about the same size.
        self.assertEqual(box.daily()["pending_review"], 0)
        self.assertEqual(box.pending(), [])

        # It is offered again after it grows by more than 25%.
        (modules / "pkg" / "extra.js").write_bytes(b"m" * 300)
        self.assertEqual(box.daily()["pending_review"], 1)
        box.review(b"y\n")
        self.assertFalse(modules.exists())

    def test_quit_and_no_terminal_leave_items_for_later(self) -> None:
        box = self.box
        modules = box.projects / "web" / "node_modules"
        modules.mkdir(parents=True)
        (modules / "index.js").write_bytes(b"m" * 10)
        box.daily()
        box.run("--review", "--force")
        box.review(b"q\n")
        self.assertTrue(modules.exists())
        self.assertEqual(len(box.pending()), 1)


class PressureGateTest(unittest.TestCase):
    def test_review_waits_until_the_system_is_near_full(self) -> None:
        box = Sandbox(
            PROJECT_ARTIFACTS_CLEANUP_ENABLED="true",
            HIGH_RISK_REVIEW_DISK_PERCENT="100",
            HIGH_RISK_REVIEW_MEMORY_PERCENT="0",
        )
        self.addCleanup(box.close)
        modules = box.projects / "web" / "node_modules"
        modules.mkdir(parents=True)
        (modules / "index.js").write_bytes(b"m" * 10)

        self.assertEqual(box.daily()["pending_review"], 1)
        output = box.review()
        self.assertNotIn("Delete", output)
        self.assertTrue(modules.exists())
        self.assertEqual(len(box.pending()), 1)
        record = json.loads((box.state / "review-status.json").read_text())
        self.assertEqual(record["status"], "deferred")
        self.assertNotIn("attempt_date", record)

        # Once the machine needs space, the same items are offered.
        box.environment["HIGH_RISK_REVIEW_DISK_PERCENT"] = "0"
        box.review(b"n\n")
        self.assertTrue(modules.exists())
        self.assertEqual(box.pending(), [])
        decisions = json.loads((box.state / "decisions.json").read_text())
        [decision] = decisions["items"].values()
        self.assertEqual(decision["decision"], "keep")


class ProjectFolderRuleTest(unittest.TestCase):
    def test_only_regenerable_folders_that_git_does_not_track(self) -> None:
        box = Sandbox(PROJECT_ARTIFACTS_CLEANUP_ENABLED="true")
        self.addCleanup(box.close)
        repo = box.projects / "app"
        files = {
            "src/main.py": "code",
            "api/.venv/lib.py": "tool cache",
            "api/__pycache__/module.pyc": "tool cache",
            "release/dist/app.js": "committed build",
            "web/dist/app.js": "untracked, not ignored",
            "site/.next/page.js": "ignored build",
            "node_modules/lib/index.js": "dependency",
            ".git-hooks/dist/run.sh": "hook",
        }
        for name, body in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(body)
        (repo / ".gitignore").write_text(".next/\nnode_modules/\n")

        def git(*arguments: str) -> None:
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *arguments],
                cwd=repo,
                check=True,
                capture_output=True,
            )

        git("init", "-q")
        git("add", ".gitignore", "src", "release", ".git-hooks")
        git("commit", "-q", "-m", "init")
        offered = {
            target
            for item in box.preview()["high_risk_candidates"]
            for target in item["targets"]
        }
        self.assertEqual(
            offered,
            {str(repo / "site/.next"), str(repo / "node_modules")},
        )
        box.daily()
        self.assertFalse((repo / "api/.venv").exists())
        self.assertFalse((repo / "api/__pycache__").exists())
        self.assertTrue((repo / "release/dist").exists())
        self.assertTrue((repo / "web/dist").exists())
        self.assertTrue((repo / "src/main.py").exists())


class HuggingFaceTest(unittest.TestCase):
    def test_model_files_in_use_are_never_offered(self) -> None:
        box = Sandbox(HUGGINGFACE_CLEANUP_ENABLED="true")
        self.addCleanup(box.close)
        model = box.cache / "huggingface" / "hub" / "models--org--live"
        blobs = model / "blobs"
        snapshot = model / "snapshots" / "rev1" / "weights"
        blobs.mkdir(parents=True)
        snapshot.mkdir(parents=True)
        live = blobs / "sha-live"
        live.write_bytes(b"x" * 1024)
        (snapshot / "model.safetensors").symlink_to("../../../blobs/sha-live")
        orphan = blobs / "sha-orphan"
        orphan.write_bytes(b"y" * 2048)
        partial = blobs / "sha-partial.incomplete"
        partial.write_bytes(b"p" * 256)

        box.daily()
        [item] = box.pending()
        self.assertEqual(set(item["targets"]), {str(orphan), str(partial)})
        self.assertEqual(item["size"], 2048 + 256)
        box.review(b"y\n")
        self.assertTrue(live.exists())
        self.assertFalse(orphan.exists() or partial.exists())


if __name__ == "__main__":
    unittest.main()
