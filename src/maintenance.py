#!/usr/bin/env python3
"""Run automatic upgrades or interactively approved cleanup."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from cleanup import execute
from config import Config, ConfigurationError, load_config
from state import cleanup_lock, write_json


def cleanup_scopes(config: Config) -> list[str]:
    """Describe enabled cleanup scopes before asking for deletion approval."""
    scopes = []
    for enabled, label, path in (
        (config.uv_cleanup_enabled, "UV cache", config.uv_cache),
        (config.brave_cleanup_enabled, "Brave cache when stopped", config.brave_cache),
        (config.npm_cleanup_enabled, "npm cache", config.npm_cache),
        (config.yarn_cleanup_enabled, "Yarn cache", config.yarn_cache),
        (
            config.playwright_cleanup_enabled,
            "Downloaded Playwright browsers",
            config.playwright_cache,
        ),
        (
            config.zed_cleanup_enabled,
            "Zed data (may contain persistent state)",
            config.zed_data,
        ),
        (config.zed_cleanup_enabled, "Zed cache", config.zed_cache),
        (
            config.huggingface_cleanup_enabled,
            "Unreferenced Hugging Face downloads",
            config.huggingface_cache,
        ),
        (
            config.zed_server_cleanup_enabled,
            "Old Zed remote servers",
            config.zed_server,
        ),
        (
            config.project_artifacts_cleanup_enabled,
            "Project dependencies and build output",
            config.project_root,
        ),
        (config.tmp_cleanup_enabled, "Old user-owned temporary files", config.tmp_dir),
    ):
        if enabled:
            scopes.append(f"{label}: {path}")
    if config.docker_prune_enabled:
        scopes.append(
            "Unused Docker containers, images, build cache, and networks in the current context; volumes are preserved"
        )
    if sys.platform == "darwin" and config.brew_cleanup_enabled:
        scopes.append("Homebrew old package versions and downloaded packages")
    if sys.platform == "linux":
        if config.apt_cache_cleanup_enabled:
            scopes.append("APT downloaded package cache")
        if config.journal_cleanup_enabled:
            scopes.append(
                f"Archived system and user journals above {config.journal_warn_size} bytes"
            )
    return scopes


def read_record(path: Path) -> dict[str, Any]:
    """Read state, treating a missing first-run record as empty."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid maintenance state in {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"maintenance state must be an object: {path}")
    return value


def request_cleanup(config: Config) -> dict[str, Any]:
    """Preview enabled scopes and require Y from a real terminal."""
    scopes = cleanup_scopes(config)
    if not scopes:
        return {"status": "success", "decision": "no-actions-selected"}
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return {
            "status": "success",
            "decision": "no-terminal",
            "deletions_approved": False,
        }
    print("\nSelected cleanup scopes:")
    for scope in scopes:
        print(f"  - {scope}")
    print("\nCleanup preview (estimates, not an exact file inventory):")
    preview = execute(config, dry_run=True, cleanup_only=True)
    print(json.dumps(preview, indent=2, sort_keys=True))
    if preview.get("errors"):
        raise RuntimeError("cleanup preview failed; no deletion was authorized")
    print("\nCleanup can permanently remove data in these scopes.")
    print(
        "Eligibility is checked again when cleanup runs. New eligible entries may be included."
    )
    while True:
        try:
            answer = input("Proceed with cleanup deletions? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCleanup declined.")
            answer = "n"
        if answer in {"", "n", "no"}:
            return {
                "status": "success",
                "decision": "declined",
                "deletions_approved": False,
            }
        if answer in {"y", "yes"}:
            result = execute(
                config, dry_run=False, cleanup_only=True, deletions_approved=True
            )
            result["decision"] = "approved"
            return result
        print("Enter Y or N.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--updates-only",
        action="store_true",
        help="run automatic upgrades without cleanup deletions",
    )
    modes.add_argument(
        "--cleanup-only",
        action="store_true",
        help="preview cleanup and ask for Y/N in a terminal",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect without upgrades or cleanup deletions",
    )
    parser.add_argument(
        "--force", action="store_true", help="ignore today's update or cleanup decision"
    )
    args = parser.parse_args()
    config = load_config()
    today = datetime.now().astimezone().date().isoformat()
    record_path = (
        config.state_dir / "cleanup-status.json"
        if args.cleanup_only
        else config.status_path
    )
    with cleanup_lock(config) as acquired:
        if not acquired:
            if args.cleanup_only and sys.stdout.isatty():
                print(
                    "Maintenance is already running. Try cleanup again after it finishes."
                )
            return 0
        previous = read_record(record_path)
        if (
            not args.force
            and not args.dry_run
            and previous.get("attempt_date") == today
            and previous.get("status") in {"success", "warning"}
        ):
            return 0
        try:
            if args.dry_run:
                result = execute(
                    config,
                    True,
                    updates_only=args.updates_only,
                    cleanup_only=args.cleanup_only,
                )
                print(json.dumps(result, indent=2, sort_keys=True))
                return 0 if result["status"] != "failed" else 1
            if args.cleanup_only:
                result = request_cleanup(config)
            else:
                write_json(record_path, {"status": "running", "attempt_date": today})
                result = execute(config, False, updates_only=True)
                if not args.updates_only:
                    result["cleanup"] = request_cleanup(config)
            result["attempt_date"] = today
            result["finished_at"] = (
                datetime.now().astimezone().isoformat(timespec="seconds")
            )
            # A missing terminal does not consume the next interactive decision.
            if result.get("decision") != "no-terminal":
                write_json(record_path, result)
            return 0 if result["status"] != "failed" else 1
        except (ConfigurationError, OSError, RuntimeError) as error:
            write_json(
                record_path,
                {"status": "failed", "attempt_date": today, "errors": [str(error)]},
            )
            print(f"Maintenance failed: {error}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
