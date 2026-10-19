"""Project lockfile discovery and OSV vulnerability queries."""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from config import Config
from state import write_json


OSV_API = "https://api.osv.dev"
BATCH_LIMIT = 500
MAX_LOCKFILE_BYTES = 20 * 1024 * 1024
SKIP_DIRECTORIES = frozenset(
    {
        ".cache",
        ".git",
        ".next",
        ".tox",
        ".venv",
        ".yarn",
        "DerivedData",
        "Library",
        "build",
        "dist",
        "node_modules",
        "Pods",
        "target",
        "tmp",
        "vendor",
        "venv",
    }
)
PYPI_NAME = re.compile(r"[-_.]+")


@dataclass(frozen=True)
class Dependency:
    ecosystem: str
    name: str
    version: str
    source: str


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_LOCKFILE_BYTES:
        raise ValueError(f"lockfile is larger than 20 MiB: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize_pypi(name: str) -> str:
    return PYPI_NAME.sub("-", name).lower()


def _package_lock(path: Path) -> list[Dependency]:
    data = json.loads(_read_text(path))
    dependencies: list[Dependency] = []
    packages = data.get("packages")
    if isinstance(packages, dict):
        for key, value in packages.items():
            if not isinstance(value, dict) or value.get("link"):
                continue
            version = value.get("version")
            if not isinstance(version, str) or not version:
                continue
            if "node_modules/" not in key:
                continue
            name = key.rsplit("node_modules/", 1)[-1]
            name = value.get("name") if isinstance(value.get("name"), str) else name
            if name and not name.startswith("."):
                dependencies.append(Dependency("npm", name, version, str(path)))
        return dependencies

    def walk(entries: Any) -> None:
        if not isinstance(entries, dict):
            return
        for name, value in entries.items():
            if not isinstance(value, dict):
                continue
            version = value.get("version")
            if isinstance(version, str) and version:
                dependencies.append(Dependency("npm", str(name), version, str(path)))
            walk(value.get("dependencies"))

    walk(data.get("dependencies"))
    return dependencies


def _pnpm_key(key: str) -> tuple[str, str] | None:
    key = key.strip().strip("'\"")
    if not key or key.startswith(("file:", "link:", "workspace:")):
        return None
    key = key.split("(", 1)[0]
    if key.startswith("/"):
        key = key[1:]
    if key.startswith("@"):
        slash = key.find("/", 1)
        if slash == -1:
            return None
        at = key.rfind("@")
        if at > 0:
            return key[:at], key[at + 1 :]
        name, _, version = key.rpartition("/")
        return (name, version) if name and version else None
    if "/" in key and "@" not in key:
        name, _, version = key.rpartition("/")
        return (name, version) if name and version else None
    at = key.rfind("@")
    if at <= 0:
        return None
    return key[:at], key[at + 1 :]


def _pnpm_lock(path: Path) -> list[Dependency]:
    dependencies: list[Dependency] = []
    in_packages = False
    for line in _read_text(path).splitlines():
        if line.strip() == "packages:":
            in_packages = True
            continue
        if not in_packages:
            continue
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  (?! )'?([^']+)'?:\s*$", line)
        if match is None:
            continue
        parsed = _pnpm_key(match.group(1))
        if parsed is None:
            continue
        name, version = parsed
        dependencies.append(Dependency("npm", name, version, str(path)))
    return dependencies


def _toml_packages(path: Path, ecosystem: str) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for block in _read_text(path).split("[[package]]")[1:]:
        name_match = re.search(r'^name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        version_match = re.search(r'^version\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if name_match is None or version_match is None:
            continue
        source_match = re.search(r'^source\s*=\s*(.+)$', block, re.MULTILINE)
        source = source_match.group(1) if source_match else ""
        if (
            "editable" in source
            or "directory" in source
            or "git+" in source
            or 'type = "git"' in source
            or 'type = "directory"' in source
            or "git = " in source
            or "url = " in source
        ):
            continue
        dependencies.append(
            Dependency(ecosystem, name_match.group(1), version_match.group(1), str(path))
        )
    return dependencies


def _requirements(path: Path) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for raw_line in _read_text(path).splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http:", "https:", "file:")):
            continue
        match = re.match(
            r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*==\s*([^\s;]+)",
            line,
        )
        if match is None:
            continue
        dependencies.append(
            Dependency(
                "PyPI",
                _normalize_pypi(match.group(1)),
                match.group(2),
                str(path),
            )
        )
    return dependencies


def _cargo_lock(path: Path) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for block in _read_text(path).split("[[package]]")[1:]:
        name_match = re.search(r'^name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        version_match = re.search(r'^version\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if name_match is None or version_match is None:
            continue
        if not re.search(r'^source\s*=', block, re.MULTILINE):
            continue
        dependencies.append(
            Dependency("crates.io", name_match.group(1), version_match.group(1), str(path))
        )
    return dependencies


def _go_sum(path: Path) -> list[Dependency]:
    dependencies: dict[tuple[str, str], Dependency] = {}
    for line in _read_text(path).splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        module, version = fields[0], fields[1].removesuffix("/go.mod")
        if not module or not version:
            continue
        dependencies[(module, version)] = Dependency("Go", module, version, str(path))
    return list(dependencies.values())


def _gemfile_lock(path: Path) -> list[Dependency]:
    dependencies: list[Dependency] = []
    in_specs = False
    section = ""
    for line in _read_text(path).splitlines():
        stripped = line.strip()
        if stripped in {"GEM", "PATH", "GIT", "PLUGIN SOURCE"}:
            section = stripped
            in_specs = False
            continue
        if stripped == "specs:":
            in_specs = section == "GEM"
            continue
        if in_specs and line and not line.startswith(" "):
            in_specs = False
        if not in_specs:
            continue
        match = re.match(r"^    ([A-Za-z0-9_.-]+) \(([^)]+)\)$", line)
        if match is None:
            continue
        version = re.sub(
            r"-(?:x86_64|arm64|aarch64|universal|java|ruby|mingw32|mswin32|x64).*$",
            "",
            match.group(2),
        )
        dependencies.append(Dependency("RubyGems", match.group(1), version, str(path)))
    return dependencies


def _composer_lock(path: Path) -> list[Dependency]:
    data = json.loads(_read_text(path))
    dependencies: list[Dependency] = []
    for section in ("packages", "packages-dev"):
        entries = data.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            version = entry.get("version")
            if isinstance(name, str) and isinstance(version, str):
                dependencies.append(
                    Dependency("Packagist", name, version.lstrip("v"), str(path))
                )
    return dependencies


LOCKFILE_PARSERS: dict[str, Callable[[Path], list[Dependency]]] = {
    "package-lock.json": _package_lock,
    "pnpm-lock.yaml": _pnpm_lock,
    "yarn.lock": lambda path: [],
    "requirements.txt": _requirements,
    "uv.lock": lambda path: _toml_packages(path, "PyPI"),
    "poetry.lock": lambda path: _toml_packages(path, "PyPI"),
    "Pipfile.lock": lambda path: [],
    "Cargo.lock": _cargo_lock,
    "go.sum": _go_sum,
    "Gemfile.lock": _gemfile_lock,
    "composer.lock": _composer_lock,
}


def discover_lockfiles(root: Path, max_depth: int) -> list[Path]:
    """Find known lockfiles under root, pruning dependency and build trees."""
    if not root.is_dir():
        return []
    root = root.resolve()
    found: list[Path] = []
    for directory, dirnames, filenames in os.walk(root):
        current = Path(directory)
        depth = len(current.relative_to(root).parts)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in SKIP_DIRECTORIES and not name.startswith(".git")
        )
        if depth >= max_depth:
            dirnames[:] = []
        for filename in sorted(filenames):
            if filename in LOCKFILE_PARSERS:
                found.append(current / filename)
    return found


def collect_dependencies(
    config: Config,
) -> tuple[dict[tuple[str, str, str], Dependency], int, list[str]]:
    """Return unique dependencies, lockfile count, and parse warnings."""
    root = config.project_root
    lockfiles = discover_lockfiles(root, config.security_project_max_depth)
    unique: dict[tuple[str, str, str], Dependency] = {}
    warnings: list[str] = []
    if not root.is_dir():
        return unique, 0, [f"project root does not exist: {root}"]
    for path in lockfiles:
        parser = LOCKFILE_PARSERS[path.name]
        if path.name in {"yarn.lock", "Pipfile.lock"}:
            warnings.append(f"{path}: parser not implemented; skipped")
            continue
        try:
            dependencies = parser(path)
        except (OSError, ValueError, json.JSONDecodeError, UnicodeError) as error:
            warnings.append(f"{path}: {error}")
            continue
        for dependency in dependencies:
            key = (dependency.ecosystem, dependency.name, dependency.version)
            existing = unique.get(key)
            if existing is None:
                unique[key] = Dependency(
                    dependency.ecosystem,
                    dependency.name,
                    dependency.version,
                    str(path),
                )
            else:
                sources = set(existing.source.split("\n"))
                sources.add(str(path))
                unique[key] = Dependency(
                    existing.ecosystem,
                    existing.name,
                    existing.version,
                    "\n".join(sorted(sources)),
                )
    return unique, len(lockfiles), warnings


def _request_json(url: str, payload: dict[str, Any] | None, timeout: int) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "linux-cleanup-service/1",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"OSV request failed for {url}: {error}") from error


def _load_cache(config: Config, now: datetime) -> dict[str, dict[str, Any]]:
    path = config.state_dir / "osv-cache.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    cutoff = now - timedelta(days=7)
    cache: dict[str, dict[str, Any]] = {}
    for key, entry in value.items():
        if not isinstance(entry, dict):
            continue
        try:
            fetched = datetime.fromisoformat(str(entry.get("fetched_at", "")))
        except ValueError:
            continue
        if fetched.tzinfo is None:
            fetched = fetched.astimezone()
        if fetched >= cutoff:
            cache[str(key)] = entry
    return cache


def _cvss3_score(vector: str) -> float | None:
    """Calculate a CVSS v3 base score from its vector string."""
    if not vector.startswith(("CVSS:3.0/", "CVSS:3.1/")):
        return None
    metrics: dict[str, str] = {}
    for part in vector.split("/")[1:]:
        key, _, value = part.partition(":")
        if key and value:
            metrics[key] = value
    try:
        scope = metrics["S"]
        if scope not in {"U", "C"}:
            return None
        pr_weights = (
            {"N": 0.85, "L": 0.68, "H": 0.5}
            if scope == "C"
            else {"N": 0.85, "L": 0.62, "H": 0.27}
        )
        exploitability = (
            8.22
            * {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[metrics["AV"]]
            * {"L": 0.77, "H": 0.44}[metrics["AC"]]
            * pr_weights[metrics["PR"]]
            * {"N": 0.85, "R": 0.62}[metrics["UI"]]
        )
        impact_component = 1 - (
            (1 - {"H": 0.56, "L": 0.22, "N": 0.0}[metrics["C"]])
            * (1 - {"H": 0.56, "L": 0.22, "N": 0.0}[metrics["I"]])
            * (1 - {"H": 0.56, "L": 0.22, "N": 0.0}[metrics["A"]])
        )
        if scope == "C":
            impact = 7.52 * (impact_component - 0.029) - 3.25 * (
                impact_component - 0.02
            ) ** 15
        else:
            impact = 6.42 * impact_component
    except (KeyError, ValueError):
        return None
    if impact <= 0:
        return 0.0
    if scope == "C":
        score = min(1.08 * (impact + exploitability), 10.0)
    else:
        score = min(impact + exploitability, 10.0)
    return math.ceil(score * 10 - 1e-9) / 10


def _severity_from_score(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"


def _severity_info(record: dict[str, Any]) -> tuple[str, float | None, str | None]:
    label: str | None = None
    specific = record.get("database_specific")
    if isinstance(specific, dict):
        value = str(specific.get("severity", "")).strip().lower()
        if value in {"critical", "high", "moderate", "medium", "low"}:
            label = "medium" if value == "moderate" else value
    score: float | None = None
    vector: str | None = None
    for item in record.get("severity", []):
        if not isinstance(item, dict):
            continue
        candidate = item.get("score")
        if not isinstance(candidate, str):
            continue
        score = _cvss3_score(candidate)
        if score is not None:
            vector = candidate
            break
    if label is not None:
        return label, score, vector
    if score is not None:
        return _severity_from_score(score) or "unknown", score, vector
    return "unknown", None, None


def _summary(record: dict[str, Any]) -> str:
    summary = record.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    details = record.get("details")
    if isinstance(details, str):
        return " ".join(details.split())[:200]
    return ""


def _details(
    config: Config,
    vuln_ids: set[str],
    cache: dict[str, dict[str, Any]],
    now: datetime,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    warnings: list[str] = []
    expiry = now - timedelta(hours=config.security_osv_cache_hours)
    records: dict[str, dict[str, Any]] = {}
    for vuln_id in sorted(vuln_ids):
        cached = cache.get(vuln_id)
        if cached is not None:
            try:
                fetched = datetime.fromisoformat(str(cached.get("fetched_at", "")))
            except ValueError:
                fetched = now - timedelta(days=1)
            if fetched.tzinfo is None:
                fetched = fetched.astimezone()
            if fetched >= expiry and isinstance(cached.get("record"), dict):
                records[vuln_id] = cached["record"]
                continue
        try:
            record = _request_json(
                f"{OSV_API}/v1/vulns/{urllib.parse.quote(vuln_id)}",
                None,
                config.security_network_timeout_seconds,
            )
        except RuntimeError as error:
            warnings.append(str(error))
            if isinstance(cached, dict) and isinstance(cached.get("record"), dict):
                records[vuln_id] = cached["record"]
            else:
                records[vuln_id] = {"id": vuln_id, "summary": "", "database_specific": {}}
            continue
        if not isinstance(record, dict):
            warnings.append(f"OSV returned an invalid record for {vuln_id}")
            continue
        records[vuln_id] = record
        cache[vuln_id] = {
            "fetched_at": now.isoformat(timespec="seconds"),
            "record": record,
        }
    return records, warnings


SEVERITY_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _merge_osv_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge records for the same advisory and package version."""
    groups: list[dict[str, Any]] = []
    for finding in findings:
        ids = {finding["id"], *finding.get("aliases", [])}
        target: dict[str, Any] | None = None
        for group in groups:
            if (
                group["ecosystem"] == finding["ecosystem"]
                and group["packages"] == finding["packages"]
                and group["version"] == finding["version"]
                and group["_ids"] & ids
            ):
                target = group
                break
        if target is None:
            group = dict(finding)
            group["_ids"] = ids
            groups.append(group)
            continue
        target["_ids"] |= ids
        target["sources"] = sorted(
            set(target.get("sources", [])) | set(finding.get("sources", []))
        )
        if SEVERITY_RANK.get(finding.get("severity"), 0) > SEVERITY_RANK.get(
            target.get("severity"), 0
        ):
            target["severity"] = finding["severity"]
        if finding.get("cvss") and (
            not target.get("cvss") or finding["cvss"] > target["cvss"]
        ):
            target["cvss"] = finding["cvss"]
            target["cvss_vector"] = finding.get("cvss_vector")
        if len(str(finding.get("summary", ""))) > len(
            str(target.get("summary", ""))
        ):
            target["summary"] = finding["summary"]
    for group in groups:
        ids = group.pop("_ids")
        preferred = next(
            (item for item in sorted(ids) if item.startswith("CVE-")),
            group["id"],
        )
        group["id"] = preferred
        group["aliases"] = sorted(ids - {preferred})
    return groups


def query_osv(
    config: Config,
    dependencies: dict[tuple[str, str, str], Dependency],
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Query OSV for every dependency and return findings plus warnings."""
    if not dependencies:
        return [], []
    items = list(dependencies.values())
    matches: list[tuple[Dependency, str]] = []
    warnings: list[str] = []
    for start in range(0, len(items), BATCH_LIMIT):
        chunk = items[start : start + BATCH_LIMIT]
        payload = {
            "queries": [
                {
                    "package": {"name": item.name, "ecosystem": item.ecosystem},
                    "version": item.version,
                }
                for item in chunk
            ]
        }
        response = _request_json(
            f"{OSV_API}/v1/querybatch",
            payload,
            config.security_network_timeout_seconds,
        )
        results = response.get("results") if isinstance(response, dict) else None
        if not isinstance(results, list) or len(results) != len(chunk):
            raise RuntimeError("OSV querybatch returned an unexpected result count")
        for item, result in zip(chunk, results):
            if not isinstance(result, dict):
                continue
            for vuln in result.get("vulns", []):
                if isinstance(vuln, dict) and isinstance(vuln.get("id"), str):
                    matches.append((item, vuln["id"]))
    cache = _load_cache(config, now)
    records, fetch_warnings = _details(
        config, {vuln_id for _, vuln_id in matches}, cache, now
    )
    warnings.extend(fetch_warnings)
    findings: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for dependency, vuln_id in matches:
        record = records.get(vuln_id)
        if record is None:
            continue
        packages = [dependency.name]
        key = (vuln_id, dependency.ecosystem, dependency.name, dependency.version)
        sources = dependency.source.split("\n")
        if key in findings:
            findings[key]["sources"] = sorted(
                set(findings[key]["sources"]) | set(sources)
            )
            continue
        aliases = record.get("aliases")
        severity, cvss, cvss_vector = _severity_info(record)
        findings[key] = {
            "source": "osv",
            "id": vuln_id,
            "aliases": sorted(str(alias) for alias in aliases) if isinstance(aliases, list) else [],
            "severity": severity,
            "cvss": cvss,
            "cvss_vector": cvss_vector,
            "summary": _summary(record),
            "ecosystem": dependency.ecosystem,
            "packages": packages,
            "version": dependency.version,
            "sources": sorted(sources),
        }
    try:
        write_json(config.state_dir / "osv-cache.json", cache)
    except OSError as error:
        warnings.append(f"could not write OSV cache: {error}")
    return _merge_osv_findings(list(findings.values())), warnings


def scan_projects(config: Config, now: datetime) -> dict[str, Any]:
    """Scan configured project lockfiles through OSV."""
    if not config.security_scan_projects:
        return {"status": "disabled", "findings": [], "warnings": []}
    dependencies, lockfiles, warnings = collect_dependencies(config)
    if lockfiles == 0 and not config.project_root.is_dir():
        return {
            "status": "unavailable",
            "reason": f"project root does not exist: {config.project_root}",
            "lockfiles": 0,
            "dependencies": 0,
            "findings": [],
            "warnings": warnings,
        }
    try:
        findings, osv_warnings = query_osv(config, dependencies, now)
    except (OSError, RuntimeError) as error:
        return {
            "status": "failed",
            "reason": str(error),
            "lockfiles": lockfiles,
            "dependencies": len(dependencies),
            "findings": [],
            "warnings": warnings,
        }
    warnings.extend(osv_warnings)
    status = "partial" if warnings else ("findings" if findings else "ok")
    return {
        "status": status,
        "lockfiles": lockfiles,
        "dependencies": len(dependencies),
        "reason": "; ".join(warnings) if warnings else None,
        "findings": findings,
        "warnings": warnings,
    }
