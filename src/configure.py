#!/usr/bin/env python3
"""Select optional maintenance actions without granting deletion approval."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from config import load_config, read_environment


CHOICES = (
    ("UV cache", ("UV_CLEANUP_ENABLED",)),
    ("Brave cache", ("BRAVE_CLEANUP_ENABLED",)),
    ("npm cache", ("NPM_CLEANUP_ENABLED",)),
    ("Yarn cache", ("YARN_CLEANUP_ENABLED",)),
    ("Playwright browsers", ("PLAYWRIGHT_CLEANUP_ENABLED",)),
    ("Zed data and cache (can remove persistent state)", ("ZED_CLEANUP_ENABLED",)),
    ("Zed remote servers", ("ZED_SERVER_CLEANUP_ENABLED",)),
    ("Hugging Face orphaned downloads", ("HUGGINGFACE_CLEANUP_ENABLED",)),
    ("Project dependencies and build output", ("PROJECT_ARTIFACTS_CLEANUP_ENABLED",)),
    ("Docker unused resources (volumes preserved)", ("DOCKER_PRUNE_ENABLED",)),
    ("Old user-owned temporary files", ("TMP_CLEANUP_ENABLED",)),
    ("APT downloaded package cache (Linux)", ("APT_CACHE_CLEANUP_ENABLED",)),
    ("Archived system and user journals (Linux)", ("JOURNAL_CLEANUP_ENABLED",)),
    ("Homebrew old versions and downloads (macOS)", ("BREW_CLEANUP_ENABLED",)),
    (
        "Automatic verified Node.js and Bun upgrades (Apple Silicon macOS)",
        ("RUNTIME_UPDATES_ENABLED",),
    ),
)


def main() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "Action selection requires an interactive terminal. No settings changed.",
            file=sys.stderr,
        )
        return 1
    config = load_config()
    path = config.state_dir / "environment"
    values = read_environment(path)
    print("Choose optional maintenance actions. This replaces the previous selection.")
    for index, (label, _) in enumerate(CHOICES, start=1):
        print(f"  {index}. {label}")
    while True:
        try:
            answer = input(
                "Enter numbers separated by commas, or Enter for none: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nNo settings changed.")
            return 1
        try:
            selected = (
                {int(item.strip()) for item in answer.split(",")} if answer else set()
            )
        except ValueError:
            print("Use comma-separated numbers from the menu.")
            continue
        if any(index < 1 or index > len(CHOICES) for index in selected):
            print("Select only numbers from the menu.")
            continue
        break
    for index, (_, flags) in enumerate(CHOICES, start=1):
        for flag in flags:
            values[flag] = "true" if index in selected else "false"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as file:
        file.write(
            "# Selected maintenance settings. Cleanup still requires Y/N approval.\n"
        )
        for key, value in values.items():
            file.write(f"{key}={value}\n")
        temporary = Path(file.name)
    temporary.chmod(0o600)
    temporary.replace(path)
    print(
        f"Saved selection to {path}. Deletions still require approval for each cleanup pass."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
