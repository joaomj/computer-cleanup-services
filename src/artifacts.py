"""Project artifact rules for automatic and reviewed cleanup.

Cleanup touches only exact directory names and never descends into `.git` or
`.pi`. Tool caches are regenerable, so they are deleted automatically. Node
dependencies and build output are expensive to restore or can be hand-written
code, so they need a recorded decision. A folder that Git tracks is never
deleted, and a build-output name is deleted only when Git ignores it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from common import (
    ProcessPaths,
    inspect_tree,
    mark_skipped,
    remove_path,
)
from config import Config

# Regenerable tool caches: deleted automatically when no program uses them.
LOW_RISK_DIRS = frozenset(
    {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
)
# Dependencies and build output: expensive to restore or possibly hand-written.
BUILD_OUTPUT_DIRS = frozenset({"dist", ".next", ".nx", "coverage"})
HIGH_RISK_DIRS = frozenset({"node_modules"}) | BUILD_OUTPUT_DIRS
ARTIFACT_DIRS = LOW_RISK_DIRS | HIGH_RISK_DIRS
NEVER_DESCEND = frozenset({".git", ".pi"})
PROTECTED_CONTENT = frozenset({".git", ".pi", ".ssh", ".gnupg"})


def protected(name: str) -> bool:
    """Never delete Git data, editor state, or credentials inside an artifact."""
    return name in PROTECTED_CONTENT


def artifact_workspace(path: Path, root: Path) -> Path:
    """Return the nearest Git repository, or the top-level project folder."""
    for parent in path.parents:
        if parent == root:
            break
        if (parent / ".git").exists():
            return parent
    return root / path.relative_to(root).parts[0]


def _git(repo: Path, config: Config, *args: str) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("git is not installed")
    return subprocess.run(
        [git, "-C", str(repo), *args],
        check=False,
        capture_output=True,
        timeout=config.command_timeout_seconds,
        cwd="/",
    )


def git_allows_deletion(path: Path, workspace: Path, config: Config) -> str:
    """Return "eligible" or the Git reason that protects an artifact folder."""
    if not (workspace / ".git").exists():
        return (
            "eligible"
            if path.name in LOW_RISK_DIRS | {"node_modules"}
            else "not-ignored"
        )
    relative = str(path.relative_to(workspace))
    try:
        tracked = _git(workspace, config, "ls-files", "-z", "--", relative)
        if tracked.returncode != 0:
            return "git-error"
        if tracked.stdout:
            return "tracked"
        if path.name in BUILD_OUTPUT_DIRS:
            ignored = _git(workspace, config, "check-ignore", "-q", "--", relative)
            if ignored.returncode == 1:
                return "not-ignored"
            if ignored.returncode != 0:
                return "git-error"
    except (OSError, subprocess.TimeoutExpired):
        return "git-error"
    return "eligible"


def artifact_status(
    path: Path, workspace: Path, cutoff: float, config: Config
) -> tuple[str, int]:
    """Check Git state, ownership, age, and content of one artifact folder."""
    if path.is_symlink() or not path.is_dir():
        return "not-a-directory", 0
    status = git_allows_deletion(path, workspace, config)
    if status != "eligible":
        return status, 0
    return inspect_tree(path, cutoff, protected)


def find_artifacts(
    root: Path,
    names: frozenset[str],
    warnings: list[str],
    skipped: list[str],
) -> list[Path]:
    """Find folders named in `names` without descending into them or into Git data."""
    found: list[Path] = []

    def on_error(error: OSError) -> None:
        if isinstance(error, PermissionError):
            mark_skipped(skipped, "project folders without read permission")
        else:
            warnings.append(f"project scan failed at {error.filename}: {error}")

    for directory, children, _ in os.walk(root, onerror=on_error):
        here = Path(directory)
        descend = []
        for name in children:
            path = here / name
            if name in NEVER_DESCEND or path.is_symlink():
                continue
            if name in names:
                found.append(path)
            else:
                descend.append(name)
        children[:] = descend
    return found


def clean_project_caches(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Delete regenerable tool caches in projects without a prompt.

    A project with a running program inside it is left alone. Every folder is
    checked again immediately before deletion.
    """
    result = {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    root = config.project_root
    if not root.is_dir() or root.is_symlink():
        return result
    snapshot = ProcessPaths.capture()
    if snapshot.uncertain:
        mark_skipped(skipped, "project cache cleanup (running programs could not be inspected)")
        return result
    cutoff = now.timestamp() - config.project_cache_max_age_hours * 3600
    eligible: list[tuple[Path, Path, int]] = []
    for path in find_artifacts(root, LOW_RISK_DIRS, warnings, skipped):
        workspace = artifact_workspace(path, root)
        if snapshot.uses(workspace):
            result["kept"] += 1
            continue
        try:
            status, size = artifact_status(path, workspace, cutoff, config)
        except PermissionError:
            mark_skipped(skipped, "project folders without read permission")
            continue
        if status != "eligible":
            result["kept"] += 1
            continue
        eligible.append((path, workspace, size))
    if dry_run:
        result["deleted"] = len(eligible)
        result["deleted_bytes"] = sum(size for _, _, size in eligible)
        return result
    snapshot = ProcessPaths.capture()
    for path, workspace, size in eligible:
        try:
            if snapshot.uses(workspace):
                result["kept"] += 1
                continue
            status, size = artifact_status(path, workspace, cutoff, config)
            if status != "eligible":
                result["kept"] += 1
                continue
            remove_path(path)
            result["deleted"] += 1
            result["deleted_bytes"] += size
        except FileNotFoundError:
            continue
        except PermissionError:
            mark_skipped(skipped, "project cache cleanup")
        except OSError as error:
            warnings.append(f"project cache cleanup failed for {path}: {error}")
    return result
