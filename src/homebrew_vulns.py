"""Homebrew vulnerability checks through `brew vulns`."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from common import run_command
from config import Config


def find_brew() -> str | None:
    """Locate Homebrew even when the service PATH is minimal."""
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


def _severity(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "moderate":
        return "medium"
    if normalized in {"critical", "high", "medium", "low"}:
        return normalized
    return "unknown"


def scan_homebrew(config: Config) -> dict[str, Any]:
    """Return active Homebrew vulnerabilities reported by `brew vulns`."""
    if sys.platform != "darwin":
        return {"status": "unsupported", "reason": "Homebrew runs on macOS only", "findings": []}
    brew = find_brew()
    if brew is None:
        return {"status": "unavailable", "reason": "Homebrew is not installed", "findings": []}
    code, stdout, stderr = run_command(
        [brew, "vulns", "--json"],
        config,
        environment={"HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_COLOR": "1"},
        timeout_seconds=config.security_scan_timeout_seconds,
    )
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as error:
        reason = (stderr or stdout or "unknown error")[-500:]
        return {
            "status": "failed",
            "reason": f"brew vulns returned invalid JSON: {error}: {reason}",
            "findings": [],
        }
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        reason = (stderr or stdout or "unknown error")[-500:]
        return {
            "status": "failed",
            "reason": f"brew vulns returned an unexpected payload: {reason}",
            "findings": [],
        }
    findings: list[dict[str, Any]] = []
    formulas = 0
    for entry in data.get("findings", []):
        if not isinstance(entry, dict):
            continue
        formula = entry.get("formula")
        version = entry.get("version")
        if not isinstance(formula, str) or not isinstance(version, str):
            continue
        formulas += 1
        for vulnerability in entry.get("vulnerabilities", []):
            if not isinstance(vulnerability, dict):
                continue
            vuln_id = vulnerability.get("id")
            if not isinstance(vuln_id, str) or not vuln_id:
                continue
            aliases = vulnerability.get("aliases")
            fixed = vulnerability.get("fixed_versions")
            findings.append(
                {
                    "source": "homebrew",
                    "id": vuln_id,
                    "aliases": sorted(str(alias) for alias in aliases) if isinstance(aliases, list) else [],
                    "severity": _severity(vulnerability.get("severity")),
                    "summary": str(vulnerability.get("summary") or ""),
                    "packages": [formula],
                    "version": version,
                    "fixed_versions": sorted(str(item) for item in fixed) if isinstance(fixed, list) else [],
                }
            )
    warnings: list[str] = []
    skipped = data.get("skipped_formulae")
    if isinstance(skipped, list) and skipped:
        warnings.append(f"{len(skipped)} Homebrew formulae were not scanned (no SBOM)")
    unscanned = data.get("unscanned_formulae")
    if isinstance(unscanned, dict) and unscanned:
        warnings.append(f"{len(unscanned)} Homebrew formulae could not be scanned")
    outdated = data.get("outdated_without_sbom")
    if isinstance(outdated, list) and outdated:
        warnings.append(f"{len(outdated)} outdated formulae have no SBOM")
    if data.get("complete") is False and not warnings:
        warnings.append("brew vulns did not scan every installed formula")
    status = "findings" if findings else "ok"
    if warnings:
        status = "partial"
    checked = data.get("checked")
    return {
        "status": status,
        "formulae": checked if isinstance(checked, int) else formulas,
        "reason": "; ".join(warnings) if warnings else None,
        "warnings": warnings,
        "findings": findings,
    }
