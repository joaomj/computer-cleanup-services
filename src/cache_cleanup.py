"""Regenerable-cache pruners under the daily 24h rule."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from common import (
    mark_skipped,
    measure_path,
    permission_limited,
    process_is_running,
    process_uses_path,
    run_command,
)
from config import Config, format_bytes


def clear_cache(path: Path, active_check: Callable[[], bool] | None = None) -> None:
    """Remove cache contents while preserving the cache directory."""
    if path.is_symlink():
        raise RuntimeError(f"refusing to clear symlink cache: {path}")
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if active_check is not None and active_check():
            raise RuntimeError("Brave started during cache cleanup")
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def dir_oldest_mtime_hours(path: Path, now: datetime) -> float | None:
    """Return age in hours of oldest file under path, or None if empty/missing."""
    try:
        if not path.exists():
            return None
        # Fast path: use find to avoid walking 20k files in Python.
        find = shutil.which("find")
        if find and path.is_dir():
            code, stdout, _ = (124, "", "")
            try:
                result = subprocess.run(
                    [find, str(path), "-mindepth", "1", "-printf", "%T@\n"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd="/",
                )
                code, stdout = result.returncode, result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                code = 124
            if code == 0 and stdout:
                try:
                    oldest_ts = min(
                        float(line) for line in stdout.splitlines() if line.strip()
                    )
                    return max(0.0, (now.timestamp() - oldest_ts) / 3600.0)
                except ValueError:
                    pass
        # Fallback: stat the dir itself.
        return max(0.0, (now.timestamp() - path.stat().st_mtime) / 3600.0)
    except OSError:
        return None


def uv_lock_diagnosis(cache_dir: Path) -> str:
    """Explain why the UV cache lock is held (stale vs active)."""
    lock_path = cache_dir / ".lock"
    if not lock_path.exists():
        return "no lock file"
    # Check for live uv processes owned by this user.
    uv_running = process_is_running({"uv", "uvx"})
    # Try non-blocking flock to see if lock is actually held.
    try:
        with lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                return "lock file exists but not held (stale)"
            except BlockingIOError:
                pass
    except OSError as error:
        return f"lock file unreadable: {error}"
    if uv_running:
        return "held by live uv/uvx process (active, skipping)"
    return (
        "held but no live uv/uvx process (stale lock from crashed run, safe to prune)"
    )


def prune_uv_cache(
    config: Config, warnings: list[str], skipped: list[str], dry_run: bool
) -> int:
    """Prune UV cache daily (24h rule) or above threshold. Fixes stale-lock skip."""
    # Measure first: an unavailable uv binary must still report the real size
    # instead of 0, otherwise the status record hides a growing cache.
    try:
        size = measure_path(config.uv_cache)
    except PermissionError:
        mark_skipped(skipped, "UV cache prune")
        return 0
    uv = shutil.which("uv")
    if uv is None:
        # Service PATH includes ~/.local/bin but cron/sudo may not; check common spots.
        for candidate in (
            Path.home() / ".local" / "bin" / "uv",
            Path("/usr/local/bin/uv"),
            Path("/usr/bin/uv"),
        ):
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    uv = str(candidate)
                    break
            except OSError:
                continue
    if uv is None:
        mark_skipped(skipped, "UV cache prune")
        return size
    now = datetime.now().astimezone()
    age_hours = dir_oldest_mtime_hours(config.uv_cache, now)
    needs_prune = (
        size > config.uv_cache_max_size
        or getattr(config, "uv_cache_clean_daily", True)
        or (
            age_hours is not None
            and age_hours >= getattr(config, "uv_cache_max_age_hours", 24)
        )
    )
    if not needs_prune or dry_run:
        return size
    # Distinguish active vs stale lock (previous code skipped on any lock,
    # even when user was idle and holder had crashed).
    diagnosis = uv_lock_diagnosis(config.uv_cache)
    if "active" in diagnosis:
        warnings.append(f"UV cache prune skipped: {diagnosis}")
        mark_skipped(skipped, "UV cache prune (active lock)")
        return size
    # Stale lock (crashed run): proceeding is safe and expected, not warning-worthy.
    # (The result - pruned vs skipped-on-error - is recorded below.)
    code, _, stderr = run_command(
        [uv, "cache", "prune", "--cache-dir", str(config.uv_cache)], config
    )
    if code != 0:
        if permission_limited(stderr):
            mark_skipped(skipped, "UV cache prune")
            return size
        warnings.append(f"UV cache prune failed: {stderr or 'unknown error'}")
    try:
        return measure_path(config.uv_cache)
    except PermissionError:
        mark_skipped(skipped, "UV cache measurement")
        return size


def manage_brave_cache(
    config: Config, warnings: list[str], skipped: list[str], dry_run: bool
) -> int:
    """Clear Brave cache only when Brave is stopped."""
    try:
        size = measure_path(config.brave_cache)
    except PermissionError:
        mark_skipped(skipped, "Brave cache")
        return 0
    if size <= config.brave_cache_max_size or dry_run:
        return size
    if process_is_running({"brave", "brave-browser", "brave-browser-stable"}):
        warnings.append(
            f"Brave cache exceeds {format_bytes(config.brave_cache_max_size)}; Brave is running"
        )
        return size
    try:
        clear_cache(
            config.brave_cache,
            lambda: process_is_running(
                {"brave", "brave-browser", "brave-browser-stable"}
            ),
        )
    except PermissionError:
        mark_skipped(skipped, "Brave cache cleanup")
    except (OSError, RuntimeError) as error:
        warnings.append(f"Brave cache cleanup failed: {error}")
    try:
        return measure_path(config.brave_cache)
    except PermissionError:
        mark_skipped(skipped, "Brave cache measurement")
        return size


def prune_dir_by_age(
    path: Path,
    max_age_hours: int,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
    label: str,
) -> dict[str, int]:
    """Delete files/dirs under path older than max_age_hours and not in use.

    24h rule: caches are temporary. Returns {deleted, deleted_bytes, kept}.
    Never follows symlinks, never deletes the top dir itself.
    """
    result = {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    try:
        if not path.exists() or path.is_symlink():
            return result
        if not path.is_dir():
            return result
    except PermissionError:
        mark_skipped(skipped, f"{label} inspection")
        return result
    cutoff = now.timestamp() - max_age_hours * 3600
    try:
        children = list(path.iterdir())
    except PermissionError:
        mark_skipped(skipped, f"{label} inspection")
        return result
    except OSError as error:
        warnings.append(f"{label} inspection failed: {error}")
        return result
    for child in children:
        try:
            if child.is_symlink():
                result["kept"] += 1
                continue
            try:
                mtime = child.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime >= cutoff:
                result["kept"] += 1
                continue
            if process_uses_path(child):
                result["kept"] += 1
                continue
            size = measure_path(child)
            if dry_run:
                result["deleted"] += 1
                result["deleted_bytes"] += size
                continue
            if process_uses_path(child):
                result["kept"] += 1
                continue
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                result["kept"] += 1
                continue
            result["deleted"] += 1
            result["deleted_bytes"] += size
        except PermissionError:
            mark_skipped(skipped, f"{label} cleanup")
        except OSError as error:
            warnings.append(f"{label} cleanup failed for {child.name}: {error}")
    return result


def clean_npm_cache(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> int:
    """Daily npm cleanup (was measure-only). Runs verify + clean --force."""
    try:
        size = measure_path(config.npm_cache)
    except PermissionError:
        mark_skipped(skipped, "npm cache measurement")
        return 0
    clean_daily = bool(getattr(config, "npm_cache_clean_daily", True))
    max_age = int(getattr(config, "npm_cache_max_age_hours", 24))
    age_hours = dir_oldest_mtime_hours(config.npm_cache, now)
    needs = (
        clean_daily
        or size > config.npm_cache_warn_size
        or (age_hours is not None and age_hours >= max_age)
    )
    if not needs or dry_run:
        return size
    npm = shutil.which("npm")
    if npm is None:
        # Fallback: age-based file deletion when npm binary missing.
        r = prune_dir_by_age(
            config.npm_cache, max_age, now, dry_run, warnings, skipped, "npm cache"
        )
        warnings.append(f"npm binary missing; pruned {r['deleted']} entries by age")
        try:
            return measure_path(config.npm_cache)
        except PermissionError:
            return size
    # Verify first (safe), then force clean (cache is regenerable, 24h rule).
    run_command([npm, "cache", "verify"], config)
    code, _, stderr = run_command([npm, "cache", "clean", "--force"], config)
    if code != 0:
        if permission_limited(stderr):
            mark_skipped(skipped, "npm cache clean")
            return size
        warnings.append(f"npm cache clean failed: {stderr or 'unknown error'}")
    try:
        return measure_path(config.npm_cache)
    except PermissionError:
        return size


def clean_yarn_cache(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Daily yarn cache cleanup. Prefers `yarn cache clean --all`, falls back to age prune."""
    if not bool(getattr(config, "yarn_cleanup_enabled", True)):
        return {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    yarn_path: Path = getattr(config, "yarn_cache", Path.home() / ".cache" / "yarn")
    max_age = int(getattr(config, "cache_max_age_hours", 24))
    if dry_run:
        try:
            size = measure_path(yarn_path)
        except PermissionError:
            size = 0
        return {"deleted": 0, "deleted_bytes": size, "kept": 0}
    yarn = shutil.which("yarn")
    if yarn is not None:
        code, _, stderr = run_command([yarn, "cache", "clean", "--all"], config)
        if code == 0:
            try:
                size = measure_path(yarn_path)
            except PermissionError:
                size = 0
            return {"deleted": 1, "deleted_bytes": 0, "kept": 0, "after_bytes": size}  # type: ignore[typeddict-unknown-key]
        if permission_limited(stderr):
            mark_skipped(skipped, "yarn cache clean")
            return {"deleted": 0, "deleted_bytes": 0, "kept": 0}
        warnings.append(
            f"yarn cache clean failed ({stderr or 'unknown'}); falling back to age prune"
        )
    return prune_dir_by_age(
        yarn_path, max_age, now, dry_run, warnings, skipped, "yarn cache"
    )


def clean_playwright_cache(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Daily Playwright browser cleanup. Age-based, skips in-use."""
    if not bool(getattr(config, "playwright_cleanup_enabled", True)):
        return {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    pw_path: Path = getattr(
        config, "playwright_cache", Path.home() / ".cache" / "ms-playwright"
    )
    max_age = int(getattr(config, "cache_max_age_hours", 24))
    return prune_dir_by_age(
        pw_path, max_age, now, dry_run, warnings, skipped, "playwright cache"
    )


def clean_zed_caches(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Daily Zed data and cache cleanup. Keeps dir, prunes old entries."""
    if not bool(getattr(config, "zed_cleanup_enabled", True)):
        return {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    max_age = int(getattr(config, "cache_max_age_hours", 24))
    total = {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    for label, p in (
        (
            "zed data",
            getattr(config, "zed_data", Path.home() / ".local" / "share" / "zed"),
        ),
        ("zed cache", getattr(config, "zed_cache", Path.home() / ".cache" / "zed")),
    ):
        r = prune_dir_by_age(Path(p), max_age, now, dry_run, warnings, skipped, label)
        total["deleted"] += r["deleted"]
        total["deleted_bytes"] += r["deleted_bytes"]
        total["kept"] += r["kept"]
    return total


def clean_huggingface_cache(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Daily HuggingFace hub cleanup. Deletes orphaned blobs and stale partial downloads.

    Blobs referenced by a snapshot symlink are live models and are never
    deleted, regardless of age. Only unreferenced blobs and `.incomplete`
    files older than the 24h rule and not in use are removed. refs/ metadata
    and non-hub content (xet/, tokens) are left alone.
    """
    if not bool(getattr(config, "huggingface_cleanup_enabled", True)):
        return {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    root: Path = getattr(
        config, "huggingface_cache", Path.home() / ".cache" / "huggingface"
    )
    max_age = int(getattr(config, "cache_max_age_hours", 24))
    result = {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    try:
        hub = Path(root) / "hub"
        if not hub.is_dir() or hub.is_symlink():
            return result
        model_dirs = [
            child
            for child in hub.iterdir()
            if child.is_dir()
            and not child.is_symlink()
            and child.name.startswith("models--")
        ]
    except PermissionError:
        mark_skipped(skipped, "huggingface cache inspection")
        return result
    except OSError as error:
        warnings.append(f"huggingface cache inspection failed: {error}")
        return result
    # Blobs referenced by snapshot symlinks are live model files.
    referenced: set[str] = set()
    for model in model_dirs:
        snapshots = model / "snapshots"
        try:
            if not snapshots.exists():
                continue
            if snapshots.is_symlink() or not snapshots.is_dir():
                raise RuntimeError(
                    f"refusing uninspectable Hugging Face snapshot root: {snapshots}"
                )
            pending = [snapshots]
            while pending:
                directory = pending.pop()
                for entry in directory.iterdir():
                    if entry.is_symlink():
                        if entry.is_dir():
                            raise RuntimeError(
                                f"refusing snapshot directory symlink: {entry}"
                            )
                        referenced.add(entry.readlink().name)
                    elif entry.is_dir():
                        pending.append(entry)
        except PermissionError:
            mark_skipped(skipped, "huggingface snapshot inspection; no blobs removed")
            return result
        except OSError as error:
            raise RuntimeError(
                f"Hugging Face snapshot inspection failed; no blobs removed: {snapshots}: {error}"
            ) from error
    cutoff = now.timestamp() - max_age * 3600
    for model in model_dirs:
        try:
            blobs = model / "blobs"
            if not blobs.is_dir() or blobs.is_symlink():
                continue
            entries = list(blobs.iterdir())
        except PermissionError:
            mark_skipped(skipped, "huggingface cache inspection")
            return result
        except OSError:
            continue
        for entry in entries:
            try:
                if (
                    entry.name in referenced
                    or entry.is_symlink()
                    or not entry.is_file()
                ):
                    result["kept"] += 1
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except FileNotFoundError:
                    continue
                if mtime >= cutoff or process_uses_path(entry):
                    result["kept"] += 1
                    continue
                size = measure_path(entry)
                if not dry_run:
                    if process_uses_path(entry):
                        result["kept"] += 1
                        continue
                    entry.unlink()
                result["deleted"] += 1
                result["deleted_bytes"] += size
            except PermissionError:
                mark_skipped(skipped, "huggingface cache cleanup")
            except OSError as error:
                warnings.append(
                    f"huggingface cache cleanup failed for {entry.name}: {error}"
                )
    return result


def clean_zed_server(
    config: Config,
    now: datetime,
    dry_run: bool,
    warnings: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Remove old versioned Zed remote-server installs.

    Zed drops one `zed-remote-server-*` entry per remote version into
    `~/.zed_server` (usually executable files, occasionally directories)
    and never removes old ones. Keeps the newest `ZED_SERVER_KEEP_COUNT`
    installs unconditionally (newest build numbers sort last); older ones
    are removed once they pass the 24h rule and are not in use. Anything
    that does not look like a versioned server install is left alone.
    """
    if not bool(getattr(config, "zed_server_cleanup_enabled", True)):
        return {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    root: Path = getattr(config, "zed_server", Path.home() / ".zed_server")
    keep = max(1, int(getattr(config, "zed_server_keep_count", 2)))
    max_age = int(getattr(config, "cache_max_age_hours", 24))
    result = {"deleted": 0, "deleted_bytes": 0, "kept": 0}
    try:
        if not root.is_dir() or root.is_symlink():
            return result
        children = list(root.iterdir())
    except PermissionError:
        mark_skipped(skipped, "zed server inspection")
        return result
    except OSError as error:
        warnings.append(f"zed server inspection failed: {error}")
        return result
    installs: list[tuple[float, str, Path]] = []
    for child in children:
        try:
            if child.is_symlink() or not child.name.startswith("zed-remote-server-"):
                result["kept"] += 1
                continue
            try:
                is_file = child.is_file()
                is_dir = child.is_dir()
            except OSError:
                result["kept"] += 1
                continue
            if not is_file and not is_dir:
                result["kept"] += 1
                continue
            try:
                installs.append((child.stat().st_mtime, child.name, child))
            except FileNotFoundError:
                continue
        except PermissionError:
            mark_skipped(skipped, "zed server inspection")
        except OSError:
            result["kept"] += 1
            continue
    installs.sort(key=lambda item: (item[0], item[1]))
    victims = installs[: max(0, len(installs) - keep)]
    result["kept"] += len(installs) - len(victims)
    cutoff = now.timestamp() - max_age * 3600
    for mtime, _, victim in victims:
        try:
            if mtime >= cutoff or process_uses_path(victim):
                result["kept"] += 1
                continue
            size = measure_path(victim)
            if dry_run:
                result["deleted"] += 1
                result["deleted_bytes"] += size
                continue
            if process_uses_path(victim):
                result["kept"] += 1
                continue
            if victim.is_file():
                victim.unlink()
            else:
                shutil.rmtree(victim)
            result["deleted"] += 1
            result["deleted_bytes"] += size
        except PermissionError:
            mark_skipped(skipped, "zed server cleanup")
        except OSError as error:
            warnings.append(f"zed server cleanup failed for {victim.name}: {error}")
    return result
