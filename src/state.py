"""Private state records and the maintenance process lock."""

from __future__ import annotations

import fcntl
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import Config


@contextmanager
def cleanup_lock(config: Config) -> Iterator[bool]:
    """Acquire a non-blocking process lock for the cleanup run."""
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with config.lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Write JSON state atomically with private permissions."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as file:
        json.dump(value, file, indent=2, sort_keys=True)
        file.write("\n")
        temporary = Path(file.name)
    temporary.chmod(0o600)
    temporary.replace(path)
