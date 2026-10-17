#!/usr/bin/env python3
"""Run the opt-in, unattended Linux host cleanup profile."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import stat
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# These session/credential services deliberately deny process inspection. They
# do not run project jobs; their runtime and credential directories are excluded.
SESSION_SERVICES = {"systemd", "(sd-pam)", "ssh-agent", "gpg-agent"}
TMP_PREFIXES = (
    ".X", ".ICE", ".font", "systemd-", "snap-", "ssh-", "tmux-",
    "gpg-", "keyring-", "pi-",
)
SECRET_MARKERS = (
    "secret", "credential", "token", "passwd", "private-key",
    "keyring", "proton", "auth",
)
Snapshot = tuple[set[Path], set[Path], set[str]]


def protected_name(name: str) -> bool:
    lowered = name.lower()
    return (
        name in {".git", ".pi", ".ssh", ".gnupg", ".env"}
        or lowered.endswith((".pem", ".key", ".p8", ".sock", ".lock"))
        or lowered.startswith(".env.")
        or any(marker in lowered for marker in SECRET_MARKERS)
    )


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def validate_root(path: Path, home: Path) -> None:
    if not path.is_absolute() or path == home or not within(path, home):
        raise RuntimeError(f"cleanup root must be below the user's home: {path}")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeError(f"refusing symlink in cleanup root: {part}")
        if part == home:
            break


def process_snapshot(projects: Path) -> Snapshot:
    """Inspect same-account jobs; unexpected visibility gaps stop cleanup."""
    paths: set[Path] = set()
    workspaces: set[Path] = set()
    commands: set[str] = set()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            command = (process / "comm").read_text().strip()
            commands.add(command)
            if command in SESSION_SERVICES:
                continue
            links = [process / "cwd", process / "exe"]
            links.extend((process / "fd").iterdir())
            for link in links:
                try:
                    target = os.readlink(link)
                except FileNotFoundError:
                    continue
                if target.startswith("/"):
                    paths.add(Path(target.removesuffix(" (deleted)")))
            for line in (process / "maps").read_text().splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    paths.add(Path(fields[5].removesuffix(" (deleted)")))
            # Executed scripts can be closed after loading; retain absolute
            # command arguments too, without recording arguments in the report.
            for argument in (process / "cmdline").read_bytes().split(b"\0"):
                value = os.fsdecode(argument)
                if value.startswith("/"):
                    paths.add(Path(value))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, OSError) as error:
            raise RuntimeError(
                f"cannot inspect user process {process.name}; no further cleanup: {error}"
            ) from error
    for path in paths:
        if not within(path, projects) or path == projects:
            continue
        relative = path.relative_to(projects)
        workspace = projects / relative.parts[0]
        for parent in (path, *path.parents):
            if parent == projects:
                break
            if (parent / ".git").exists():
                workspace = parent
                break
        workspaces.add(workspace)
    return paths, workspaces, commands


def busy(path: Path, scope: str, snapshot: Snapshot) -> bool:
    paths, workspaces, commands = snapshot
    if scope == "uv" and commands & {"uv", "uvx"}:
        return True
    if scope == "git-cache" and "git" in commands:
        return True
    if scope == "node_modules" and any(within(path, root) for root in workspaces):
        return True
    return any(within(reference, path) for reference in paths)


def inspect_tree(path: Path, cutoff: float | None, scope: str) -> tuple[str, int]:
    """Check ownership, ages, and entry types without following symlinks."""
    total = 0
    pending = [path]
    while pending:
        entry = pending.pop()
        try:
            info = entry.lstat()
        except FileNotFoundError:
            if entry == path:
                return "disappeared", 0
            continue
        if info.st_uid != os.getuid():
            return "other-owner", 0
        if scope in {"tmp", "node_modules"} and entry.name in {".git", ".pi", ".ssh", ".gnupg"}:
            return "protected-content", 0
        if scope == "tmp" and protected_name(entry.name):
            return "protected-content", 0
        if cutoff is not None and max(info.st_mtime, info.st_ctime) >= cutoff:
            return "recent-content", 0
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(entry) as children:
                pending.extend(Path(child.path) for child in children)
        elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            total += info.st_size
        else:
            return "special-file", 0
    return "eligible", total


def candidates(home: Path, projects: Path, tmp: Path) -> Iterator[tuple[str, Path]]:
    if tmp.is_symlink() or not tmp.is_dir():
        raise RuntimeError(f"temporary directory must be a real directory: {tmp}")
    for path in sorted(tmp.iterdir()):
        if path.name.startswith(TMP_PREFIXES) or protected_name(path.name):
            continue
        yield "tmp", path
    if projects.exists():
        walker = os.walk(projects, followlinks=False, onerror=raise_walk_error)
        for directory, names, _ in walker:
            names[:] = [
                name for name in names
                if name not in {".git", ".venv", ".pi"}
                and not (Path(directory) / name).is_symlink()
            ]
            if "node_modules" in names:
                names.remove("node_modules")
                yield "node_modules", Path(directory) / "node_modules"
    for scope, root in (
        ("uv", home / ".cache/uv"),
        ("git-cache", home / ".cache/git"),
        ("git-cache", home / ".cache/git-clones"),
    ):
        validate_root(root, home)
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if path.name in {".lock", "CACHEDIR.TAG"}:
                continue
            yield scope, path


def raise_walk_error(error: OSError) -> None:
    raise error


@contextmanager
def uv_cache_lock(home: Path, scope: str) -> Iterator[bool]:
    """Keep an existing UV lock inode intact, including during a purge."""
    lock = home / ".cache/uv/.lock"
    if scope != "uv" or not lock.exists():
        yield True
        return
    if lock.is_symlink() or lock.stat().st_uid != os.getuid():
        raise RuntimeError(f"refusing unsafe UV cache lock: {lock}")
    with lock.open("r+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def clean(home: Path, projects: Path, tmp: Path, apply: bool) -> dict:
    validate_root(projects, home)
    cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
    report = {
        "status": "success", "dry_run": not apply, "user": os.getuid(),
        "started_at": datetime.now(timezone.utc).isoformat(), "actions": [],
        "cache_roots": [str(home / suffix) for suffix in (
            ".cache/uv", ".cache/git", ".cache/git-clones",
        )],
    }
    snapshot = process_snapshot(projects)
    for scope, path in candidates(home, projects, tmp):
        action = {"scope": scope, "path": str(path)}
        report["actions"].append(action)
        if path.is_symlink():
            action["status"] = "symlink"
            continue
        if busy(path, scope, snapshot):
            action["status"] = "active"
            continue
        age_cutoff = cutoff if scope in {"tmp", "node_modules"} else None
        status, size = inspect_tree(path, age_cutoff, scope)
        action["status"] = status
        if status != "eligible":
            continue
        action["estimated_bytes"] = size
        if not apply:
            continue
        with uv_cache_lock(home, scope) as acquired:
            if not acquired:
                action["status"] = "locked"
                continue
            snapshot = process_snapshot(projects)
            if busy(path, scope, snapshot):
                action["status"] = "active"
                continue
            # Recheck the entire tree and root type immediately before deletion.
            status, size = inspect_tree(path, age_cutoff, scope)
            if status != "eligible" or path.is_symlink():
                action["status"] = status if status != "eligible" else "symlink"
                continue
            if path.is_dir():
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise RuntimeError("this Python lacks symlink-safe directory deletion")
                shutil.rmtree(path)
            else:
                path.unlink()
            action.update(status="deleted", estimated_bytes=size)
            print(json.dumps(action), flush=True)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def write_report(directory: Path, report: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(directory / "status.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="authorize unattended deletion")
    mode.add_argument("--dry-run", action="store_true", help="inspect only (the default)")
    parser.add_argument(
        "--state-dir", type=Path,
        default=Path.home() / ".local/state/linux-daily-cleanup",
    )
    args = parser.parse_args()
    if sys.platform != "linux" or os.getuid() == 0:
        parser.error("run on Linux as the workspace owner, never root")
    home = Path.home()
    projects = home / "projects"
    try:
        if not args.apply:
            report = clean(home, projects, Path("/tmp"), False)
        else:
            if args.state_dir.is_symlink():
                raise RuntimeError(f"refusing symlink state directory: {args.state_dir}")
            args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if args.state_dir.stat().st_uid != os.getuid():
                raise RuntimeError(f"state directory must belong to uid {os.getuid()}: {args.state_dir}")
            lock_path = args.state_dir / "cleanup.lock"
            if lock_path.is_symlink():
                raise RuntimeError(f"refusing symlink lock: {lock_path}")
            with lock_path.open("a+") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    print("Daily cleanup is already running; skipped.")
                    return 0
                write_report(args.state_dir, {
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                })
                try:
                    report = clean(home, projects, Path("/tmp"), True)
                except (OSError, RuntimeError) as error:
                    write_report(args.state_dir, {
                        "status": "failed", "error": str(error),
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    })
                    raise
                write_report(args.state_dir, report)
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, RuntimeError) as error:
        print(f"Daily cleanup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
