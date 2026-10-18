"""High-risk cleanup: candidate discovery and re-validated deletion.

High-risk data can hold application state or take long to restore. The daily
pass only discovers candidates. Deletion happens after a terminal decision,
and every target is checked again immediately before it is removed.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifacts import (
    HIGH_RISK_DIRS,
    artifact_status,
    artifact_workspace,
    find_artifacts,
)
from common import (
    ProcessPaths,
    inspect_tree,
    mark_skipped,
    permission_limited,
    remove_path,
    within,
)
from config import Config

DOCKER_DEFAULT_NETWORKS = frozenset({"bridge", "host", "none"})
_DOCKER_TIME = re.compile(r"^(.*?T\d\d:\d\d:\d\d)(?:\.(\d+))?(.*)$")


@dataclass(frozen=True)
class Candidate:
    """One high-risk item that needs a recorded decision."""

    key: str
    kind: str
    label: str
    size: int
    targets: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "label": self.label,
            "size": self.size,
            "targets": list(self.targets),
        }

    @classmethod
    def from_json(cls, value: Any) -> "Candidate":
        try:
            return cls(
                str(value["key"]),
                str(value["kind"]),
                str(value["label"]),
                int(value["size"]),
                tuple(str(target) for target in value["targets"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"invalid high-risk candidate record: {error}") from error


def _cutoff(now: datetime, hours: int) -> float:
    return now.timestamp() - hours * 3600


def _display(path: Path, config: Config) -> str:
    try:
        return "~/" + str(path.relative_to(config.home))
    except ValueError:
        return str(path)


def project_candidates(
    config: Config,
    now: datetime,
    snapshot: ProcessPaths,
    warnings: list[str],
    skipped: list[str],
) -> list[Candidate]:
    """Group eligible dependency and build folders into one decision per project."""
    root = config.project_root
    if not root.is_dir() or root.is_symlink():
        return []
    cutoff = _cutoff(now, config.project_artifacts_max_age_hours)
    groups: dict[Path, list[tuple[Path, int]]] = {}
    for path in find_artifacts(root, HIGH_RISK_DIRS, warnings, skipped):
        workspace = artifact_workspace(path, root)
        # Any program working inside the project protects all of its artifacts.
        if snapshot.uses(workspace):
            continue
        try:
            status, size = artifact_status(path, workspace, cutoff, config)
        except PermissionError:
            mark_skipped(skipped, "project folders without read permission")
            continue
        if status == "eligible":
            groups.setdefault(workspace, []).append((path, size))
    candidates = []
    for workspace, entries in sorted(groups.items()):
        entries.sort()
        names = ", ".join(sorted({path.name for path, _ in entries}))
        count = len(entries)
        label = f"{_display(workspace, config)}: {names} ({count} folder{'s' if count != 1 else ''})"
        candidates.append(
            Candidate(
                f"project:{workspace}",
                "project",
                label,
                sum(size for _, size in entries),
                tuple(str(path) for path, _ in entries),
            )
        )
    return candidates


# Application data folders


def entry_candidates(
    kind: str,
    root: Path,
    label: str,
    config: Config,
    now: datetime,
    snapshot: ProcessPaths,
    skipped: list[str],
) -> list[Candidate]:
    """Offer each old, unused direct child of an application data folder."""
    if not root.is_dir() or root.is_symlink():
        return []
    cutoff = _cutoff(now, config.cache_max_age_hours)
    try:
        children = sorted(root.iterdir())
    except PermissionError:
        mark_skipped(skipped, f"{label} inspection")
        return []
    candidates = []
    for child in children:
        if child.is_symlink() or snapshot.uses(child):
            continue
        status, size = inspect_tree(child, cutoff)
        if status == "eligible":
            candidates.append(
                Candidate(
                    f"{kind}:{child}",
                    kind,
                    f"{label}: {_display(child, config)}",
                    size,
                    (str(child),),
                )
            )
    return candidates


def huggingface_orphans(
    model: Path, cutoff: float, snapshot: ProcessPaths
) -> list[tuple[Path, int]]:
    """List old, unused blobs that no snapshot references.

    Raises when snapshots cannot be inspected, because a missed reference would
    turn a live model file into an orphan.
    """
    referenced: set[str] = set()
    snapshots = model / "snapshots"
    if snapshots.exists():
        if snapshots.is_symlink() or not snapshots.is_dir():
            raise RuntimeError(f"refusing uninspectable Hugging Face snapshots: {snapshots}")
        pending = [snapshots]
        while pending:
            directory = pending.pop()
            for entry in directory.iterdir():
                if entry.is_symlink():
                    if entry.is_dir():
                        raise RuntimeError(f"refusing snapshot directory symlink: {entry}")
                    referenced.add(Path(os.readlink(entry)).name)
                elif entry.is_dir():
                    pending.append(entry)
    blobs = model / "blobs"
    if not blobs.is_dir() or blobs.is_symlink():
        return []
    orphans = []
    for entry in sorted(blobs.iterdir()):
        if entry.name in referenced or entry.is_symlink() or not entry.is_file():
            continue
        info = entry.stat()
        if max(info.st_mtime, info.st_ctime) >= cutoff or snapshot.uses(entry):
            continue
        orphans.append((entry, info.st_size))
    return orphans


def _huggingface_models(config: Config) -> list[Path]:
    hub = config.huggingface_cache / "hub"
    if not hub.is_dir() or hub.is_symlink():
        return []
    return sorted(
        child
        for child in hub.iterdir()
        if child.name.startswith("models--") and child.is_dir() and not child.is_symlink()
    )


def huggingface_candidates(
    config: Config, now: datetime, snapshot: ProcessPaths, skipped: list[str]
) -> list[Candidate]:
    """Offer unreferenced downloads, grouped by model."""
    cutoff = _cutoff(now, config.cache_max_age_hours)
    candidates = []
    try:
        models = _huggingface_models(config)
        for model in models:
            orphans = huggingface_orphans(model, cutoff, snapshot)
            if not orphans:
                continue
            name = model.name.removeprefix("models--").replace("--", "/")
            count = len(orphans)
            candidates.append(
                Candidate(
                    f"huggingface:{model}",
                    "huggingface",
                    f"Hugging Face {name}: {count} unused downloaded file{'s' if count != 1 else ''}",
                    sum(size for _, size in orphans),
                    tuple(str(path) for path, _ in orphans),
                )
            )
    except PermissionError:
        mark_skipped(skipped, "Hugging Face inspection")
        return []
    return candidates


# Docker images and networks


def _docker(config: Config, *args: str) -> tuple[int, str, str]:
    docker = shutil.which("docker")
    if docker is None:
        return 127, "", "docker is not installed"
    try:
        result = subprocess.run(
            [docker, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=config.command_timeout_seconds,
            cwd="/",
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"docker {args[0]} timed out"
    except OSError as error:
        return 127, "", str(error)
    return result.returncode, result.stdout, result.stderr.strip()


def _docker_time(value: str) -> float:
    match = _DOCKER_TIME.match(value.strip().replace("Z", "+00:00"))
    if match is None:
        raise RuntimeError(f"unexpected Docker timestamp: {value}")
    fraction = (match.group(2) or "0")[:6].ljust(6, "0")
    moment = datetime.fromisoformat(f"{match.group(1)}.{fraction}{match.group(3)}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _docker_problem(error: str, warnings: list[str], skipped: list[str]) -> list[Candidate]:
    if permission_limited(error):
        mark_skipped(skipped, "Docker image and network review")
    else:
        warnings.append(f"Docker image and network review failed: {error or 'unknown error'}")
    return []


def docker_candidates(
    config: Config, now: datetime, warnings: list[str], skipped: list[str]
) -> list[Candidate]:
    """Offer old images that no container uses, and old unused networks."""
    if shutil.which("docker") is None:
        mark_skipped(skipped, "Docker image and network review")
        return []
    cutoff = _cutoff(now, config.docker_prune_max_age_hours)
    code, out, error = _docker(config, "image", "ls", "-q", "--no-trunc")
    if code != 0:
        return _docker_problem(error, warnings, skipped)
    image_ids = sorted(set(out.split()))
    code, out, error = _docker(config, "ps", "-aq", "--no-trunc")
    if code != 0:
        return _docker_problem(error, warnings, skipped)
    used: set[str] = set()
    if out.split():
        code, out, error = _docker(
            config, "container", "inspect", "--format", "{{.Image}}", *out.split()
        )
        if code != 0:
            return _docker_problem(error, warnings, skipped)
        used = set(out.split())
    candidates = []
    if image_ids:
        code, out, error = _docker(
            config,
            "image",
            "inspect",
            "--format",
            '{{.Id}}|{{.Size}}|{{.Created}}|{{join .RepoTags ","}}',
            *image_ids,
        )
        if code != 0:
            return _docker_problem(error, warnings, skipped)
        for line in out.splitlines():
            image_id, size, created, tags = line.split("|", 3)
            if image_id in used or _docker_time(created) >= cutoff:
                continue
            names = tuple(tag for tag in tags.split(",") if tag and tag != "<none>:<none>")
            shown = ", ".join(names) or image_id.removeprefix("sha256:")[:12]
            candidates.append(
                Candidate(
                    f"docker-image:{image_id}",
                    "docker-image",
                    f"Docker image {shown}",
                    int(size),
                    names or (image_id,),
                )
            )
    code, out, error = _docker(config, "network", "ls", "-q", "--filter", "dangling=true")
    if code != 0:
        return _docker_problem(error, warnings, skipped) or candidates
    if out.split():
        code, out, error = _docker(
            config, "network", "inspect", "--format", "{{.Name}}|{{.Created}}", *out.split()
        )
        if code != 0:
            return _docker_problem(error, warnings, skipped) or candidates
        names = sorted(
            name
            for name, created in (line.split("|", 1) for line in out.splitlines())
            if name not in DOCKER_DEFAULT_NETWORKS and _docker_time(created) < cutoff
        )
        if names:
            digest = hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]
            candidates.append(
                Candidate(
                    f"docker-networks:{digest}",
                    "docker-networks",
                    f"Unused Docker networks: {', '.join(names)}",
                    0,
                    tuple(names),
                )
            )
    return candidates


# Entry points


def discover(
    config: Config, now: datetime, warnings: list[str], skipped: list[str]
) -> list[Candidate]:
    """Find high-risk candidates for the selected actions."""
    snapshot = ProcessPaths.capture()
    if snapshot.uncertain:
        mark_skipped(skipped, "high-risk review (running programs could not be inspected)")
        return []
    candidates: list[Candidate] = []
    if config.project_artifacts_cleanup_enabled:
        candidates += project_candidates(config, now, snapshot, warnings, skipped)
    if config.zed_cleanup_enabled:
        candidates += entry_candidates(
            "zed-data", config.zed_data, "Zed saved data", config, now, snapshot, skipped
        )
    if config.playwright_cleanup_enabled:
        candidates += entry_candidates(
            "playwright",
            config.playwright_cache,
            "Playwright browser",
            config,
            now,
            snapshot,
            skipped,
        )
    if config.huggingface_cleanup_enabled:
        candidates += huggingface_candidates(config, now, snapshot, skipped)
    if config.docker_prune_enabled:
        candidates += docker_candidates(config, now, warnings, skipped)
    return candidates


def delete(candidate: Candidate, config: Config, now: datetime) -> dict[str, Any]:
    """Delete an approved candidate after checking every target again.

    Returns the bytes deleted and the targets kept, with reasons.
    """
    snapshot = ProcessPaths.capture()
    if snapshot.uncertain:
        raise RuntimeError("running programs could not be inspected; nothing deleted")
    deleted = 0
    kept: list[str] = []
    if candidate.kind == "project":
        root = config.project_root
        cutoff = _cutoff(now, config.project_artifacts_max_age_hours)
        for target in candidate.targets:
            path = Path(target)
            if not within(path, root) or path == root or path.name not in HIGH_RISK_DIRS:
                raise RuntimeError(f"refusing project target outside cleanup rules: {path}")
            workspace = artifact_workspace(path, root)
            if snapshot.uses(workspace):
                kept.append(f"{path}: project in use")
                continue
            status, size = artifact_status(path, workspace, cutoff, config)
            if status != "eligible":
                kept.append(f"{path}: {status}")
                continue
            remove_path(path)
            deleted += size
    elif candidate.kind in {"zed-data", "playwright"}:
        root = config.zed_data if candidate.kind == "zed-data" else config.playwright_cache
        cutoff = _cutoff(now, config.cache_max_age_hours)
        for target in candidate.targets:
            path = Path(target)
            if path.parent != root:
                raise RuntimeError(f"refusing target outside {root}: {path}")
            if snapshot.uses(path):
                kept.append(f"{path}: in use")
                continue
            status, size = inspect_tree(path, cutoff)
            if status != "eligible":
                kept.append(f"{path}: {status}")
                continue
            remove_path(path)
            deleted += size
    elif candidate.kind == "huggingface":
        hub = config.huggingface_cache / "hub"
        approved = set(candidate.targets)
        models = {Path(target).parents[1] for target in candidate.targets}
        if any(model.parent != hub for model in models):
            raise RuntimeError(f"refusing Hugging Face target outside {hub}")
        cutoff = _cutoff(now, config.cache_max_age_hours)
        still_orphaned: set[str] = set()
        for model in models:
            for path, size in huggingface_orphans(model, cutoff, snapshot):
                if str(path) in approved:
                    path.unlink()
                    deleted += size
                    still_orphaned.add(str(path))
        kept.extend(f"{target}: now in use or referenced" for target in approved - still_orphaned)
    elif candidate.kind == "docker-image":
        # Without --force, Docker refuses images that a container uses.
        code, _, error = _docker(config, "image", "rm", *candidate.targets)
        if code != 0:
            kept.append(f"{', '.join(candidate.targets)}: {error or 'docker image rm failed'}")
        else:
            deleted = candidate.size
    elif candidate.kind == "docker-networks":
        for name in candidate.targets:
            code, _, error = _docker(config, "network", "rm", name)
            if code != 0:
                kept.append(f"{name}: {error or 'docker network rm failed'}")
    else:
        raise RuntimeError(f"unknown high-risk candidate kind: {candidate.kind}")
    return {"deleted_bytes": deleted, "kept": kept}
