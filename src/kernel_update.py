"""Find pending kernel updates and install one only after the user agrees.

The daily pass never installs a kernel. It records what is waiting; the next
interactive shell shows the essentials and asks.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from common import mark_skipped, run_command
from config import Config, format_bytes
from state import write_json

_INSTALL_LINE = re.compile(r"^Inst (\S+) (?:\[\S+\] )?\((\S+) ")
_DOWNLOAD_LINE = re.compile(r"^'[^']+' \S+ (\d+) ")
_KERNEL_IMAGE = re.compile(r"linux-image-(\d\S*)")
_C_LOCALE = {"LC_ALL": "C"}


def pending_path(config: Config) -> Path:
    return config.state_dir / "pending-kernel.json"


def read_pending(config: Config) -> dict[str, Any] | None:
    path = pending_path(config)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid kernel update record {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"kernel update record must be an object: {path}")
    return value


def find_kernel_update(config: Config) -> dict[str, Any] | None:
    """Simulate a full upgrade and describe the kernel it would install."""
    apt_get = shutil.which("apt-get")
    if apt_get is None:
        raise RuntimeError("apt-get was not found")
    code, stdout, stderr = run_command(
        [apt_get, "-s", "full-upgrade"], config, environment=_C_LOCALE, quiet=True
    )
    if code != 0:
        raise RuntimeError(f"apt-get simulation failed: {stderr or stdout}")
    installs = [m for line in stdout.splitlines() if (m := _INSTALL_LINE.match(line))]
    images = [m for m in installs if _KERNEL_IMAGE.fullmatch(m.group(1))]
    if not images:
        return None
    code, stdout, stderr = run_command(
        [apt_get, "--print-uris", "-y", "full-upgrade"],
        config,
        environment=_C_LOCALE,
        quiet=True,
    )
    if code != 0:
        raise RuntimeError(f"apt-get download listing failed: {stderr or stdout}")
    download = sum(
        int(m.group(1)) for line in stdout.splitlines() if (m := _DOWNLOAD_LINE.match(line))
    )
    return {
        "kernel": _KERNEL_IMAGE.fullmatch(images[0].group(1)).group(1),
        "package_version": images[0].group(2),
        "packages": len(installs),
        "download_bytes": download,
    }


def _record(config: Config, update: dict[str, Any] | None, declined_on: str | None) -> None:
    """Store the pending kernel update, or clear the record when none waits."""
    path = pending_path(config)
    if update is None:
        path.unlink(missing_ok=True)
        return
    write_json(
        path,
        {
            **update,
            "running": os.uname().release,
            "found_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "declined_on": declined_on,
        },
    )


def _refresh(config: Config, update: dict[str, Any] | None) -> None:
    """Record the current finding and keep a decline only for the same kernel."""
    previous = read_pending(config)
    declined_on = None
    if update and previous and previous.get("kernel") == update["kernel"]:
        declined_on = previous.get("declined_on")
    _record(config, update, declined_on)


def check_kernel_update(
    config: Config, warnings: list[str], skipped: list[str], dry_run: bool
) -> str:
    """Daily-pass step: record a waiting kernel update for the next shell to offer."""
    if sys.platform != "linux" or not config.apt_updates_enabled:
        return "disabled"
    if dry_run:
        return "dry-run"
    if shutil.which("apt-get") is None:
        mark_skipped(skipped, "kernel update check")
        return "unavailable"
    try:
        update = find_kernel_update(config)
        _refresh(config, update)
    except RuntimeError as error:
        warnings.append(f"kernel update check failed: {error}")
        return "failed"
    return "pending" if update else "none"


def _ask(question: str) -> str:
    """Return "yes", "no", or "quit"."""
    while True:
        try:
            answer = input(question).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"
        if answer in {"y", "yes"}:
            return "yes"
        if answer in {"", "n", "no"}:
            return "no"
        if answer in {"q", "quit"}:
            return "quit"
        print("Enter y to install, n to skip today, or q to stop.")


def _boot_free() -> str | None:
    try:
        return format_bytes(shutil.disk_usage("/boot").free)
    except OSError:
        return None


def offer_kernel_update(config: Config, today: str, force: bool) -> str:
    """Ask whether to install a waiting kernel; return what happened.

    Results: "none", "declined", "installed", "failed", or "stopped" when the
    user wants no further questions.
    """
    pending = read_pending(config)
    if pending is None:
        return "none"
    if not force and pending.get("declined_on") == today:
        return "declined"
    # The record can be stale: the user may have installed the kernel already.
    try:
        update = find_kernel_update(config)
    except RuntimeError as error:
        print(f"Kernel update check failed: {error}", file=sys.stderr)
        return "failed"
    _refresh(config, update)
    if update is None:
        return "none"
    boot_free = _boot_free()
    print(
        f"\nKernel update available: {update['kernel']} (running {os.uname().release})"
        f"\n  Download: {format_bytes(update['download_bytes'])}, "
        f"{update['packages']} packages"
        + (f"; /boot has {boot_free} free" if boot_free else "")
        + "\n  Needs a reboot afterwards. Installing asks for your sudo password."
        "\n  Enter y to install now, n to skip today, q to stop.\n"
    )
    answer = _ask("Install the kernel update now? [y/N/q] ")
    if answer != "yes":
        _record(config, update, today)
        return "stopped" if answer == "quit" else "declined"
    sudo = shutil.which("sudo")
    apt_get = shutil.which("apt-get")
    if sudo is None or apt_get is None:
        print("Kernel update needs sudo and apt-get.", file=sys.stderr)
        return "failed"
    code = subprocess.run([sudo, apt_get, "full-upgrade", "-y"], check=False).returncode
    if code != 0:
        print(f"Kernel update failed (apt-get exit {code}).", file=sys.stderr)
        return "failed"
    _record(config, None, None)
    print("Kernel installed. Reboot to start using it.")
    return "installed"
