#!/usr/bin/env python3
"""Render the last daily cleanup result for an interactive shell."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

from config import Config, format_bytes, load_config


SERVICE_LABEL = (
    "com.user.macos-cleanup-service"
    if sys.platform == "darwin"
    else "linux-cleanup.service"
)
SHELL_PREFIX = "[macos-cleanup]" if sys.platform == "darwin" else "[linux-cleanup]"

# Updater fields are platform-specific: Homebrew exists only on macOS, while
# APT, Snap, and the systemd journal exist only on Linux. The shell line
# shows only the current OS fields so a macOS shell never reports
# "APT unavailable" noise (and vice versa on Linux).
_MACOS_UPDATERS = (
    ("homebrew_updates", "Homebrew"),
    ("homebrew_cleanup", "Homebrew cleanup"),
)
_LINUX_UPDATERS = (
    ("apt_updates", "APT"),
    ("snap_updates", "Snap"),
)
_LINUX_ONLY_STATUS_KEYS = frozenset(
    {
        "apt_updates",
        "snap_updates",
        "apt_cache",
        "system_journal_bytes",
        "user_journal_bytes",
    }
)
_MACOS_ONLY_STATUS_KEYS = frozenset(
    {
        "homebrew_updates",
        "homebrew_cleanup",
    }
)
_LINUX_ONLY_SKIPPED_MARKERS = ("apt", "snap", "journal")
_MACOS_ONLY_SKIPPED_MARKERS = ("homebrew", "brew")


def read_status(config: Config) -> dict[str, Any] | None:
    """Read the atomically written status record."""
    try:
        return json.loads(config.status_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return {"status": "failed", "errors": ["status file is invalid"]}


def service_is_active() -> bool:
    """Return whether the service unit is active."""
    if sys.platform == "darwin":
        return _service_is_active_macos()
    return _service_is_active_linux()


def _service_is_active_linux() -> bool:
    """Check the systemd user service on Linux."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_LABEL],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() in {"active", "activating"}


def _service_is_active_macos() -> bool:
    """Check launchd job on macOS."""
    try:
        result = subprocess.run(
            ["launchctl", "list", SERVICE_LABEL],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and SERVICE_LABEL in result.stdout


def format_value(status: dict[str, Any], key: str) -> str:
    """Format a byte field from a status record."""
    value = status.get(key)
    return format_bytes(int(value)) if isinstance(value, int) else "?"


def platform_skipped(status: dict[str, Any]) -> list[str]:
    """Return skipped entries relevant to the current OS."""
    skipped = status.get("skipped", [])
    if not isinstance(skipped, list):
        return []
    if sys.platform == "darwin":
        markers = _LINUX_ONLY_SKIPPED_MARKERS
    else:
        markers = _MACOS_ONLY_SKIPPED_MARKERS
    relevant: list[str] = []
    for item in skipped:
        text = str(item).lower()
        if any(marker in text for marker in markers):
            continue
        relevant.append(str(item))
    return relevant


def security_line(status: dict[str, Any]) -> str | None:
    """Render the actionable part of the last security scan."""
    security = status.get("security")
    if not isinstance(security, dict):
        return None
    state = str(security.get("status", "unknown"))
    if state in {"disabled", "dry-run", "unsupported", "unavailable"}:
        return None
    critical = int(security.get("critical") or 0)
    high = int(security.get("high") or 0)
    reboot = bool(security.get("reboot_required"))
    if state == "failed":
        reason = str(security.get("reason") or "security check failed")
        return f"security check failed: {reason[:120]}"
    if not critical and not high and not reboot:
        return None
    parts: list[str] = []
    if critical:
        parts.append(f"{critical} critical")
    if high:
        parts.append(f"{high} high")
    text = "security: " + ", ".join(parts) if parts else "security: reboot required"
    first = security.get("first")
    if isinstance(first, dict) and first.get("id"):
        text += f" ({first['id']}"
        if first.get("package"):
            text += f" {first['package']}"
        text += ")"
    if reboot and parts:
        text += "; reboot required"
    if state == "partial":
        text += " (partial)"
    return text


def render_shell(config: Config) -> str | None:
    """Render one concise shell status line, or None when quiet.

    The shell init script runs on every new shell, so it stays silent
    unless the last pass failed or the security scan has something
    actionable. Success, warning, and in-progress cleanup details remain
    available via ``status.py --json``.
    """
    status = read_status(config)
    if status is None:
        return None
    state = str(status.get("status", "unknown"))
    if state == "running":
        if service_is_active():
            return None
        for _ in range(10):
            time.sleep(0.2)
            if service_is_active():
                return None
        state = "failed"
        status.setdefault("errors", []).append(
            "cleanup stopped before it wrote a final result"
        )
    security = security_line(status)
    if state != "failed":
        return f"{SHELL_PREFIX} {security}" if security else None
    if sys.platform == "darwin":
        hidden_keys = _LINUX_ONLY_STATUS_KEYS
        updater_fields = _MACOS_UPDATERS
    else:
        hidden_keys = _MACOS_ONLY_STATUS_KEYS
        updater_fields = _LINUX_UPDATERS
    parts = [f"{SHELL_PREFIX} {state}"]
    if status.get("finished_at"):
        parts.append(str(status["finished_at"]))
    for key, label in updater_fields:
        if key not in hidden_keys and isinstance(status.get(key), str):
            parts.append(f"{label} {status[key]}")
    for key, label in (
        ("brave_cache_bytes", "Brave"),
        ("uv_cache_bytes", "UV"),
        ("npm_cache_bytes", "npm"),
        ("system_journal_bytes", "system journal"),
        ("user_journal_bytes", "user journal"),
    ):
        if key not in hidden_keys and key in status:
            parts.append(f"{label} {format_value(status, key)}")
    # Report storage reclaimed by automatic cleanup.
    # Compact single part so the one-line shell message stays readable.
    reclaimed: list[str] = []
    tmp_bytes = 0
    for key in ("tmp_global",):
        entry = status.get(key)
        if isinstance(entry, dict) and isinstance(entry.get("deleted_bytes"), int):
            tmp_bytes += entry["deleted_bytes"]
    if tmp_bytes > 0:
        reclaimed.append(f"tmp {format_bytes(tmp_bytes)}")
    for key, label in (
        ("yarn_cache", "yarn"),
        ("zed_cache", "zed"),
        ("zed_server", "zed-server"),
    ):
        entry = status.get(key)
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("deleted_bytes"), int)
            and entry["deleted_bytes"] > 0
        ):
            reclaimed.append(f"{label} {format_bytes(entry['deleted_bytes'])}")
    docker = status.get("docker_prune")
    if isinstance(docker, dict) and docker.get("status") == "pruned":
        reclaimed.append("docker pruned")
    if reclaimed:
        parts.append("reclaimed " + " + ".join(reclaimed))
    warnings = status.get("warnings", [])
    errors = status.get("errors", [])
    skipped = platform_skipped(status)
    if skipped:
        parts.append("skipped: " + ", ".join(str(item) for item in skipped[:2]))
    if warnings:
        parts.append("warning: " + "; ".join(str(item) for item in warnings[:2]))
    if errors:
        parts.append("error: " + "; ".join(str(item) for item in errors[:2]))
    if security:
        parts.append(security)
    return " | ".join(parts)


def main() -> int:
    """Print shell status or JSON status."""
    config = load_config()
    if len(sys.argv) > 1 and sys.argv[1] == "--review-json":
        path = config.state_dir / "review-status.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            record = {}
        print(json.dumps(record, indent=2, sort_keys=True))
    elif len(sys.argv) > 1 and sys.argv[1] == "--json":
        status = read_status(config)
        print(json.dumps(status or {}, indent=2, sort_keys=True))
    elif len(sys.argv) > 1 and sys.argv[1] == "--security-json":
        path = config.state_dir / "security.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            record = {}
        except OSError as error:
            record = {"status": "failed", "reason": f"security.json is unreadable: {error}"}
        except json.JSONDecodeError:
            record = {"status": "failed", "reason": "security.json is invalid"}
        print(json.dumps(record, indent=2, sort_keys=True))
    else:
        line = render_shell(config)
        if line:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
