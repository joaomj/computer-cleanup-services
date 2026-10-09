"""Shared process, filesystem, and command primitives for cleanup modules."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from config import Config

PERMISSION_MARKERS = (
    "permission denied",
    "insufficient permissions",
    "operation not permitted",
    "not authorized",
    "access denied",
    "a password is required",
    "unable to open database file",
    # sudo denial phrasings (user phases must never attempt root-only cmds).
    "is not allowed to execute",
    "is not in the sudoers",
    "i'm afraid i can't do that",
    "no tty present",
    "user may not run sudo",
    # Docker daemon unreachable for this user (rootless socket missing,
    # not in docker group). Treat as skip, root run handles host prune.
    "cannot connect to the docker daemon",
    "failed to connect to the docker api",
    "is the docker daemon running",
    "dial unix",
)


def permission_limited(message: str) -> bool:
    """Return whether an operation was unavailable to this account."""
    normalized = message.lower()
    return any(marker in normalized for marker in PERMISSION_MARKERS)


def mark_skipped(skipped: list[str], task: str) -> None:
    """Record a task that the account could not inspect or execute."""
    if task not in skipped:
        skipped.append(task)


def run_command(
    command: list[str],
    config: Config,
    cwd: Path | None = None,
    *,
    environment: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run an external command with a bounded timeout."""
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.command_timeout_seconds,
            cwd=cwd,
            env={**os.environ, **environment} if environment is not None else None,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {config.command_timeout_seconds}s"
    except OSError as error:
        return 127, "", str(error)


def measure_path(path: Path) -> int:
    """Measure a file or directory in bytes."""
    if not path.exists():
        return 0
    du = shutil.which("du")
    if du:
        # GNU du (Linux) reports exact bytes; BSD du (macOS) has no -b,
        # so use POSIX kilobytes there. Without this, macOS always fell
        # back to the pure-Python walk below, which takes minutes on
        # hundred-thousand-file cache trees.
        for args, scale in ((["-sb"], 1), (["-sk"], 1024)):
            result = subprocess.run(
                [du, *args, str(path)], check=False, capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.split():
                try:
                    return int(result.stdout.split()[0]) * scale
                except ValueError:
                    pass
    if path.is_file():
        return path.stat().st_size
    return sum(measure_path(child) for child in path.iterdir())


def _process_is_running_linux(names: set[str]) -> bool:
    """Check processes on Linux via /proc."""
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        status_path = entry / "status"
        cmdline_path = entry / "comm"
        try:
            status = status_path.read_text(encoding="utf-8")
            uid_line = next(
                line for line in status.splitlines() if line.startswith("Uid:")
            )
            owner_uid = int(uid_line.split()[1])
            if owner_uid != os.getuid():
                continue
            name = cmdline_path.read_text(encoding="utf-8").strip().lower()
        except (FileNotFoundError, PermissionError, StopIteration, ValueError):
            continue
        if name in names:
            return True
    return False


def _process_is_running_macos(names: set[str]) -> bool:
    """Check processes on macOS via pgrep."""
    import shutil as _shutil

    pgrep_bin = _shutil.which("pgrep")
    if pgrep_bin is None:
        return False
    for name in names:
        result = subprocess.run(
            [pgrep_bin, "-u", str(os.getuid()), "-x", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    return False


def process_is_running(names: set[str]) -> bool:
    """Check processes owned by this user."""
    if sys.platform == "darwin":
        return _process_is_running_macos(names)
    return _process_is_running_linux(names)


def process_uses_path(path: Path) -> bool:
    """Return whether a process owned by this user has a handle in a path."""
    root = path.resolve()
    if sys.platform == "darwin":
        lsof = shutil.which("lsof")
        if lsof is None:
            return True
        try:
            result = subprocess.run(
                [lsof, "-a", "-u", str(os.getuid()), "+D", str(root), "-t"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True
        return result.returncode == 0 and bool(result.stdout.strip())
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8")
            uid_line = next(
                line for line in status.splitlines() if line.startswith("Uid:")
            )
            if int(uid_line.split()[1]) != os.getuid():
                continue
            links = [entry / "cwd"]
            links.extend((entry / "fd").iterdir())
            for link in links:
                candidate = Path(os.path.realpath(link))
                try:
                    candidate.relative_to(root)
                except ValueError:
                    continue
                return True
        except (FileNotFoundError, PermissionError, OSError, StopIteration, ValueError):
            continue
    return False
