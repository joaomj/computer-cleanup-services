"""Storage cleanup and system update operations."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from artifacts import clean_project_caches
from cache_cleanup import (
    clean_npm_cache,
    clean_yarn_cache,
    clean_zed_cache,
    clean_zed_server,
    manage_brave_cache,
    prune_uv_cache,
)
from common import (
    adopt_login_path,
    find_brew,
    ProcessPaths,
    inspect_tree,
    mark_skipped,
    permission_limited,
    remove_path,
    run_command,
    tool_path,
)
from config import Config, format_bytes
from high_risk import discover
from kernel_update import check_kernel_update
from runtime_update import update_runtimes
from security import scan_security
from state import write_json


def parse_journal_size(output: str) -> int | None:
    """Parse the byte value from journalctl's disk usage line."""
    units = {
        "B": 1,
        "K": 1_000,
        "M": 1_000_000,
        "G": 1_000_000_000,
        "T": 1_000_000_000_000,
    }
    matches = re.findall(r"([0-9]+(?:[.,][0-9]+)?)\s*([BKMGT])", output.upper())
    for number, unit in reversed(matches):
        normalized_number = number.replace(",", ".")
        if unit in units:
            return int(float(normalized_number) * units[unit])
    return None


def journal_size(
    user: bool, config: Config, skipped: list[str]
) -> tuple[int | None, str | None]:
    """Read system or user journal usage (Linux only)."""
    if sys.platform != "linux":
        return None, None
    scope = "user" if user else "system"
    command = ["journalctl"]
    if user:
        command.append("--user")
    command.append("--disk-usage")
    code, stdout, stderr = run_command(command, config)
    if code != 0:
        if permission_limited(stderr or stdout):
            mark_skipped(skipped, f"{scope} journal measurement")
            return None, None
        return None, stderr or "journalctl failed"
    size = parse_journal_size(stdout)
    if size is None and permission_limited(stderr):
        mark_skipped(skipped, f"{scope} journal measurement")
        return None, None
    return size, None if size is not None else "journalctl returned no size"


def vacuum_journal(
    user: bool,
    config: Config,
    current_size: int | None,
    warnings: list[str],
    skipped: list[str],
) -> int | None:
    """Vacuum an oversized journal without prompting for credentials (Linux only)."""
    if sys.platform != "linux":
        return current_size
    scope = "user" if user else "system"
    if current_size is None or current_size <= config.journal_warn_size:
        return current_size
    if not user and not config.sudo_tasks_enabled:
        return current_size
    command_prefix = ["journalctl", "--user"] if user else ["sudo", "-n", "journalctl"]
    for command in (
        command_prefix + ["--rotate"],
        command_prefix + [f"--vacuum-size={config.journal_system_max_use}"],
    ):
        code, _, stderr = run_command(command, config)
        if code != 0:
            if permission_limited(stderr):
                mark_skipped(skipped, f"{scope} journal vacuum")
                return current_size
            warnings.append(
                f"{scope} journal vacuum failed: {stderr or 'permission denied'}"
            )
            return current_size
    size, error = journal_size(user, config, skipped)
    if error:
        warnings.append(error)
    return size


def update_apt(
    config: Config, warnings: list[str], skipped: list[str], dry_run: bool
) -> str:
    """Refresh APT package lists and install available upgrades."""
    if not config.apt_updates_enabled or not config.sudo_tasks_enabled:
        return "disabled"
    if dry_run:
        return "dry-run"
    apt_get = shutil.which("apt-get")
    if apt_get is None:
        mark_skipped(skipped, "APT updates")
        return "unavailable"
    sudo = shutil.which("sudo")
    if sudo is None:
        mark_skipped(skipped, "APT updates")
        return "unavailable"
    code, stdout, stderr = run_command([sudo, "-n", apt_get, "update"], config)
    if code != 0:
        if permission_limited(stderr or stdout):
            mark_skipped(skipped, "APT updates")
            return "skipped"
        warnings.append(
            f"APT package list update failed: {stderr or stdout or 'unknown error'}"
        )
        return "failed"
    code, stdout, stderr = run_command([sudo, "-n", apt_get, "upgrade", "-y"], config)
    if code != 0:
        if permission_limited(stderr or stdout):
            mark_skipped(skipped, "APT updates")
            return "skipped"
        warnings.append(
            f"APT package upgrade failed: {stderr or stdout or 'unknown error'}"
        )
        return "failed"
    return "updated"


def update_snap(
    config: Config, warnings: list[str], skipped: list[str], dry_run: bool
) -> str:
    """Refresh installed Snap packages."""
    if not config.snap_updates_enabled or not config.sudo_tasks_enabled:
        return "disabled"
    if dry_run:
        return "dry-run"
    snap = shutil.which("snap")
    if snap is None:
        mark_skipped(skipped, "Snap updates")
        return "unavailable"
    sudo = shutil.which("sudo")
    if sudo is None:
        mark_skipped(skipped, "Snap updates")
        return "unavailable"
    code, stdout, stderr = run_command([sudo, "-n", snap, "refresh"], config)
    if code != 0:
        if permission_limited(stderr or stdout):
            mark_skipped(skipped, "Snap updates")
            return "skipped"
        warnings.append(f"Snap refresh failed: {stderr or stdout or 'unknown error'}")
        return "failed"
    return "updated"


def read_brew_upgrade_state(config: Config) -> datetime | None:
    """Return the last successful `brew upgrade` date, if recorded."""
    path = config.state_dir / "brew_upgrade.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw)
        return datetime.fromisoformat(value["last_success"]).astimezone()
    except (ValueError, TypeError, KeyError) as error:
        raise RuntimeError(
            f"invalid Homebrew upgrade state in {path}: {error}"
        ) from error


def write_brew_upgrade_state(config: Config, now: datetime) -> None:
    """Record a successful `brew upgrade` run."""
    write_json(
        config.state_dir / "brew_upgrade.json",
        {"last_success": now.isoformat(timespec="seconds")},
    )


def update_homebrew(
    config: Config,
    now: datetime,
    warnings: list[str],
    skipped: list[str],
    dry_run: bool,
) -> str:
    """Refresh Homebrew metadata daily, upgrade packages weekly (macOS).

    Like the Linux APT flow: `brew update` runs every pass, `brew upgrade`
    runs only when the last successful upgrade is at least
    ``BREW_UPGRADE_MIN_AGE_DAYS`` old. The interval does not guarantee
    a minimum age for individual package releases.
    """
    if sys.platform != "darwin":
        return "unsupported"
    if not config.brew_updates_enabled:
        return "disabled"
    if dry_run:
        return "dry-run"
    brew = find_brew()
    if brew is None:
        mark_skipped(skipped, "Homebrew updates")
        return "unavailable"
    code, stdout, stderr = run_command(
        [brew, "update"], config, environment={"HOMEBREW_NO_INSTALL_CLEANUP": "1"}
    )
    if code != 0:
        if permission_limited(stderr or stdout):
            mark_skipped(skipped, "Homebrew updates")
            return "skipped"
        warnings.append(
            f"Homebrew update failed: {stderr or stdout or 'unknown error'}"
        )
        return "failed"
    last_success = read_brew_upgrade_state(config)
    if last_success is not None:
        age_days = (now - last_success).days
        if age_days < config.brew_upgrade_min_age_days:
            return "deferred"
    # Ask Homebrew to resolve upgrades first. Never deliberately start a known source build.
    code, preview, preview_errors = run_command(
        [brew, "upgrade", "--dry-run"],
        config,
        environment={
            "HOMEBREW_NO_INSTALL_CLEANUP": "1",
            "HOMEBREW_NO_AUTO_UPDATE": "1",
            "HOMEBREW_NO_COLOR": "1",
        },
    )
    if code != 0:
        warnings.append(
            f"Homebrew upgrade planning failed: {preview_errors or preview or 'unknown error'}"
        )
        return "failed"
    plan = preview + "\n" + preview_errors
    source_builds = re.search(
        r"\b[1-9][0-9]*\s+(?:homebrew/core\s+)?formulae?\s+that would build from source",
        plan,
        re.IGNORECASE,
    )
    if source_builds or re.search(
        r"(?:would|will|must|requires?)\s+(?:be\s+)?(?:built|build|building|compil\w*)\s+from source",
        plan,
        re.IGNORECASE,
    ):
        reason = "Homebrew upgrade deferred: plan includes source builds; install compatible bottles or upgrade those packages manually"
        warnings.append(reason)
        mark_skipped(skipped, reason)
        print(f"[maintenance] {reason}", file=sys.stderr, flush=True)
        return "source-build-skipped"
    code, stdout, stderr = run_command(
        [brew, "upgrade", "--force-bottle"],
        config,
        environment={"HOMEBREW_NO_INSTALL_CLEANUP": "1"},
    )
    if code != 0:
        if permission_limited(stderr or stdout):
            mark_skipped(skipped, "Homebrew updates")
            return "skipped"
        warnings.append(
            f"Homebrew upgrade failed: {stderr or stdout or 'unknown error'}"
        )
        return "failed"
    write_brew_upgrade_state(config, now)
    return "updated"


def cleanup_homebrew(
    config: Config, warnings: list[str], skipped: list[str], dry_run: bool
) -> str:
    """Remove stale Homebrew downloads and old package versions on macOS."""
    if sys.platform != "darwin":
        return "unsupported"
    if dry_run:
        return "dry-run"
    brew = find_brew()
    if brew is None:
        mark_skipped(skipped, "Homebrew cleanup")
        return "unavailable"
    code, stdout, stderr = run_command([brew, "cleanup", "-s"], config)
    if code != 0:
        if permission_limited(stderr or stdout):
            mark_skipped(skipped, "Homebrew cleanup")
            return "skipped"
        warnings.append(
            f"Homebrew cleanup failed: {stderr or stdout or 'unknown error'}"
        )
        return "failed"
    return "updated"


def clean_apt_cache(
    config: Config, dry_run: bool, warnings: list[str], skipped: list[str]
) -> str:
    """Clean downloaded APT packages."""
    if not config.apt_cache_cleanup_enabled or not config.sudo_tasks_enabled:
        return "disabled"
    if dry_run:
        return "dry-run"
    if sys.platform != "linux":
        return "unsupported"
    apt_get = shutil.which("apt-get")
    sudo = shutil.which("sudo")
    if apt_get is None or sudo is None:
        mark_skipped(skipped, "APT cache cleanup")
        return "unavailable"
    for args in (["clean"], ["autoclean"]):
        code, stdout, stderr = run_command([sudo, "-n", apt_get, *args], config)
        if code != 0:
            if permission_limited(stderr or stdout):
                mark_skipped(skipped, "APT cache cleanup")
                return "skipped"
            warnings.append(
                f"APT {args[0]} failed: {stderr or stdout or 'unknown error'}"
            )
            return "failed"
    return "cleaned"


def prune_docker(
    config: Config, dry_run: bool, warnings: list[str], skipped: list[str]
) -> dict[str, Any]:
    """Prune stopped containers and build cache in the selected context."""
    result: dict[str, Any] = {
        "enabled": bool(getattr(config, "docker_prune_enabled", True))
    }
    if not result["enabled"]:
        result["status"] = "disabled"
        return result
    docker = shutil.which("docker")
    if docker is None:
        mark_skipped(skipped, "Docker prune")
        result["status"] = "unavailable"
        return result
    max_age = int(getattr(config, "docker_prune_max_age_hours", 24))
    until = f"{max_age}h"
    if dry_run:
        code, stdout, stderr = run_command([docker, "system", "df"], config)
        if code != 0 and permission_limited((stderr or "") + (stdout or "")):
            mark_skipped(skipped, "Docker prune")
        result["status"] = "dry-run"
        result["system_df"] = (
            stdout[:2000] if code == 0 else (stderr or stdout or "")[:500]
        )
        return result
    # Unused images and networks are high-risk and need a recorded decision.
    steps: list[tuple[str, list[str]]] = [
        (
            "containers",
            [docker, "container", "prune", "-f", "--filter", f"until={until}"],
        ),
        ("builder", [docker, "builder", "prune", "-f", "--filter", f"until={until}"]),
    ]
    for name, cmd in steps:
        code, stdout, stderr = run_command(cmd, config)
        if code != 0:
            if permission_limited(stderr or stdout):
                mark_skipped(skipped, f"Docker {name} prune")
                result[name] = "skipped"
                continue
            warnings.append(
                f"Docker {name} prune failed: {stderr or stdout or 'unknown error'}"
            )
            result[name] = "failed"
        else:
            result[name] = (stdout or "pruned")[:1000]
    code, stdout, _ = run_command([docker, "system", "df"], config)
    if code == 0:
        result["system_df_after"] = stdout[:2000]
    result["status"] = (
        "failed" if any(result.get(name) == "failed" for name, _ in steps) else "pruned"
    )
    return result


# Session, agent, and tool runtime entries in /tmp are never cleanup targets.
TMP_PROTECTED_PREFIXES = (
    ".X",
    ".ICE",
    ".font",
    "systemd-",
    "snap-",
    "ssh-",
    "tmux-",
    "gpg-",
    "keyring-",
    "pi-",
    "claude-",
)
SECRET_MARKERS = (
    "secret",
    "credential",
    "token",
    "passwd",
    "private-key",
    "keyring",
    "proton",
    "auth",
)


def tmp_name_protected(name: str) -> bool:
    """Return whether a /tmp entry name marks credentials or session state."""
    lowered = name.lower()
    return (
        name in {".git", ".pi", ".ssh", ".gnupg", ".env"}
        or lowered.startswith(".env.")
        or lowered.endswith((".pem", ".key", ".p8", ".sock", ".lock"))
        or any(marker in lowered for marker in SECRET_MARKERS)
    )


def clean_tmp_global(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Delete this user's old, unused /tmp entries.

    An entry is deleted only when every file in it belongs to this user, is
    older than TMP_MAX_AGE_HOURS, has no credential-like name, and no running
    program references it. The listing uses `find` because some hardened
    hosts deny Python a direct listing of /tmp.
    """
    result = {
        "deleted": 0,
        "deleted_bytes": 0,
        "kept": 0,
        "skipped_active": 0,
        "skipped_system": 0,
    }
    find = tool_path("find", "/usr/bin/find", "/bin/find")
    if find is None:
        mark_skipped(skipped, "tmp inspection (find is not installed)")
        return result
    try:
        proc = subprocess.run(
            [find, str(config.tmp_dir), "-mindepth", "1", "-maxdepth", "1", "-print"],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            cwd="/",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        warnings.append(f"tmp inspection failed: {error}")
        return result
    entries = [Path(line) for line in proc.stdout.splitlines() if line.strip()]
    if not entries and proc.returncode != 0:
        if permission_limited(proc.stderr or ""):
            mark_skipped(skipped, "tmp inspection")
        else:
            warnings.append(f"tmp inspection failed: {(proc.stderr or '').strip()[:200]}")
        return result
    cutoff = now.timestamp() - config.tmp_max_age_hours * 3600
    snapshot = ProcessPaths.capture()
    if snapshot.uncertain:
        mark_skipped(skipped, "tmp cleanup (running programs could not be inspected)")
        return result
    for entry in sorted(entries):
        name = entry.name
        if name.startswith(TMP_PROTECTED_PREFIXES) or tmp_name_protected(name):
            result["skipped_system"] += 1
            continue
        try:
            if snapshot.uses(entry):
                result["skipped_active"] += 1
                continue
            status, size = inspect_tree(entry, cutoff, tmp_name_protected)
            if status != "eligible":
                result["kept"] += 1
                continue
            if not dry_run:
                # Check again immediately before deletion.
                if ProcessPaths.capture().uses(entry):
                    result["skipped_active"] += 1
                    continue
                status, size = inspect_tree(entry, cutoff, tmp_name_protected)
                if status != "eligible":
                    result["kept"] += 1
                    continue
                remove_path(entry)
            result["deleted"] += 1
            result["deleted_bytes"] += size
        except PermissionError:
            result["kept"] += 1
        except OSError as error:
            warnings.append(f"tmp cleanup failed for {name}: {error}")
    return result


def execute(config: Config, dry_run: bool) -> dict[str, Any]:
    """Run upgrades and low-risk cleanup, and list high-risk candidates.

    Low-risk cleanup deletes regenerable data without a prompt. High-risk
    candidates are only discovered here; deleting them needs a decision.
    """
    now = datetime.now().astimezone()
    warnings: list[str] = []
    skipped: list[str] = []
    result: dict[str, Any] = {
        "status": "success",
        "started_at": now.isoformat(timespec="seconds"),
        "attempt_date": now.date().isoformat(),
        "warnings": warnings,
        "errors": [],
        "skipped": skipped,
        "dry_run": dry_run,
    }
    path_warning = adopt_login_path()
    if path_warning:
        warnings.append(path_warning)
    runtime_result = update_runtimes(config, now, dry_run)
    result["runtime_updates"] = runtime_result
    warnings.extend(runtime_result["warnings"])
    if sys.platform == "darwin":
        result["homebrew_updates"] = update_homebrew(
            config, now, warnings, skipped, dry_run
        )
        if config.brew_cleanup_enabled:
            result["homebrew_cleanup"] = cleanup_homebrew(
                config, warnings, skipped, dry_run
            )
    else:
        result["apt_updates"] = update_apt(config, warnings, skipped, dry_run)
        result["kernel_update"] = check_kernel_update(config, warnings, skipped, dry_run)
        result["snap_updates"] = update_snap(config, warnings, skipped, dry_run)
        if config.apt_cache_cleanup_enabled:
            result["apt_cache"] = clean_apt_cache(config, dry_run, warnings, skipped)
    result["security"] = scan_security(config, now, dry_run)
    if config.uv_cleanup_enabled:
        result["uv_cache_bytes"] = prune_uv_cache(config, warnings, skipped, dry_run)
    if config.brave_cleanup_enabled:
        result["brave_cache_bytes"] = manage_brave_cache(
            config, warnings, skipped, dry_run
        )
    if config.npm_cleanup_enabled:
        result["npm_cache_bytes"] = clean_npm_cache(
            config, now, dry_run, warnings, skipped
        )
    for enabled, name, action in (
        (config.yarn_cleanup_enabled, "yarn_cache", clean_yarn_cache),
        (config.zed_cleanup_enabled, "zed_cache", clean_zed_cache),
        (config.zed_server_cleanup_enabled, "zed_server", clean_zed_server),
        (config.tmp_cleanup_enabled, "tmp_global", clean_tmp_global),
    ):
        if enabled:
            result[name] = action(config, now, dry_run, warnings, skipped)
    if config.project_artifacts_cleanup_enabled:
        result["project_caches"] = clean_project_caches(
            config, now, dry_run, warnings, skipped
        )
    if config.docker_prune_enabled:
        result["docker_prune"] = prune_docker(config, dry_run, warnings, skipped)
    if sys.platform == "linux":
        for user, key in (
            (False, "system_journal_bytes"),
            (True, "user_journal_bytes"),
        ):
            size, error = journal_size(user, config, skipped)
            if error:
                warnings.append(f"journal measurement failed: {error}")
            if config.journal_cleanup_enabled and not dry_run:
                size = vacuum_journal(user, config, size, warnings, skipped)
            if size is not None:
                result[key] = size
                if size > config.journal_warn_size:
                    warnings.append(
                        f"{key} exceeds {format_bytes(config.journal_warn_size)}"
                    )
    result["high_risk_candidates"] = [
        candidate.to_json() for candidate in discover(config, now, warnings, skipped)
    ]
    result["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if warnings:
        result["status"] = "warning"
    return result
