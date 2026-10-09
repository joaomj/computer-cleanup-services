"""Storage cleanup and system update operations."""

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

from cache_cleanup import (
    clean_huggingface_cache,
    clean_playwright_cache,
    clean_yarn_cache,
    clean_zed_caches,
    clean_zed_server,
)
from common import (
    mark_skipped,
    measure_path,
    permission_limited,
    process_uses_path,
    run_command,
)
from config import Config, format_bytes
from runtime_update import update_runtimes
from state import write_json

from cache_cleanup import (
    clean_npm_cache,
    manage_brave_cache,
    prune_uv_cache,
)


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
    if not config.apt_updates_enabled:
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
    if not config.snap_updates_enabled:
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


def find_brew() -> str | None:
    """Locate the Homebrew binary without trusting the launchd PATH.

    The launchd job runs with a minimal PATH that may not contain a
    user-local Homebrew installation (for example ``~/homebrew/bin``),
    so check the well-known prefixes as a fallback."""
    resolved = shutil.which("brew")
    if resolved:
        return resolved
    for candidate in (
        Path.home() / "homebrew" / "bin" / "brew",
        Path("/opt/homebrew/bin/brew"),
        Path("/usr/local/bin/brew"),
    ):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


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
    code, stdout, stderr = run_command(
        [brew, "upgrade"], config, environment={"HOMEBREW_NO_INSTALL_CLEANUP": "1"}
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
    """Clean downloaded APT packages after deletion approval."""
    if not bool(getattr(config, "apt_cache_cleanup_enabled", True)):
        return "disabled"
    # APT cache belongs to whoever manages APT. User phases have
    # APT_UPDATES_ENABLED=false and no sudo rights; attempting `sudo -n
    # apt-get clean` there only produces denial noise, so skip early.
    if not bool(getattr(config, "apt_updates_enabled", True)):
        mark_skipped(skipped, "APT cache cleanup (APT updates disabled for this user)")
        return "skipped"
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
    """Prune unused Docker resources in the selected context; preserve volumes."""
    result: dict[str, Any] = {
        "enabled": bool(getattr(config, "docker_prune_enabled", True))
    }
    if not result["enabled"]:
        result["status"] = "disabled"
        return result
    if sys.platform == "darwin":
        # Docker Desktop on macOS shares the same CLI filters.
        pass
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
    steps: list[tuple[str, list[str]]] = [
        (
            "containers",
            [docker, "container", "prune", "-f", "--filter", f"until={until}"],
        ),
        (
            "images",
            [docker, "image", "prune", "-a", "-f", "--filter", f"until={until}"],
        ),
        ("builder", [docker, "builder", "prune", "-f", "--filter", f"until={until}"]),
        ("networks", [docker, "network", "prune", "-f", "--filter", f"until={until}"]),
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


_TMP_SAFE_SKIP = frozenset(
    {
        ".X11-unix",
        ".ICE-unix",
        ".XIM-unix",
        ".font-unix",
        "systemd-private-",
        "snap-private-tmp",
        ".dotnet",
    }
)


def _tmp_entry_protected(name: str) -> bool:
    if name in _TMP_SAFE_SKIP:
        return True
    for prefix in (
        ".X11",
        ".ICE",
        ".XIM",
        ".font",
        "systemd-private-",
        "snap-private-tmp",
        "tmux-",
    ):
        if name.startswith(prefix):
            return True
    # Sockets/pipes/locks are never data to prune by age.
    if name.endswith((".sock", ".lock")):
        return True
    # Secrets/keys must never be auto-deleted, even if stale in /tmp.
    lowered = name.lower()
    for marker in ("private-key", "secret", "token", "credential", "passwd"):
        if marker in lowered:
            return True
    if lowered.endswith((".p8", ".pem", ".key")):
        return True
    return False


def clean_tmp_global(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Clean /tmp entries older than 24h owned by this user (or all if root).

    Never deletes system sockets, private tmp mounts, or in-use paths.
    User run: only own uid. Root run: all uids (daily host cleanup).

    Uses `find` binary for listing: Python cannot list /tmp on hardened
    hosts (os.listdir('/tmp') -> Permission denied while /bin/ls works).
    """
    result = {
        "deleted": 0,
        "deleted_bytes": 0,
        "kept": 0,
        "skipped_active": 0,
        "skipped_system": 0,
    }
    if not config.tmp_cleanup_enabled:
        return result
    find = shutil.which("find")
    if find is None:
        mark_skipped(skipped, "tmp global inspection")
        return result
    max_age = int(getattr(config, "tmp_max_age_hours", 24))
    mmin = max(60, max_age * 60)
    is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
    my_uid = os.getuid() if hasattr(os, "getuid") else None
    try:
        # -mindepth 1 -maxdepth 1: direct children only. Include hidden (-name .*).
        # NOTE: rc=1 with valid stdout is common (subdir perms); process stdout anyway.
        proc = subprocess.run(
            [
                find,
                str(config.tmp_dir),
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                "-mmin",
                f"+{mmin}",
                "-print",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            cwd="/",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        warnings.append(f"tmp global inspection failed: {error}")
        return result
    tmp_lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    if not tmp_lines:
        if proc.returncode != 0:
            err = proc.stderr or ""
            if "Permission denied" in err and "/tmp" in err:
                mark_skipped(
                    skipped, "tmp global (AppArmor Python block, use bash helper)"
                )
            elif permission_limited(err):
                mark_skipped(skipped, "tmp global inspection")
            else:
                warnings.append(
                    f"tmp global find found none (rc={proc.returncode}): {err.strip()[:200]}"
                )
        return result
    for line in tmp_lines:
        entry = Path(line)
        name = entry.name
        try:
            if _tmp_entry_protected(name):
                result["skipped_system"] += 1
                continue
            try:
                if entry.is_symlink():
                    result["kept"] += 1
                    continue
                st = entry.stat()
            except FileNotFoundError:
                continue
            if not is_root and my_uid is not None and st.st_uid != my_uid:
                result["kept"] += 1
                continue
            if process_uses_path(entry):
                result["skipped_active"] += 1
                continue
            size = measure_path(entry)
            if dry_run:
                result["deleted"] += 1
                result["deleted_bytes"] += size
                continue
            if process_uses_path(entry):
                result["skipped_active"] += 1
                continue
            if entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
            else:
                result["kept"] += 1
                continue
            result["deleted"] += 1
            result["deleted_bytes"] += size
        except PermissionError:
            mark_skipped(skipped, "tmp global cleanup")
        except OSError as error:
            warnings.append(f"tmp cleanup failed for {name}: {error}")
    return result


def clean_project_artifacts(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Delete regenerable project artifacts older than 24h and not in use.

    Targets (exact dir names only, under ~/projects):
      node_modules, .venv, dist, .next, .nx, coverage,
      .pytest_cache, .ruff_cache, .mypy_cache, __pycache__
    Never touches source, .git, or running processes.
    """
    result = {"deleted": 0, "deleted_bytes": 0, "kept": 0, "scanned": 0}
    if not bool(getattr(config, "project_artifacts_cleanup_enabled", True)):
        return result
    root = Path(getattr(config, "project_root", Path.home() / "projects"))
    max_age = int(getattr(config, "project_artifacts_max_age_hours", 24))
    cutoff = now.timestamp() - max_age * 3600
    targets = frozenset(
        {
            "node_modules",
            ".venv",
            "dist",
            ".next",
            ".nx",
            "coverage",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "__pycache__",
        }
    )
    try:
        if not root.is_dir() or root.is_symlink():
            return result
    except PermissionError:
        mark_skipped(skipped, "project artifacts inspection")
        return result
    # Limit discovery depth to avoid unbounded project scans.
    # NOTE: -mmin (not -mtime +1) for exact 24h: find -mtime +1 means >48h.
    # NOTE: find returns rc=1 when some subdirs deny access (common in
    # node_modules with root-owned entries). Process stdout anyway.
    find = shutil.which("find")
    candidates: list[Path] = []
    if find:
        mmin = max(60, max_age * 60)
        cmd = [find, str(root), "-maxdepth", "5", "-type", "d", "("]
        first = True
        for t in sorted(targets):
            if not first:
                cmd += ["-o"]
            cmd += ["-name", t]
            first = False
        cmd += [")", "-mmin", f"+{mmin}", "-not", "-path", "*/node_modules/*", "-print"]
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True, timeout=120, cwd="/"
            )
            out_lines = [line for line in proc.stdout.splitlines() if line.strip()]
            if out_lines:
                # Exclude nested hits inside node_modules (e.g. pkg/dist,
                # pkg/node_modules): only top-level artifacts are safe targets.
                # Deleting dist/ inside node_modules would break packages.
                filtered: list[Path] = []
                for line in out_lines:
                    try:
                        rel = Path(line).relative_to(root)
                        # If any parent dir is node_modules, this is nested -> skip.
                        if "node_modules" in rel.parent.parts:
                            continue
                    except (ValueError, OSError):
                        continue
                    filtered.append(Path(line))
                candidates = filtered
            elif proc.returncode != 0:
                warnings.append(
                    f"project artifacts find found none (rc={proc.returncode}): {(proc.stderr or '').strip()[:200]}"
                )
        except (OSError, subprocess.TimeoutExpired) as error:
            warnings.append(f"project artifacts find failed: {error}")
            return result
    else:
        # Fallback walk (slower).
        try:
            for dirpath, dirnames, _ in os.walk(root, followlinks=False):
                depth = len(Path(dirpath).relative_to(root).parts)
                if depth > 5:
                    dirnames[:] = []
                    continue
                for d in list(dirnames):
                    if d in targets:
                        candidates.append(Path(dirpath) / d)
        except OSError as error:
            warnings.append(f"project artifacts walk failed: {error}")
            return result
    for cand in candidates:
        result["scanned"] += 1
        try:
            if cand.is_symlink() or not cand.is_dir():
                result["kept"] += 1
                continue
            try:
                if cand.stat().st_mtime >= cutoff:
                    result["kept"] += 1
                    continue
            except FileNotFoundError:
                continue
            if process_uses_path(cand):
                result["kept"] += 1
                continue
            size = measure_path(cand)
            if dry_run:
                result["deleted"] += 1
                result["deleted_bytes"] += size
                continue
            if process_uses_path(cand):
                result["kept"] += 1
                continue
            shutil.rmtree(cand)
            result["deleted"] += 1
            result["deleted_bytes"] += size
        except PermissionError:
            mark_skipped(skipped, "project artifacts cleanup")
        except OSError as error:
            warnings.append(f"project artifact cleanup failed for {cand}: {error}")
    return result


def execute(
    config: Config,
    dry_run: bool,
    *,
    updates_only: bool = False,
    cleanup_only: bool = False,
    deletions_approved: bool = False,
) -> dict[str, Any]:
    """Run selected actions; cleanup mutations require explicit approval."""
    if updates_only and cleanup_only:
        raise ValueError("updates_only and cleanup_only cannot both be true")
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
        "deletions_approved": deletions_approved and not dry_run,
    }
    if not cleanup_only:
        runtime_result = update_runtimes(config, now, dry_run)
        result["runtime_updates"] = runtime_result
        warnings.extend(runtime_result["warnings"])
        if sys.platform == "darwin":
            result["homebrew_updates"] = update_homebrew(
                config, now, warnings, skipped, dry_run
            )
        else:
            result["apt_updates"] = update_apt(config, warnings, skipped, dry_run)
            result["snap_updates"] = update_snap(config, warnings, skipped, dry_run)
    if not updates_only:
        inspect_only = dry_run or not deletions_approved
        if not dry_run and not deletions_approved:
            mark_skipped(skipped, "cleanup deletions (confirmation required)")
        if sys.platform == "darwin" and config.brew_cleanup_enabled:
            result["homebrew_cleanup"] = cleanup_homebrew(
                config, warnings, skipped, inspect_only
            )
        if sys.platform == "linux" and config.apt_cache_cleanup_enabled:
            result["apt_cache"] = clean_apt_cache(
                config, inspect_only, warnings, skipped
            )
        if config.uv_cleanup_enabled:
            result["uv_cache_bytes"] = prune_uv_cache(
                config, warnings, skipped, inspect_only
            )
        if config.brave_cleanup_enabled:
            result["brave_cache_bytes"] = manage_brave_cache(
                config, warnings, skipped, inspect_only
            )
        if config.npm_cleanup_enabled:
            result["npm_cache_bytes"] = clean_npm_cache(
                config, now, inspect_only, warnings, skipped
            )
        for enabled, name, action in (
            (config.yarn_cleanup_enabled, "yarn_cache", clean_yarn_cache),
            (
                config.playwright_cleanup_enabled,
                "playwright_cache",
                clean_playwright_cache,
            ),
            (config.zed_cleanup_enabled, "zed_caches", clean_zed_caches),
            (
                config.huggingface_cleanup_enabled,
                "huggingface_cache",
                clean_huggingface_cache,
            ),
            (config.zed_server_cleanup_enabled, "zed_server", clean_zed_server),
            (config.tmp_cleanup_enabled, "tmp_global", clean_tmp_global),
            (
                config.project_artifacts_cleanup_enabled,
                "project_artifacts",
                clean_project_artifacts,
            ),
        ):
            if enabled:
                result[name] = action(config, now, inspect_only, warnings, skipped)
        if config.docker_prune_enabled:
            result["docker_prune"] = prune_docker(
                config, inspect_only, warnings, skipped
            )
    if sys.platform == "linux":
        for user, key in (
            (False, "system_journal_bytes"),
            (True, "user_journal_bytes"),
        ):
            size, error = journal_size(user, config, skipped)
            if error:
                warnings.append(f"journal measurement failed: {error}")
            if (
                not updates_only
                and config.journal_cleanup_enabled
                and not dry_run
                and deletions_approved
            ):
                size = vacuum_journal(user, config, size, warnings, skipped)
            if size is not None:
                result[key] = size
                if size > config.journal_warn_size:
                    warnings.append(
                        f"{key} exceeds {format_bytes(config.journal_warn_size)}"
                    )
    result["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if warnings:
        result["status"] = "warning"
    return result
