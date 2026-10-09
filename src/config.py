#!/usr/bin/env python3
"""Validated configuration for the daily storage maintenance service."""

from __future__ import annotations

import getpass
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


SIZE_UNITS: dict[str, int] = {
    "B": 1,
    "KB": 1_000,
    "KIB": 1_024,
    "MB": 1_000_000,
    "MIB": 1_048_576,
    "GB": 1_000_000_000,
    "GIB": 1_073_741_824,
    "TB": 1_000_000_000_000,
    "TIB": 1_099_511_627_776,
}
SIZE_PATTERN = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}

SERVICE_NAME = (
    "macos-cleanup-service" if sys.platform == "darwin" else "linux-cleanup-service"
)


class ConfigurationError(ValueError):
    """Raised when a service setting has an invalid value."""


def parse_size(value: str, name: str) -> int:
    """Parse a positive byte size such as ``2GiB``."""
    match = SIZE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ConfigurationError(f"{name} must be a size such as 500MiB or 2GiB")
    number = float(match.group(1))
    unit = match.group(2).upper()
    multiplier = SIZE_UNITS.get(unit)
    if multiplier is None:
        raise ConfigurationError(f"{name} uses an unknown size unit: {unit}")
    result = int(number * multiplier)
    if result <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return result


def parse_positive_int(value: str, name: str) -> int:
    """Parse a positive integer setting."""
    try:
        result = int(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if result <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return result


def parse_bool(value: str, name: str) -> bool:
    """Parse a boolean setting."""
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def format_bytes(value: int) -> str:
    """Format bytes with a compact binary unit."""
    if value < 1_024:
        return f"{value}B"
    units = ("KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        amount /= 1_024
        if amount < 1_024 or unit == units[-1]:
            return f"{amount:.1f}{unit}"
    return f"{value}B"


def read_environment(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE settings without shell evaluation."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(f"{path}:{line_number} must contain KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if ENV_NAME_PATTERN.fullmatch(key) is None:
            raise ConfigurationError(
                f"{path}:{line_number} has an invalid variable name"
            )
        values[key] = value.strip()
    return values


def _value(values: dict[str, str], name: str, default: str) -> str:
    return values.get(name, default)


@dataclass(frozen=True)
class Config:
    """Validated settings for the selected maintenance actions."""

    home: Path
    state_dir: Path
    status_path: Path
    lock_path: Path
    uv_cache: Path
    brave_cache: Path
    npm_cache: Path
    yarn_cache: Path
    playwright_cache: Path
    zed_data: Path
    zed_cache: Path
    huggingface_cache: Path
    zed_server: Path
    project_root: Path
    tmp_dir: Path
    uv_cache_max_size: int
    brave_cache_max_size: int
    npm_cache_warn_size: int
    cache_max_age_hours: int
    uv_cleanup_enabled: bool
    brave_cleanup_enabled: bool
    npm_cleanup_enabled: bool
    uv_cache_clean_daily: bool
    uv_cache_max_age_hours: int
    npm_cache_clean_daily: bool
    npm_cache_max_age_hours: int
    yarn_cleanup_enabled: bool
    playwright_cleanup_enabled: bool
    zed_cleanup_enabled: bool
    huggingface_cleanup_enabled: bool
    zed_server_cleanup_enabled: bool
    zed_server_keep_count: int
    project_artifacts_cleanup_enabled: bool
    project_artifacts_max_age_hours: int
    docker_prune_enabled: bool
    docker_prune_max_age_hours: int
    apt_cache_cleanup_enabled: bool
    tmp_cleanup_enabled: bool
    tmp_max_age_hours: int
    journal_cleanup_enabled: bool
    journal_warn_size: int
    journal_system_max_use: str
    journal_system_max_file_size: str
    journal_runtime_max_use: str
    journal_runtime_max_file_size: str
    command_timeout_seconds: int
    brew_updates_enabled: bool
    brew_cleanup_enabled: bool
    brew_upgrade_min_age_days: int
    runtime_updates_enabled: bool
    runtime_node_major: int
    runtime_min_release_age_days: int
    runtime_install_root: Path
    runtime_bin_dir: Path
    runtime_network_timeout_seconds: int
    runtime_gpgv_path: Path | None
    runtime_gpg_path: Path | None
    apt_updates_enabled: bool
    snap_updates_enabled: bool


def load_config() -> Config:
    """Load defaults, the user configuration file, and process overrides."""
    home = Path.home()
    is_macos = sys.platform == "darwin"
    if "XDG_CONFIG_HOME" in os.environ:
        config_dir = Path(os.environ["XDG_CONFIG_HOME"]) / SERVICE_NAME
    elif is_macos:
        config_dir = home / "Library" / "Application Support" / SERVICE_NAME
    else:
        config_dir = home / ".config" / SERVICE_NAME
    values = {**read_environment(config_dir / "environment"), **os.environ}

    def size(name: str, default: str) -> int:
        return parse_size(_value(values, name, default), name)

    def integer(name: str, default: str) -> int:
        return parse_positive_int(_value(values, name, default), name)

    def enabled(name: str, default: str = "false") -> bool:
        return parse_bool(_value(values, name, default), name)

    def journal_limit(name: str, default: str) -> str:
        value = _value(values, name, default)
        if re.fullmatch(r"[1-9][0-9]*[KMGT]?", value, re.IGNORECASE) is None:
            raise ConfigurationError(
                f"{name} must be a positive journal size such as 80M; remove spaces and extra arguments"
            )
        return value

    def path(name: str, default: Path) -> Path:
        result = Path(_value(values, name, str(default))).expanduser()
        if not result.is_absolute():
            raise ConfigurationError(
                f"{name} must be an absolute path; set it to an absolute directory"
            )
        return result

    def optional_path(name: str) -> Path | None:
        raw = _value(values, name, "").strip()
        return path(name, Path(raw)) if raw else None

    cache_root = Path(
        os.environ.get(
            "XDG_CACHE_HOME",
            home / "Library" / "Caches" if is_macos else home / ".cache",
        )
    )
    uv_root = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
    data_root = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return Config(
        home=home,
        state_dir=config_dir,
        status_path=config_dir / "status.json",
        lock_path=config_dir / "cleanup.lock",
        uv_cache=path("UV_CACHE_DIR", uv_root / "uv"),
        brave_cache=path(
            "BRAVE_CACHE_PATH",
            cache_root / "BraveSoftware" / "Brave-Browser" / "Default" / "Cache"
            if is_macos
            else cache_root / "BraveSoftware" / "Brave-Browser",
        ),
        npm_cache=path("NPM_CACHE_PATH", home / ".npm"),
        yarn_cache=path("YARN_CACHE_PATH", cache_root / "yarn"),
        playwright_cache=path("PLAYWRIGHT_CACHE_PATH", cache_root / "ms-playwright"),
        zed_data=path("ZED_DATA_PATH", data_root / "zed"),
        zed_cache=path("ZED_CACHE_PATH", cache_root / "zed"),
        huggingface_cache=path("HUGGINGFACE_CACHE_PATH", uv_root / "huggingface"),
        zed_server=path("ZED_SERVER_PATH", home / ".zed_server"),
        project_root=path("PROJECT_ROOT", home / "projects"),
        tmp_dir=path("TMP_DIR", Path("/tmp")),
        uv_cache_max_size=size("UV_CACHE_MAX_SIZE", "1GiB"),
        brave_cache_max_size=size("BRAVE_CACHE_MAX_SIZE", "2GiB"),
        npm_cache_warn_size=size("NPM_CACHE_WARN_SIZE", "1GiB"),
        cache_max_age_hours=integer("CACHE_MAX_AGE_HOURS", "24"),
        uv_cleanup_enabled=enabled("UV_CLEANUP_ENABLED"),
        brave_cleanup_enabled=enabled("BRAVE_CLEANUP_ENABLED"),
        npm_cleanup_enabled=enabled("NPM_CLEANUP_ENABLED"),
        uv_cache_clean_daily=enabled("UV_CACHE_CLEAN_DAILY", "true"),
        uv_cache_max_age_hours=integer("UV_CACHE_MAX_AGE_HOURS", "24"),
        npm_cache_clean_daily=enabled("NPM_CACHE_CLEAN_DAILY", "true"),
        npm_cache_max_age_hours=integer("NPM_CACHE_MAX_AGE_HOURS", "24"),
        yarn_cleanup_enabled=enabled("YARN_CLEANUP_ENABLED"),
        playwright_cleanup_enabled=enabled("PLAYWRIGHT_CLEANUP_ENABLED"),
        zed_cleanup_enabled=enabled("ZED_CLEANUP_ENABLED"),
        huggingface_cleanup_enabled=enabled("HUGGINGFACE_CLEANUP_ENABLED"),
        zed_server_cleanup_enabled=enabled("ZED_SERVER_CLEANUP_ENABLED"),
        zed_server_keep_count=integer("ZED_SERVER_KEEP_COUNT", "2"),
        project_artifacts_cleanup_enabled=enabled("PROJECT_ARTIFACTS_CLEANUP_ENABLED"),
        project_artifacts_max_age_hours=integer(
            "PROJECT_ARTIFACTS_MAX_AGE_HOURS", "24"
        ),
        docker_prune_enabled=enabled("DOCKER_PRUNE_ENABLED"),
        docker_prune_max_age_hours=integer("DOCKER_PRUNE_MAX_AGE_HOURS", "24"),
        apt_cache_cleanup_enabled=enabled("APT_CACHE_CLEANUP_ENABLED"),
        tmp_cleanup_enabled=enabled("TMP_CLEANUP_ENABLED"),
        tmp_max_age_hours=integer("TMP_MAX_AGE_HOURS", "24"),
        journal_cleanup_enabled=enabled("JOURNAL_CLEANUP_ENABLED"),
        journal_warn_size=size("JOURNAL_WARN_SIZE", "100MiB"),
        journal_system_max_use=journal_limit("JOURNAL_SYSTEM_MAX_USE", "80M"),
        journal_system_max_file_size=journal_limit(
            "JOURNAL_SYSTEM_MAX_FILE_SIZE", "8M"
        ),
        journal_runtime_max_use=journal_limit("JOURNAL_RUNTIME_MAX_USE", "16M"),
        journal_runtime_max_file_size=journal_limit(
            "JOURNAL_RUNTIME_MAX_FILE_SIZE", "8M"
        ),
        command_timeout_seconds=integer("COMMAND_TIMEOUT_SECONDS", "900"),
        brew_updates_enabled=enabled("BREW_UPDATES_ENABLED", "true"),
        brew_cleanup_enabled=enabled("BREW_CLEANUP_ENABLED"),
        brew_upgrade_min_age_days=integer("BREW_UPGRADE_MIN_AGE_DAYS", "7"),
        runtime_updates_enabled=enabled("RUNTIME_UPDATES_ENABLED"),
        runtime_node_major=integer("RUNTIME_NODE_MAJOR", "24"),
        runtime_min_release_age_days=integer("RUNTIME_MIN_RELEASE_AGE_DAYS", "7"),
        runtime_install_root=path("RUNTIME_INSTALL_ROOT", home / ".local" / "opt"),
        runtime_bin_dir=path("RUNTIME_BIN_DIR", home / ".local" / "bin"),
        runtime_network_timeout_seconds=integer(
            "RUNTIME_NETWORK_TIMEOUT_SECONDS", "30"
        ),
        runtime_gpgv_path=optional_path("RUNTIME_GPGV_PATH"),
        runtime_gpg_path=optional_path("RUNTIME_GPG_PATH"),
        apt_updates_enabled=enabled("APT_UPDATES_ENABLED", "true"),
        snap_updates_enabled=enabled("SNAP_UPDATES_ENABLED", "true"),
    )


def print_journal_config() -> None:
    """Print journal limits for explicit administrator use."""
    config = load_config()
    print("[Journal]")
    print("Compress=yes")
    print(f"SystemMaxUse={config.journal_system_max_use}")
    print(f"SystemMaxFileSize={config.journal_system_max_file_size}")
    print(f"RuntimeMaxUse={config.journal_runtime_max_use}")
    print(f"RuntimeMaxFileSize={config.journal_runtime_max_file_size}")


def print_journal_vacuum_size() -> None:
    """Print the journal vacuum target."""
    print(load_config().journal_system_max_use)


def print_sudoers_config() -> None:
    """Print scoped passwordless sudo commands for this user."""
    config = load_config()
    commands = (
        "/usr/bin/apt-get update",
        "/usr/bin/apt-get upgrade -y",
        "/usr/bin/apt-get clean",
        "/usr/bin/apt-get autoclean",
        "/usr/bin/snap refresh",
        "/usr/bin/journalctl --rotate",
        f"/usr/bin/journalctl --vacuum-size={config.journal_system_max_use}",
    )
    lines = [getpass.getuser() + " ALL=(root) " + "NOPASSWD" + ": " + commands[0]]
    lines.extend(f"    {command}" for command in commands[1:])
    print("# linux-cleanup-service: scoped maintenance commands")
    print(",\\\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "journal-config":
        print_journal_config()
    elif len(sys.argv) == 2 and sys.argv[1] == "journal-vacuum-size":
        print_journal_vacuum_size()
    elif len(sys.argv) == 2 and sys.argv[1] == "sudoers-config":
        print_sudoers_config()
    else:
        print(format_bytes(load_config().journal_warn_size))
