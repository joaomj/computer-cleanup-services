#!/usr/bin/env python3
"""Download and install verified macOS Node.js and Bun runtime releases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from config import Config


BUN_KEY_FINGERPRINT = "F3DCC08A8572C0749B3E18888EAB4D40A7B22B59"  # gitleaks:allow -- public release-signing fingerprint, not a credential
BUN_KEY_URL = f"https://keys.openpgp.org/vks/v1/by-fingerprint/{BUN_KEY_FINGERPRINT}"
NODE_RELEASE_INDEX_URL = "https://nodejs.org/dist/index.tab"
NODE_RELEASE_KEYRING_URL = (
    "https://github.com/nodejs/release-keys/raw/HEAD/gpg/pubring.kbx"
)
BUN_RELEASES_URL = "https://api.github.com/repos/oven-sh/bun/releases?per_page=100"
NODE_ARCHIVE_TEMPLATE = "node-v{version}-darwin-arm64.tar.xz"
BUN_ARCHIVE_NAME = "bun-darwin-aarch64.zip"
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class RuntimeUpdateError(RuntimeError):
    """Raised when a runtime cannot be safely inspected or installed."""


@dataclass(frozen=True, order=True)
class Version:
    """A strict three-part release version."""

    major: int
    minor: int
    patch: int

    @property
    def text(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class Release:
    """A verified-candidate release discovered from an official index."""

    version: Version
    published_at: datetime
    archive_name: str
    archive_url: str
    manifest_url: str
    kind: str
    bundled_npm: str | None = None


def parse_version(value: str) -> Version:
    """Parse only stable semantic versions with three numeric components."""
    match = VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise RuntimeUpdateError(f"unsupported runtime version: {value}")
    return Version(*(int(component) for component in match.groups()))


def parse_release_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp and normalize it to UTC."""
    normalized = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        return datetime.combine(
            date.fromisoformat(normalized), datetime.min.time(), tzinfo=timezone.utc
        )
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeUpdateError(f"invalid release timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise RuntimeUpdateError(f"release timestamp has no timezone: {value}")
    return parsed.astimezone(timezone.utc)


def release_is_eligible(release: Release, now: datetime, minimum_age_days: int) -> bool:
    """Return whether a release is at least the required age."""
    current = now.astimezone(timezone.utc)
    return current - release.published_at >= timedelta(days=minimum_age_days)


def _request_bytes(url: str, timeout: int, maximum: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeUpdateError(f"refusing non-HTTPS download: {url}")
    request = Request(
        url,
        headers={
            "Accept": "application/json, application/octet-stream",
            "User-Agent": "macos-cleanup-service/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > maximum:
                raise RuntimeUpdateError(f"download exceeds limit: {url}")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise RuntimeUpdateError(f"download exceeds limit: {url}")
                chunks.append(chunk)
    except (HTTPError, URLError, OSError, ValueError) as error:
        raise RuntimeUpdateError(f"download failed for {url}: {error}") from error
    return b"".join(chunks)


def _download_file(url: str, path: Path, timeout: int) -> None:
    content = _request_bytes(url, timeout, MAX_DOWNLOAD_BYTES)
    path.write_bytes(content)


def discover_node_release(config: Config) -> Release:
    """Find the newest stable Node release in the configured major line."""
    raw = _request_bytes(
        NODE_RELEASE_INDEX_URL,
        config.runtime_network_timeout_seconds,
        MAX_METADATA_BYTES,
    )
    try:
        rows = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeUpdateError("Node release index is not UTF-8") from error
    if not rows:
        raise RuntimeUpdateError("Node release index is empty")
    headers = rows[0].split("\t")
    try:
        columns = {name: index for index, name in enumerate(headers)}
        version_column = columns["version"]
        date_column = columns["date"]
        files_column = columns["files"]
        npm_column = columns["npm"]
        lts_column = columns["lts"]
    except KeyError as error:
        raise RuntimeUpdateError(
            "Node release index is missing required columns"
        ) from error

    candidates: list[Release] = []
    for row in rows[1:]:
        fields = row.split("\t")
        if len(fields) <= max(columns.values()):
            continue
        version = parse_version(fields[version_column])
        if version.major != config.runtime_node_major or fields[lts_column] == "-":
            continue
        if "osx-arm64-tar" not in fields[files_column].split(","):
            continue
        archive_name = NODE_ARCHIVE_TEMPLATE.format(version=version.text)
        base_url = f"https://nodejs.org/download/release/v{version.text}"
        candidates.append(
            Release(
                version=version,
                published_at=parse_release_timestamp(fields[date_column]),
                archive_name=archive_name,
                archive_url=f"{base_url}/{archive_name}",
                manifest_url=f"{base_url}/SHASUMS256.txt.asc",
                kind="node",
                bundled_npm=fields[npm_column],
            )
        )
    if not candidates:
        raise RuntimeUpdateError(
            f"no stable Node {config.runtime_node_major}.x release found"
        )
    return max(candidates, key=lambda release: release.version)


def discover_bun_release(config: Config) -> Release:
    """Find the newest stable Bun release with the required macOS asset."""
    raw = _request_bytes(
        BUN_RELEASES_URL, config.runtime_network_timeout_seconds, MAX_METADATA_BYTES
    )
    try:
        releases = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeUpdateError("Bun release metadata is not valid JSON") from error
    if not isinstance(releases, list):
        raise RuntimeUpdateError("Bun release metadata is not a list")

    candidates: list[Release] = []
    for item in releases:
        if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
            continue
        tag = item.get("tag_name")
        published_at = item.get("published_at")
        assets = item.get("assets")
        if (
            not isinstance(tag, str)
            or not tag.startswith("bun-v")
            or not isinstance(published_at, str)
        ):
            continue
        if not isinstance(assets, list) or not any(
            isinstance(asset, dict) and asset.get("name") == BUN_ARCHIVE_NAME
            for asset in assets
        ):
            continue
        try:
            version = parse_version(tag.removeprefix("bun-"))
        except RuntimeUpdateError:
            continue
        base_url = f"https://github.com/oven-sh/bun/releases/download/{tag}"
        candidates.append(
            Release(
                version=version,
                published_at=parse_release_timestamp(published_at),
                archive_name=BUN_ARCHIVE_NAME,
                archive_url=f"{base_url}/{BUN_ARCHIVE_NAME}",
                manifest_url=f"{base_url}/SHASUMS256.txt.asc",
                kind="bun",
            )
        )
    if not candidates:
        raise RuntimeUpdateError(
            "no stable Bun release with the required macOS asset found"
        )
    return max(candidates, key=lambda release: release.version)


def _find_tool(config: Config, name: str, configured: Path | None) -> Path:
    candidates: list[Path] = []
    if configured is not None:
        candidates.append(configured)
    resolved = shutil.which(name)
    if resolved:
        candidates.append(Path(resolved))
    candidates.extend(
        Path.home() / relative
        for relative in (f"homebrew/bin/{name}", f".local/bin/{name}")
    )
    candidates.extend(
        Path(prefix) / name
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeUpdateError(f"required verification tool is unavailable: {name}")


def _verify_signature(
    manifest: Path,
    keyring: Path,
    config: Config,
    expected_fingerprint: str | None = None,
) -> None:
    gpgv = _find_tool(config, "gpgv", config.runtime_gpgv_path)
    result = subprocess.run(
        [str(gpgv), "--status-fd", "1", "--keyring", str(keyring), str(manifest)],
        check=False,
        capture_output=True,
        text=True,
        timeout=config.command_timeout_seconds,
    )
    if result.returncode != 0:
        detail = (
            result.stderr.strip() or result.stdout.strip() or "unknown signature error"
        )
        raise RuntimeUpdateError(f"signature verification failed: {detail}")
    if expected_fingerprint is not None:
        fingerprints = {
            line.split()[2].upper()
            for line in result.stdout.splitlines()
            if line.startswith("[GNUPG:] VALIDSIG ") and len(line.split()) > 2
        }
        if expected_fingerprint.upper() not in fingerprints:
            raise RuntimeUpdateError(
                "signature was not made by the pinned Bun release key"
            )


def _cleartext_body(manifest: bytes) -> str:
    """Extract the body from a clear-signed OpenPGP message after verification."""
    lines = manifest.decode("utf-8").splitlines()
    if not lines or lines[0] != "-----BEGIN PGP SIGNED MESSAGE-----":
        raise RuntimeUpdateError("checksum manifest is not clear-signed OpenPGP text")
    try:
        separator = lines.index("", 1)
        signature = next(
            index
            for index in range(separator + 1, len(lines))
            if lines[index] == "-----BEGIN PGP SIGNATURE-----"
        )
    except (ValueError, StopIteration) as error:
        raise RuntimeUpdateError(
            "checksum manifest has an invalid clear-signed structure"
        ) from error
    body = []
    for line in lines[separator + 1 : signature]:
        body.append(line[2:] if line.startswith("- ") else line)
    return "\n".join(body) + "\n"


def _manifest_hash(manifest: bytes, archive_name: str) -> str:
    """Return the SHA-256 value for the exact archive named in a manifest."""
    body = _cleartext_body(manifest)
    for line in body.splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) == 2 and fields[1].lstrip("*").split("/")[-1] == archive_name:
            digest = fields[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                return digest
    raise RuntimeUpdateError(f"signed checksum is missing for {archive_name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _download_and_verify_release(
    release: Release, config: Config, work_dir: Path
) -> Path:
    manifest = work_dir / "SHASUMS256.txt.asc"
    archive = work_dir / release.archive_name
    _download_file(
        release.manifest_url, manifest, config.runtime_network_timeout_seconds
    )
    _download_file(release.archive_url, archive, config.runtime_network_timeout_seconds)

    keyring = work_dir / "release-keyring.gpg"
    if release.kind == "node":
        keyring.write_bytes(
            _request_bytes(
                NODE_RELEASE_KEYRING_URL,
                config.runtime_network_timeout_seconds,
                MAX_METADATA_BYTES,
            )
        )
    else:
        armored_key = work_dir / "bun-release-key.asc"
        armored_key.write_bytes(
            _request_bytes(
                BUN_KEY_URL, config.runtime_network_timeout_seconds, MAX_METADATA_BYTES
            )
        )
        gpg = _find_tool(config, "gpg", config.runtime_gpg_path)
        result = subprocess.run(
            [
                str(gpg),
                "--batch",
                "--yes",
                "--no-options",
                "--dearmor",
                "--output",
                str(keyring),
                str(armored_key),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=config.command_timeout_seconds,
        )
        if result.returncode != 0:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or "unknown key conversion error"
            )
            raise RuntimeUpdateError(f"Bun release key conversion failed: {detail}")

    _verify_signature(
        manifest,
        keyring,
        config,
        expected_fingerprint=BUN_KEY_FINGERPRINT if release.kind == "bun" else None,
    )
    expected = _manifest_hash(manifest.read_bytes(), release.archive_name)
    actual = _sha256(archive)
    if actual != expected:
        raise RuntimeUpdateError(f"archive hash mismatch for {release.archive_name}")
    return archive


def _safe_tar_path(root: Path, name: str, linkname: str | None = None) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeUpdateError(f"unsafe archive path: {name}")
    target = (root / Path(*relative.parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeUpdateError(f"archive path escapes destination: {name}") from error
    if linkname is not None:
        link = PurePosixPath(linkname)
        if link.is_absolute():
            raise RuntimeUpdateError(f"unsafe archive link: {linkname}")
        linked_target = (target.parent / Path(*link.parts)).resolve()
        try:
            linked_target.relative_to(root.resolve())
        except ValueError as error:
            raise RuntimeUpdateError(
                f"archive link escapes destination: {linkname}"
            ) from error
    return target


def _extract_node_archive(archive: Path, destination: Path, expected_root: str) -> Path:
    with tarfile.open(archive, mode="r:xz") as bundle:
        members = bundle.getmembers()
        for member in members:
            _safe_tar_path(
                destination,
                member.name,
                member.linkname if member.issym() or member.islnk() else None,
            )
            if not (
                member.isdir() or member.isreg() or member.issym() or member.islnk()
            ):
                raise RuntimeUpdateError(
                    f"unsupported Node archive entry: {member.name}"
                )
        for member in members:
            bundle.extract(member, destination)
    extracted = destination / expected_root
    if (
        not (extracted / "bin" / "node").is_file()
        or not (extracted / "bin" / "npm").exists()
    ):
        raise RuntimeUpdateError(
            "Node archive did not contain the expected runtime layout"
        )
    return extracted


def _extract_bun_archive(archive: Path, destination: Path) -> Path:
    expected = "bun-darwin-aarch64/bun"
    with zipfile.ZipFile(archive) as bundle:
        try:
            info = bundle.getinfo(expected)
        except KeyError as error:
            raise RuntimeUpdateError(
                "Bun archive did not contain the expected runtime"
            ) from error
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise RuntimeUpdateError("Bun runtime archive entry is a symlink")
        target = destination / "bun"
        with bundle.open(info) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, CHUNK_SIZE)
    target.chmod(0o755)
    return target


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode & 0o777
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod((mode | 0o400) & ~0o222)
    root.chmod(0o555)


def _current_version(link: Path, prefix: str) -> Version | None:
    if not link.is_symlink():
        return None
    try:
        resolved = link.resolve(strict=True)
    except OSError:
        return None
    parent = resolved.parent.parent if prefix == "node" else resolved.parent
    if not parent.name.startswith(f"{prefix}-v"):
        return None
    try:
        return parse_version(parent.name.removeprefix(f"{prefix}-v"))
    except RuntimeUpdateError:
        return None


def _switch_links(links: dict[Path, Path]) -> dict[Path, str | None]:
    """Switch symlinks and return old targets for rollback."""
    old: dict[Path, str | None] = {}
    for link in links:
        if link.exists() and not link.is_symlink():
            raise RuntimeUpdateError(
                f"refusing to replace non-symlink runtime entry: {link}"
            )
        old[link] = os.readlink(link) if link.is_symlink() else None
    changed: list[Path] = []
    try:
        for link, target in links.items():
            link.parent.mkdir(parents=True, exist_ok=True)
            temporary = link.with_name(f".{link.name}.new-{os.getpid()}")
            temporary.unlink(missing_ok=True)
            os.symlink(os.path.relpath(target, link.parent), temporary)
            os.replace(temporary, link)
            changed.append(link)
    except (OSError, RuntimeError) as error:
        for link in changed:
            link.unlink(missing_ok=True)
        for link, target in old.items():
            link.unlink(missing_ok=True)
            if target is not None:
                os.symlink(target, link)
        raise RuntimeUpdateError(f"runtime symlink switch failed: {error}") from error
    return old


def _restore_links(old: dict[Path, str | None]) -> None:
    for link, target in old.items():
        link.unlink(missing_ok=True)
        if target is not None:
            os.symlink(target, link)


def _validate_node(root: Path, release: Release, config: Config) -> None:
    node = root / "bin" / "node"
    npm = root / "bin" / "npm"
    environment = os.environ.copy()
    environment["PATH"] = f"{root / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
    node_result = subprocess.run(
        [str(node), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=config.command_timeout_seconds,
    )
    if (
        node_result.returncode != 0
        or node_result.stdout.strip() != f"v{release.version.text}"
    ):
        raise RuntimeUpdateError("new Node binary failed its version check")
    npm_result = subprocess.run(
        [str(npm), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=config.command_timeout_seconds,
    )
    if npm_result.returncode != 0 or npm_result.stdout.strip() != release.bundled_npm:
        raise RuntimeUpdateError("new npm binary failed its version check")


def _validate_bun(root: Path, release: Release, config: Config) -> None:
    result = subprocess.run(
        [str(root / "bun"), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=config.command_timeout_seconds,
    )
    if result.returncode != 0 or result.stdout.strip() != release.version.text:
        raise RuntimeUpdateError("new Bun binary failed its version check")


def _install_release(release: Release, config: Config) -> None:
    root = config.runtime_install_root
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    target = root / f"{release.kind}-v{release.version.text}"
    if target.exists():
        raise RuntimeUpdateError(f"versioned install already exists: {target}")
    with tempfile.TemporaryDirectory(
        prefix=f".{release.kind}-{release.version.text}-", dir=root
    ) as work:
        work_dir = Path(work)
        archive = _download_and_verify_release(release, config, work_dir)
        extracted = work_dir / "extracted"
        extracted.mkdir()
        if release.kind == "node":
            staged = _extract_node_archive(
                archive, extracted, f"node-v{release.version.text}-darwin-arm64"
            )
            _validate_node(staged, release, config)
        else:
            staged = extracted / "bun-root"
            staged.mkdir()
            _extract_bun_archive(archive, staged)
            _validate_bun(staged, release, config)
        os.replace(staged, target)
        _make_read_only(target)

    if release.kind == "node":
        links = {
            config.runtime_bin_dir / "node": target / "bin" / "node",
            config.runtime_bin_dir / "npm": target / "bin" / "npm",
            config.runtime_bin_dir / "npx": target / "bin" / "npx",
        }
    else:
        links = {
            config.runtime_bin_dir / "bun": target / "bun",
            config.runtime_bin_dir / "bunx": target / "bun",
        }
    old = _switch_links(links)
    try:
        if release.kind == "node":
            _validate_node(target, release, config)
        else:
            _validate_bun(target, release, config)
    except RuntimeUpdateError:
        _restore_links(old)
        shutil.rmtree(target, ignore_errors=True)
        raise


def _runtime_plan(
    release: Release, current: Version | None, config: Config, now: datetime
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": release.version.text,
        "published_at": release.published_at.isoformat(),
        "current": current.text if current else None,
    }
    if current is not None and current.major != release.version.major:
        result["status"] = "blocked-major"
    elif current is not None and release.version <= current:
        result["status"] = "current"
    elif not release_is_eligible(release, now, config.runtime_min_release_age_days):
        result["status"] = "waiting-release-age"
    else:
        result["status"] = "eligible"
    return result


def update_runtimes(config: Config, now: datetime, dry_run: bool) -> dict[str, Any]:
    """Check and safely update Node/npm and Bun during a cleanup pass."""
    result: dict[str, Any] = {
        "status": "disabled",
        "node": {},
        "bun": {},
        "warnings": [],
    }
    if not config.runtime_updates_enabled or sys.platform != "darwin":
        return result
    result["status"] = "checked"
    discoveries = (
        ("node", discover_node_release, config.runtime_bin_dir / "node", "node"),
        ("bun", discover_bun_release, config.runtime_bin_dir / "bun", "bun"),
    )
    for name, discover, link, prefix in discoveries:
        try:
            release = discover(config)
            current = _current_version(link, prefix)
            plan = _runtime_plan(release, current, config, now)
            if plan["status"] == "eligible" and not dry_run:
                _install_release(release, config)
                plan["status"] = "updated"
                plan["current"] = release.version.text
            result[name] = plan
        except (
            RuntimeUpdateError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ) as error:
            result[name] = {"status": "failed"}
            result["warnings"].append(f"{name} runtime update skipped: {error}")
    return result
