"""Shared process, filesystem, and command primitives for cleanup modules."""

from __future__ import annotations

import os
import shutil
import selectors
import signal
import shlex
import stat
import time
import subprocess
import sys
from pathlib import Path
from typing import Callable

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
    """Stream progress to stderr and bound both command lifetime and captured output."""
    print(f"[maintenance] Starting: {shlex.join(command)}", file=sys.stderr, flush=True)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            start_new_session=True,
            env={**os.environ, **environment} if environment is not None else None,
        )
    except OSError as error:
        return 127, "", str(error)
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + config.command_timeout_seconds
    next_notice = time.monotonic() + 15
    timed_out = False
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while selector.get_map():
                now = time.monotonic()
                if now >= deadline:
                    timed_out = True
                    break
                if now >= next_notice:
                    print(
                        f"[maintenance] Still running: {shlex.join(command)}",
                        file=sys.stderr,
                        flush=True,
                    )
                    next_notice = now + 15
                for key, _ in selector.select(min(1, deadline - now)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    captured[key.data].extend(chunk)
                    # Keep a bounded tail for status/error reporting.
                    del captured[key.data][: -2 * 1024 * 1024]
                    print(
                        chunk.decode("utf-8", errors="replace"),
                        end="",
                        file=sys.stderr,
                        flush=True,
                    )
            if not timed_out:
                try:
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
        finally:
            if timed_out or process.poll() is None:
                # A separate session lets us terminate descendants, not only the wrapper.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            process.stdout.close()
            process.stderr.close()
    stdout = captured["stdout"].decode("utf-8", errors="replace").strip()
    stderr = captured["stderr"].decode("utf-8", errors="replace").strip()
    if timed_out:
        message = f"command timed out after {config.command_timeout_seconds}s; process group terminated"
        print(f"[maintenance] {message}", file=sys.stderr, flush=True)
        return 124, stdout, f"{stderr}\n{message}".strip()
    print(
        f"[maintenance] Finished (exit {process.returncode}): {shlex.join(command)}",
        file=sys.stderr,
        flush=True,
    )
    return process.returncode, stdout, stderr


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


SESSION_SERVICES = frozenset({"systemd", "(sd-pam)", "ssh-agent", "gpg-agent"})


def tool_path(name: str, *fallbacks: str) -> str | None:
    """Find a tool on PATH, then in known system locations.

    Service managers and non-login shells can run with a PATH that omits
    directories such as /usr/sbin, where macOS keeps lsof.
    """
    resolved = shutil.which(name)
    if resolved:
        return resolved
    for candidate in fallbacks:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def within(path: Path, root: Path) -> bool:
    """Return whether path is root or lies beneath it, lexically."""
    return path == root or root in path.parents


class ProcessPaths:
    """Absolute paths that this user's processes reference at one moment.

    On Linux, sandboxed processes such as browser renderers hide their open
    files but still expose their command line. A process that exposes nothing
    makes the snapshot uncertain, and an uncertain snapshot reports every path
    as in use so that nothing is deleted. macOS uses lsof.
    """

    def __init__(self, paths: set[Path], uncertain: bool) -> None:
        self.paths = paths
        self.uncertain = uncertain

    @classmethod
    def capture(cls) -> "ProcessPaths":
        if sys.platform == "darwin":
            return cls._capture_macos()
        return cls._capture_linux()

    @classmethod
    def _capture_linux(cls) -> "ProcessPaths":
        paths: set[Path] = set()
        proc = Path("/proc")
        if not proc.is_dir():
            return cls(paths, True)
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                if entry.stat().st_uid != os.getuid():
                    continue
                name = (entry / "comm").read_text(encoding="utf-8").strip()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            if name in SESSION_SERVICES:
                continue
            references, visible = _linux_process_references(entry)
            paths.update(references)
            if not visible:
                return cls(paths, True)
        return cls(paths, False)

    @classmethod
    def _capture_macos(cls) -> "ProcessPaths":
        """List open files and working directories through lsof."""
        lsof = tool_path("lsof", "/usr/sbin/lsof", "/usr/bin/lsof")
        if lsof is None:
            return cls(set(), True)
        try:
            result = subprocess.run(
                [lsof, "-n", "-P", "-w", "-u", str(os.getuid()), "-Fn"],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return cls(set(), True)
        if result.returncode != 0 and not result.stdout.strip():
            return cls(set(), True)
        paths = {
            Path(line[1:])
            for line in result.stdout.splitlines()
            if line.startswith("n/")
        }
        return cls(paths, False)

    def uses(self, path: Path) -> bool:
        if self.uncertain:
            return True
        root = path.resolve()
        return any(within(reference, root) for reference in self.paths)


def _linux_process_references(entry: Path) -> tuple[set[Path], bool]:
    """Collect cwd, executable, open and mapped files, and path arguments.

    Returns the references and whether any source was readable.
    """
    references: set[Path] = set()
    visible = False

    def add(target: str) -> None:
        if target.startswith("/"):
            references.add(Path(target.removesuffix(" (deleted)")))

    for link in ("cwd", "exe"):
        try:
            add(os.readlink(entry / link))
            visible = True
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            continue
    try:
        for descriptor in (entry / "fd").iterdir():
            try:
                add(os.readlink(descriptor))
            except FileNotFoundError:
                continue
        visible = True
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
    try:
        for line in (entry / "maps").read_text(encoding="utf-8").splitlines():
            fields = line.split(maxsplit=5)
            if len(fields) == 6:
                add(fields[5])
        visible = True
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
    try:
        # Interpreted scripts can close their file after loading.
        for argument in (entry / "cmdline").read_bytes().split(b"\0"):
            add(os.fsdecode(argument))
        visible = True
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
    return references, visible


def inspect_tree(
    path: Path,
    cutoff: float | None,
    protected: Callable[[str], bool] | None = None,
) -> tuple[str, int]:
    """Check a tree before deletion without following symlinks.

    The tree is eligible only when every entry belongs to this user, is a
    directory, regular file, or symlink, has no protected name, and, with a
    cutoff, was modified and changed before the cutoff. Returns the status and
    the apparent size in bytes.
    """
    total = 0
    uid = os.getuid()
    pending = [path]
    while pending:
        entry = pending.pop()
        try:
            info = entry.lstat()
        except FileNotFoundError:
            if entry == path:
                return "missing", 0
            continue
        if info.st_uid != uid:
            return "other-owner", 0
        if protected is not None and protected(entry.name):
            return "protected", 0
        if cutoff is not None and max(info.st_mtime, info.st_ctime) >= cutoff:
            return "recent", 0
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(entry) as children:
                pending.extend(Path(child.path) for child in children)
        elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            total += info.st_size
        else:
            return "special-file", 0
    return "eligible", total


def remove_path(path: Path) -> None:
    """Delete a file, symlink, or directory tree without following symlinks."""
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)
