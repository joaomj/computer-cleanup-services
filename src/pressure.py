"""Detect when the machine needs space before offering high-risk cleanup.

Deleting large, expensive-to-restore data is worth asking about only when the
machine is short of resources. Low-risk cleanup is not gated.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from config import Config, format_bytes

# Swap use above this share counts as memory pressure on any platform.
SWAP_PRESSURE_PERCENT = 90.0
_SWAP_VALUE = re.compile(r"([0-9.]+)\s*([KMG]?)", re.IGNORECASE)
_SWAP_FACTORS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}


def disk_state(path: Path) -> tuple[float, int]:
    """Return used percent and free bytes of the filesystem holding a path."""
    usage = shutil.disk_usage(path)
    if usage.total <= 0:
        return 0.0, usage.free
    return (usage.total - usage.free) / usage.total * 100, usage.free


def _swap_bytes(value: str) -> int:
    match = _SWAP_VALUE.match(value.strip())
    if match is None:
        raise RuntimeError(f"unexpected swap value: {value}")
    return int(float(match.group(1)) * _SWAP_FACTORS[match.group(2).upper()])


def _linux_memory() -> tuple[float | None, float | None]:
    """Return available memory percent and swap used percent from /proc/meminfo."""
    fields: dict[str, int] = {}
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None
    for line in lines:
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            fields[name] = int(parts[0]) * 1024
    total = fields.get("MemTotal")
    available = fields.get("MemAvailable")
    swap_total = fields.get("SwapTotal") or 0
    swap_free = fields.get("SwapFree") or 0
    available_percent = (
        available / total * 100 if total and available is not None else None
    )
    swap_percent = (
        (swap_total - swap_free) / swap_total * 100 if swap_total > 0 else None
    )
    return available_percent, swap_percent


def _macos_memory() -> tuple[float | None, float | None]:
    """Return available memory percent and swap used percent on macOS."""
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        available = os.sysconf("SC_AVPHYS_PAGES") * page
        total = os.sysconf("SC_PHYS_PAGES") * page
    except (OSError, ValueError):
        available_percent = None
    else:
        available_percent = available / total * 100 if total > 0 else None
    try:
        result = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return available_percent, None
    if result.returncode != 0:
        return available_percent, None
    values = dict(re.findall(r"(\w+)\s*=\s*([0-9.]+\s*[KMG]?)", result.stdout))
    try:
        total = _swap_bytes(values["total"])
        used = _swap_bytes(values["used"])
    except (KeyError, RuntimeError):
        return available_percent, None
    return available_percent, (used / total * 100 if total > 0 else None)


def memory_state() -> tuple[float | None, float | None]:
    """Return available memory percent and swap used percent, if known."""
    if sys.platform == "darwin":
        return _macos_memory()
    return _linux_memory()


def near_full(config: Config) -> tuple[bool, list[str]]:
    """Return whether high-risk cleanup should be offered, with the reasons."""
    reasons: list[str] = []
    used_percent, free = disk_state(config.home)
    if used_percent >= config.high_risk_review_disk_percent:
        reasons.append(
            f"disk {used_percent:.0f}% used ({format_bytes(free)} free)"
        )
    available_percent, swap_percent = memory_state()
    if (
        available_percent is not None
        and config.high_risk_review_memory_percent > 0
        and available_percent < config.high_risk_review_memory_percent
    ):
        reasons.append(f"memory {available_percent:.0f}% available")
    if swap_percent is not None and swap_percent >= SWAP_PRESSURE_PERCENT:
        reasons.append(f"swap {swap_percent:.0f}% used")
    return bool(reasons), reasons
