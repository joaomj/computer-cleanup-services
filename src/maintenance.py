#!/usr/bin/env python3
"""Run the daily maintenance pass or review high-risk cleanup items."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from cleanup import execute
from config import Config, ConfigurationError, format_bytes, load_config
from decisions import (
    load_decisions,
    needs_decision,
    read_pending,
    record_decision,
    save_decisions,
    write_pending,
)
from high_risk import Candidate, delete
from kernel_update import offer_kernel_update
from pressure import near_full
from state import cleanup_lock, write_json


def read_record(path: Path) -> dict[str, Any]:
    """Read state, treating a missing first-run record as empty."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid maintenance state in {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"maintenance state must be an object: {path}")
    return value


def review_path(config: Config) -> Path:
    return config.state_dir / "review-status.json"


def daily_pass(config: Config, today: str) -> int:
    """Upgrade, clean low-risk data, and queue undecided high-risk items."""
    write_json(config.status_path, {"status": "running", "attempt_date": today})
    try:
        result = execute(config, dry_run=False)
        now = datetime.now().astimezone()
        decided = load_decisions(config)
        candidates = [Candidate.from_json(item) for item in result["high_risk_candidates"]]
        pending = [candidate for candidate in candidates if needs_decision(candidate, decided)]
        write_pending(config, pending, now)
        result["pending_review"] = len(pending)
    except (ConfigurationError, OSError, RuntimeError) as error:
        write_json(
            config.status_path,
            {"status": "failed", "attempt_date": today, "errors": [str(error)]},
        )
        print(f"Maintenance failed: {error}", file=sys.stderr)
        return 1
    write_json(config.status_path, result)
    return 0 if result["status"] != "failed" else 1


def ask(question: str) -> str | None:
    """Return "delete", "keep", or None when the user stops the review."""
    while True:
        try:
            answer = input(question).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if answer in {"y", "yes"}:
            return "delete"
        if answer in {"", "n", "no"}:
            return "keep"
        if answer in {"q", "quit"}:
            return None
        print("Enter y to delete, n to keep, or q to stop.")


def review(config: Config, today: str, force: bool, quiet: bool, ignore_pressure: bool) -> int:
    """Ask once about each pending item and record every answer.

    High-risk deletion is offered only when the system is short of space or
    memory. Without pressure, the items stay pending for a later day.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return 0
    if not force and read_record(review_path(config)).get("attempt_date") == today:
        return 0
    pending = read_pending(config)
    if not pending:
        return 0
    if not ignore_pressure:
        pressure, reasons = near_full(config)
        if not pressure:
            # No attempt date: a later shell offers the items once pressure appears.
            write_json(
                review_path(config),
                {
                    "status": "deferred",
                    "pending": len(pending),
                    "reason": "system is not near full",
                },
            )
            if not quiet:
                print("High-risk review skipped: the system is not near full.")
            return 0
    write_json(review_path(config), {"attempt_date": today, "status": "running"})
    total = sum(candidate.size for candidate in pending)
    print(
        f"\nCleanup review: {len(pending)} high-risk item(s), {format_bytes(total)}."
        "\nEach answer is saved. A kept item returns only if it grows by more than 25%."
        "\nEnter y to delete, n or Enter to keep, q to stop and decide later.\n"
    )
    decided = load_decisions(config)
    remaining = list(pending)
    results: list[dict[str, Any]] = []
    for candidate in pending:
        answer = ask(f"Delete {candidate.label} ({format_bytes(candidate.size)})? [y/N/q] ")
        if answer is None:
            break
        now = datetime.now().astimezone()
        if answer == "delete":
            outcome = delete(candidate, config, now)
            print(f"  deleted {format_bytes(outcome['deleted_bytes'])}")
            for reason in outcome["kept"]:
                print(f"  kept {reason}")
            results.append({"key": candidate.key, "decision": "delete", **outcome})
        else:
            results.append({"key": candidate.key, "decision": "keep"})
        record_decision(decided, candidate, answer, now)
        save_decisions(config, decided)
        remaining.remove(candidate)
        write_pending(config, remaining, now)
    write_json(
        review_path(config),
        {
            "attempt_date": today,
            "status": "success",
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "answered": results,
            "undecided": len(remaining),
        },
    )
    if remaining:
        print(f"{len(remaining)} item(s) left for a later review.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--review",
        action="store_true",
        help="ask about pending high-risk items in a terminal",
    )
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="report what the daily pass would do without upgrades or deletions",
    )
    parser.add_argument(
        "--force", action="store_true", help="ignore today's daily-pass or review record"
    )
    parser.add_argument(
        "--ignore-pressure",
        action="store_true",
        help="review high-risk items even when the system is not near full",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print nothing when no review is due"
    )
    args = parser.parse_args()
    try:
        config = load_config()
    except ConfigurationError as error:
        print(f"Maintenance configuration error: {error}", file=sys.stderr)
        return 1
    today = datetime.now().astimezone().date().isoformat()
    daily = not args.review and not args.dry_run
    # Check the daily gate before locking so a finished day never blocks a review.
    if daily and not args.force and read_record(config.status_path).get("attempt_date") == today:
        return 0
    with cleanup_lock(config) as acquired:
        if not acquired:
            # A review that meets a running pass stays quiet; items wait for a later shell.
            if args.dry_run:
                print("Maintenance is already running. Try again after it finishes.")
            return 0
        if args.dry_run:
            result = execute(config, dry_run=True)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] != "failed" else 1
        if args.review:
            # The kernel question is never gated by disk or memory pressure.
            if sys.stdin.isatty() and sys.stdout.isatty():
                if offer_kernel_update(config, today, args.force) == "stopped":
                    return 0
            return review(
                config, today, args.force, args.quiet, args.ignore_pressure
            )
        if not args.force and read_record(config.status_path).get("attempt_date") == today:
            return 0
        return daily_pass(config, today)


if __name__ == "__main__":
    raise SystemExit(main())
