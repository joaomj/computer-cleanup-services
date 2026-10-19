"""Daily security checks for the OS, project dependencies, and Homebrew."""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime
from typing import Any

from common import run_command
from config import Config
from homebrew_vulns import scan_homebrew
from osv import scan_projects
from oval import scan_ubuntu
from state import write_json


SEVERITY_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_ORDER = ("critical", "high", "medium", "low", "unknown")
def normalize_severity(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "moderate":
        return "medium"
    return normalized if normalized in SEVERITY_RANK else "unknown"


def _kernel_key(version: str) -> tuple[int, int, int, int, str] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)-(\d+)-(.+)$", version)
    if match is None:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        int(match.group(4)),
        match.group(5),
    )


def reboot_status(config: Config) -> dict[str, Any]:
    """Report whether a reboot is needed to activate installed updates."""
    if sys.platform != "linux":
        return {"required": None, "status": "unsupported"}
    running = os.uname().release
    reasons: list[str] = []
    required = False
    flags = "/var/run/reboot-required"
    if os.path.exists(flags):
        required = True
        reasons.append("packages requested a reboot")
    newest: str | None = None
    dpkg_query = shutil.which("dpkg-query")
    if dpkg_query is None:
        return {
            "required": required,
            "status": "partial",
            "running_kernel": running,
            "installed_kernel": None,
            "reasons": reasons + ["dpkg-query is not available"],
        }
    code, stdout, stderr = run_command(
        [dpkg_query, "-W", "-f=${Package}\n", "linux-image-*"],
        config,
        timeout_seconds=30,
    )
    if code == 0:
        candidates: dict[str, tuple[int, int, int, int, str]] = {}
        for line in stdout.splitlines():
            name = line.strip()
            if not name.startswith("linux-image-"):
                continue
            candidate = name.removeprefix("linux-image-")
            if any(marker in candidate for marker in ("unsigned", "dbg", "dbgsym")):
                continue
            key = _kernel_key(candidate)
            if key is not None:
                candidates[candidate] = key
        if candidates:
            newest = max(candidates, key=lambda item: candidates[item])
    elif stderr and "no packages found" not in stderr.lower():
        reasons.append(f"could not inspect installed kernels: {stderr[-300:]}")
    if newest is not None and newest != running:
        required = True
        reasons.append(f"newest installed kernel is {newest}")
    return {
        "required": required,
        "status": "ok" if code == 0 or newest is not None else "partial",
        "running_kernel": running,
        "installed_kernel": newest,
        "reasons": reasons,
    }


def _source_failure(name: str, error: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": f"{name} check failed: {error}",
        "findings": [],
    }


def _finding_key(finding: dict[str, Any]) -> tuple[Any, ...]:
    return (
        finding.get("source"),
        finding.get("id"),
        tuple(finding.get("packages", [])),
        finding.get("version"),
    )


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for finding in findings:
        key = _finding_key(finding)
        if key in unique:
            existing = unique[key]
            existing["sources"] = sorted(
                set(existing.get("sources", [])) | set(finding.get("sources", []))
            )
            continue
        unique[key] = finding
    return list(unique.values())


def _sort_key(finding: dict[str, Any]) -> tuple[Any, ...]:
    try:
        score = float(finding.get("cvss") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return (
        -SEVERITY_RANK.get(normalize_severity(finding.get("severity")), 0),
        -score,
        str(finding.get("id", "")),
    )


def _overall_status(
    sources: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
    reboot: dict[str, Any],
) -> str:
    failed = any(item.get("status") == "failed" for item in sources.values())
    unavailable = any(item.get("status") in {"unavailable", "partial"} for item in sources.values())
    if findings:
        return "partial" if failed or unavailable else "findings"
    if failed:
        return "failed"
    if unavailable:
        return "unavailable"
    if reboot.get("required"):
        return "ok"
    return "ok"


def _reason(sources: dict[str, dict[str, Any]]) -> str | None:
    for name, source in sources.items():
        if source.get("status") in {"failed", "partial", "unavailable"}:
            reason = source.get("reason")
            if reason:
                return f"{name}: {reason}"
    return None


def scan_security(config: Config, now: datetime, dry_run: bool) -> dict[str, Any]:
    """Run the security sources, write security.json, and return a summary."""
    if not config.security_scan_enabled:
        return {"status": "disabled"}
    if dry_run:
        return {"status": "dry-run"}
    sources: dict[str, dict[str, Any]] = {}
    if sys.platform == "linux":
        try:
            sources["os"] = scan_ubuntu(config)
        except (OSError, RuntimeError) as error:
            sources["os"] = _source_failure("OS", error)
    else:
        sources["os"] = {
            "status": "unsupported",
            "reason": "Ubuntu OVAL is Linux-only",
            "findings": [],
        }
    try:
        sources["projects"] = scan_projects(config, now)
    except (OSError, RuntimeError) as error:
        sources["projects"] = _source_failure("Project", error)
    if sys.platform == "darwin":
        try:
            sources["homebrew"] = scan_homebrew(config)
        except (OSError, RuntimeError) as error:
            sources["homebrew"] = _source_failure("Homebrew", error)
    else:
        sources["homebrew"] = {
            "status": "unsupported",
            "reason": "Homebrew scan is macOS-only",
            "findings": [],
        }
    reboot = reboot_status(config)
    findings: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_summaries: dict[str, dict[str, Any]] = {}
    for name, source in sources.items():
        findings.extend(source.get("findings", []))
        warnings.extend(str(item) for item in source.get("warnings", []))
        source_summaries[name] = {
            key: value
            for key, value in source.items()
            if key not in {"findings", "warnings"}
        }
    findings = _dedupe(findings)
    minimum = SEVERITY_RANK[config.security_min_severity]
    findings = [
        finding
        for finding in findings
        if SEVERITY_RANK.get(normalize_severity(finding.get("severity")), 0) >= minimum
    ]
    findings.sort(key=_sort_key)
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for finding in findings:
        counts[normalize_severity(finding.get("severity"))] += 1
    status = _overall_status(source_summaries, findings, reboot)
    record = {
        "status": status,
        "checked_at": now.isoformat(timespec="seconds"),
        "min_severity": config.security_min_severity,
        "counts": {**counts, "total": len(findings)},
        "sources": source_summaries,
        "reboot": reboot,
        "findings": findings,
        "warnings": warnings,
        "reason": _reason(source_summaries),
    }
    write_json(config.state_dir / "security.json", record)
    first = findings[0] if findings else None
    return {
        "status": status,
        "critical": counts["critical"],
        "high": counts["high"],
        "medium": counts["medium"],
        "low": counts["low"],
        "total": len(findings),
        "reboot_required": bool(reboot.get("required")),
        "reason": record["reason"],
        "first": (
            {
                "id": first.get("id"),
                "severity": normalize_severity(first.get("severity")),
                "package": (first.get("packages") or ["unknown"])[0],
            }
            if first
            else None
        ),
    }


def main() -> int:
    """Run only the security scan and print its summary."""
    import argparse
    import json

    from config import ConfigurationError, load_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report the plan without scanning"
    )
    args = parser.parse_args()
    try:
        config = load_config()
    except ConfigurationError as error:
        print(f"Security configuration error: {error}", file=sys.stderr)
        return 1
    summary = scan_security(config, datetime.now().astimezone(), args.dry_run)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
