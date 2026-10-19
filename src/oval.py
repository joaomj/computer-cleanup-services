"""Ubuntu OVAL feed handling for the daily security check."""

from __future__ import annotations

import bz2
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from common import run_command
from config import Config


OVAL_BASE = "https://security-metadata.canonical.com/oval"
OS_RELEASE = Path("/etc/os-release")
PACKAGE_IN_COMMENT = re.compile(r"(?:kernel|package) '([^']+)'", re.IGNORECASE)
SOURCE_PACKAGE_IN_COMMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9+.-]*) source package\b"
)
SEVERITY_SCORE = (
    (9.0, "critical"),
    (7.0, "high"),
    (4.0, "medium"),
    (0.1, "low"),
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def ubuntu_release() -> tuple[str, str] | None:
    """Return the Ubuntu ID and codename, or None for another distribution."""
    try:
        lines = OS_RELEASE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    if values.get("ID") != "ubuntu":
        return None
    codename = values.get("VERSION_CODENAME", "")
    if re.fullmatch(r"[a-z]+", codename) is None:
        return None
    return values.get("ID", "ubuntu"), codename


def severity_from_cvss(score: float | None) -> str | None:
    """Map a CVSS base score to a compact severity label."""
    if score is None:
        return None
    for threshold, label in SEVERITY_SCORE:
        if score >= threshold:
            return label
    return "unknown"


def _comment_package(comment: str) -> str | None:
    """Return the package named by a Canonical OVAL criterion comment."""
    match = PACKAGE_IN_COMMENT.search(comment)
    if match:
        return match.group(1)
    match = SOURCE_PACKAGE_IN_COMMENT.match(comment.strip())
    if match:
        return match.group(1)
    return None


def _installed_source_packages(config: Config) -> set[str]:
    """Return installed binary and source package names for filtering."""
    dpkg_query = shutil.which("dpkg-query")
    if dpkg_query is None:
        return set()
    code, stdout, _ = run_command(
        [dpkg_query, "-W", "-f=${Package}\t${source:Package}\n"],
        config,
        timeout_seconds=30,
        quiet=True,
    )
    if code != 0:
        return set()
    installed: set[str] = set()
    for line in stdout.splitlines():
        name, _, source = line.partition("\t")
        if name.strip():
            installed.add(name.strip())
        if source.strip():
            installed.add(source.strip())
    return installed


def _priority_severity(priority: str | None) -> str | None:
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
        "negligible": "low",
    }
    return mapping.get((priority or "").strip().lower())


def _download_feed(url: str, target: Path, timeout: int) -> str | None:
    """Download and decompress an OVAL feed, returning an error or None."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    compressed_name = ""
    xml_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".oval-", suffix=".bz2", delete=False
        ) as compressed:
            compressed_name = compressed.name
            request = urllib.request.Request(
                url, headers={"User-Agent": "linux-cleanup-service/1"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 200 * 1024 * 1024:
                        return "OVAL download exceeded 200 MiB"
                    compressed.write(chunk)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".oval-", suffix=".xml", delete=False
        ) as xml_file:
            xml_name = xml_file.name
            with bz2.BZ2File(compressed_name, "rb") as source:
                shutil.copyfileobj(source, xml_file, length=1024 * 1024)
        with open(xml_name, "rb") as handle:
            head = handle.read(4096)
        if b"<oval_definitions" not in head:
            return "OVAL feed did not contain an oval_definitions document"
        os.replace(xml_name, target)
        xml_name = ""
        return None
    except (OSError, EOFError, ValueError, urllib.error.URLError) as error:
        return str(error)
    finally:
        for name in (compressed_name, xml_name):
            if name:
                try:
                    Path(name).unlink()
                except FileNotFoundError:
                    pass


def _parse_definitions(path: Path) -> dict[str, dict[str, Any]]:
    """Map OVAL definition IDs to CVE metadata without loading the full tree."""
    definitions: dict[str, dict[str, Any]] = {}
    try:
        with path.open("rb") as handle:
            events = ET.iterparse(handle, events=("end",))
            for _, element in events:
                if _local_name(element.tag) != "definition":
                    continue
                if element.get("class") != "vulnerability":
                    element.clear()
                    continue
                title = ""
                cve = ""
                priority = ""
                cvss_text = ""
                cvss_severity = ""
                packages: set[str] = set()
                for child in element.iter():
                    name = _local_name(child.tag)
                    if name == "title" and child.text:
                        title = child.text.strip()
                    elif name == "cve":
                        cve = (child.text or child.get("ref_id") or "").strip()
                        priority = (
                            child.get("priority", "")
                            or child.get("impact", "")
                            or priority
                        )
                        cvss_text = (
                            child.get("cvss_score", "")
                            or child.get("cvss3", "")
                            or child.get("cvss2", "")
                            or cvss_text
                        )
                        cvss_severity = child.get("cvss_severity", "") or cvss_severity
                    elif name == "advisory":
                        priority = priority or child.get("priority", "")
                        cvss_text = cvss_text or child.get("cvss_score", "")
                        cvss_severity = cvss_severity or child.get("cvss_severity", "")
                    elif name == "severity" and child.text and not cvss_severity:
                        cvss_severity = child.text.strip()
                    elif name == "criterion":
                        package = _comment_package(child.get("comment", ""))
                        if package:
                            packages.add(package)
                try:
                    cvss = float(cvss_text) if cvss_text else None
                except ValueError:
                    cvss = None
                severity = (
                    cvss_severity.lower()
                    if cvss_severity.lower() in {"critical", "high", "medium", "low"}
                    else severity_from_cvss(cvss) or _priority_severity(priority) or "unknown"
                )
                if cve:
                    definitions[element.get("id", "")] = {
                        "id": cve,
                        "title": title or cve,
                        "severity": severity,
                        "cvss": cvss,
                        "priority": priority or None,
                        "packages": sorted(packages) or ["unknown"],
                    }
                element.clear()
    except (OSError, ET.ParseError) as error:
        raise RuntimeError(f"could not parse OVAL definitions in {path}: {error}") from error
    return definitions


def _parse_results(path: Path) -> dict[str, set[str]]:
    """Return true vulnerability definitions and their matched packages."""
    vulnerable: dict[str, set[str]] = {}
    packages: set[str] = set()
    result_value = ""
    try:
        with path.open("rb") as handle:
            events = ET.iterparse(handle, events=("end",))
            for _, element in events:
                name = _local_name(element.tag)
                if name == "criterion":
                    if element.get("result") == "true":
                        package = _comment_package(element.get("comment", ""))
                        if package:
                            packages.add(package)
                    continue
                if name == "result":
                    result_value = (element.text or "").strip()
                    continue
                if name != "definition":
                    continue
                if element.get("result") == "true" or result_value == "true":
                    definition_id = element.get("definition_id") or element.get("id", "")
                    if definition_id:
                        vulnerable[definition_id] = set(packages)
                packages.clear()
                result_value = ""
                element.clear()
    except (OSError, ET.ParseError) as error:
        raise RuntimeError(f"could not parse OVAL results in {path}: {error}") from error
    return vulnerable


def scan_ubuntu(config: Config) -> dict[str, Any]:
    """Evaluate the Ubuntu OVAL feed and return affected packages."""
    if sys.platform != "linux":
        return {"status": "unsupported", "reason": "Ubuntu OVAL is Linux-only", "findings": []}
    release = ubuntu_release()
    if release is None:
        return {
            "status": "unsupported",
            "reason": "OS CVE scanning currently supports Ubuntu only",
            "findings": [],
        }
    _, codename = release
    oscap = shutil.which("oscap")
    if oscap is None:
        return {
            "status": "unavailable",
            "reason": "install openscap-scanner to enable OS CVE scanning",
            "findings": [],
        }
    oval_dir = config.state_dir / "oval"
    oval_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    xml_path = oval_dir / f"com.ubuntu.{codename}.cve.oval.xml"
    results_path = oval_dir / f"com.ubuntu.{codename}.results.xml"
    url = f"{OVAL_BASE}/com.ubuntu.{codename}.cve.oval.xml.bz2"
    warnings: list[str] = []
    error = _download_feed(url, xml_path, config.security_network_timeout_seconds)
    if error is not None:
        if xml_path.is_file() and xml_path.stat().st_size > 0:
            warnings.append(f"OVAL download failed; using the cached feed: {error}")
        else:
            return {
                "status": "failed",
                "reason": f"OVAL download failed: {error}",
                "findings": [],
                "warnings": warnings,
            }
    code, _, stderr = run_command(
        [oscap, "oval", "eval", "--results", str(results_path), str(xml_path)],
        config,
        timeout_seconds=config.security_scan_timeout_seconds,
        quiet=True,
    )
    if code != 0:
        return {
            "status": "failed",
            "reason": f"OpenSCAP evaluation failed: {stderr[-500:] or 'unknown error'}",
            "findings": [],
            "warnings": warnings,
        }
    definitions = _parse_definitions(xml_path)
    vulnerable = _parse_results(results_path)
    merged: dict[str, dict[str, Any]] = {}
    for definition_id, matched in sorted(vulnerable.items()):
        meta = definitions.get(definition_id)
        if meta is None:
            continue
        affected = sorted(matched) or list(meta["packages"])
        finding = merged.get(meta["id"])
        if finding is None:
            merged[meta["id"]] = {
                "source": "ubuntu-oval",
                "release": codename,
                "id": meta["id"],
                "severity": meta["severity"],
                "cvss": meta["cvss"],
                "priority": meta["priority"],
                "summary": meta["title"],
                "packages": affected,
            }
            continue
        finding["packages"] = sorted(set(finding["packages"]) | set(affected))
    findings = list(merged.values())
    installed = _installed_source_packages(config)
    if installed:
        for finding in findings:
            matched = [
                package
                for package in finding["packages"]
                if package in installed
            ]
            if matched:
                finding["packages"] = matched
    status = "partial" if warnings else ("findings" if findings else "ok")
    return {
        "status": status,
        "release": codename,
        "definitions": len(definitions),
        "evaluated": len(vulnerable),
        "reason": "; ".join(warnings) if warnings else None,
        "findings": findings,
        "warnings": warnings,
    }
