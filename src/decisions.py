"""Recorded decisions and pending review items for high-risk cleanup."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import Config
from high_risk import Candidate
from state import write_json

# A kept item is offered again only after it grows by more than this factor.
REOFFER_GROWTH = 1.25


def decisions_path(config: Config) -> Path:
    return config.state_dir / "decisions.json"


def pending_path(config: Config) -> Path:
    return config.state_dir / "pending-review.json"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid cleanup record {path}: {error}") from error


def load_decisions(config: Config) -> dict[str, dict[str, Any]]:
    """Read recorded decisions keyed by candidate identity."""
    path = decisions_path(config)
    value = _read(path)
    if value is None:
        return {}
    if not isinstance(value, dict) or not isinstance(value.get("items"), dict):
        raise RuntimeError(f"cleanup decisions must contain an items object: {path}")
    return value["items"]


def save_decisions(config: Config, items: dict[str, dict[str, Any]]) -> None:
    write_json(decisions_path(config), {"items": items})


def needs_decision(candidate: Candidate, items: dict[str, dict[str, Any]]) -> bool:
    """Return whether a candidate must be offered.

    A kept item stays quiet until it grows by more than 25%. Deleted items are
    recorded for reference; if the item returns, it is offered again.
    """
    previous = items.get(candidate.key)
    if previous is None or previous.get("decision") != "keep":
        return True
    return candidate.size > int(previous.get("size", 0)) * REOFFER_GROWTH


def record_decision(
    items: dict[str, dict[str, Any]], candidate: Candidate, decision: str, now: datetime
) -> None:
    items[candidate.key] = {
        "decision": decision,
        "size": candidate.size,
        "label": candidate.label,
        "decided_at": now.isoformat(timespec="seconds"),
    }


def write_pending(config: Config, candidates: list[Candidate], now: datetime) -> None:
    """Store items for the next terminal review, or clear the list."""
    path = pending_path(config)
    if not candidates:
        path.unlink(missing_ok=True)
        return
    write_json(
        path,
        {
            "generated_at": now.isoformat(timespec="seconds"),
            "items": [candidate.to_json() for candidate in candidates],
        },
    )


def read_pending(config: Config) -> list[Candidate]:
    path = pending_path(config)
    value = _read(path)
    if value is None:
        return []
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise RuntimeError(f"pending review must contain an items list: {path}")
    return [Candidate.from_json(item) for item in value["items"]]
