"""
Check and apply updates from GitHub Releases.

Settings key ``update_github_repo``: ``owner/repo`` or full GitHub URL.

Every release must carry ``SHA256SUMS`` and ``SHA256SUMS.sig``. The SUMS file
starts with a ``version: X.Y.Z`` line followed by ``<sha256>  <file>`` lines,
and the whole file is signed with an Ed25519 key listed in
``update_keys.UPDATE_PUBLIC_KEYS``. Nothing is extracted, moved or run until
the signature, the version (equal to the tag, newer than this build) and the
downloaded file's hash all check out — otherwise the update is refused.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import config

_GITHUB_API = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
_REPO_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
    re.I,
)
_VERSION_RE = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)$")
_SUMS_LINE_RE = re.compile(r"^([0-9a-fA-F]{64}) [ *](\S.*)$")
SUMS_NAME = "SHA256SUMS"
SIG_NAME = "SHA256SUMS.sig"
_MAX_SIGNATURE_FILE = 64 * 1024
_SKIP_DIRS = {".venv", ".git", "dist", "build", "__pycache__"}
_SKIP_FILES = {".DS_Store"}


class UpdateVerificationError(Exception):
    """The release could not be verified; nothing was installed."""


class UpdateNotNewerError(UpdateVerificationError):
    """The signed release is not newer than the running version."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    html_url: str
    source_zip_url: Optional[str]
    exe_url: Optional[str]
    exe_name: Optional[str]
    source_zip_name: Optional[str] = None
    sums_url: Optional[str] = None
    sig_url: Optional[str] = None
    owner: str = ""
    repo: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest: Optional[ReleaseInfo]
    update_available: bool
    error: Optional[str] = None
    # True when the repo exists but has no published release yet (GitHub 404
    # on releases/latest). This is a normal state, not an error.
    no_releases: bool = False
    # A newer release exists but has no SHA256SUMS / SHA256SUMS.sig.
    unsigned: bool = False
    # A newer release exists but its signature / version check failed.
    verify_error: Optional[str] = None


def parse_github_repo(spec: str) -> tuple[str, str]:
    """Return (owner, repo) from owner/repo or a github.com URL."""
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("empty repository")
    if "/" in spec and "github.com" not in spec.lower():
        owner, repo = spec.split("/", 1)
        repo = repo.removesuffix(".git")
        return owner.strip(), repo.strip()
    m = _REPO_RE.match(spec)
    if m:
        return m.group(1), m.group(2)
    raise ValueError(f"Not a valid GitHub repo: {spec!r}")


def _parse_version(tag: str) -> tuple[int, int, int]:
    """Strict ``X.Y.Z`` (optional leading v). Pre-release / build suffixes are
    rejected: ``0.4.0-rc1`` would otherwise compare equal to ``0.4.0``."""
    m = _VERSION_RE.match((tag or "").strip())
    if not m:
        raise ValueError(f"not an X.Y.Z version: {tag!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _version_gt(a: str, b: str) -> bool:
    return _parse_version(a) > _parse_version(b)


# --- HTTP ------------------------------------------------------------------

class _HttpsOnlyRedirects(HTTPRedirectHandler):
    """Follow GitHub's asset redirects (to its CDN), but only over HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise UpdateVerificationError(f"refusing non-HTTPS redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = build_opener(_HttpsOnlyRedirects)


def _user_agent() -> dict:
    return {"User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"}


def _http_get_json(url: str, timeout: float = 30.0) -> dict:
    req = Request(url, headers={"Accept": "application/vnd.github+json", **_user_agent()})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _check_asset_url(url: str, owner: str, repo: str) -> None:
    """The URL we start from must be this repo's release download.

    Only the initial URL is pinned: GitHub redirects downloads to its CDN
    (objects.githubusercontent.com today), which may change. Integrity comes
    from the signature; redirects are still required to stay on HTTPS.
    """
    prefix = f"https://github.com/{owner}/{repo}/releases/download/".lower()
    if not (url or "").lower().startswith(prefix):
        raise UpdateVerificationError(
            f"release asset is not a {owner}/{repo} release download: {url}"
        )


def _download(url: str, dest: Path, timeout: float = 120.0) -> None:
    req = Request(url, headers=_user_agent())
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _opener.open(req, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _download_small(url: str, timeout: float = 30.0) -> bytes:
    req = Request(url, headers=_user_agent())
    with _opener.open(req, timeout=timeout) as resp:
        data = resp.read(_MAX_SIGNATURE_FILE + 1)
    if len(data) > _MAX_SIGNATURE_FILE:
        raise UpdateVerificationError(f"{url} is unexpectedly large")
    return data


# --- Release lookup ----------------------------------------------------------

def fetch_latest_release(owner: str, repo: str) -> ReleaseInfo:
    data = _http_get_json(_GITHUB_API.format(owner=owner, repo=repo))
    tag = str(data.get("tag_name") or "")
    version = tag.lstrip("vV") or "0"
    html_url = str(data.get("html_url") or f"https://github.com/{owner}/{repo}/releases")

    source_zip_url: Optional[str] = None
    source_zip_name: Optional[str] = None
    exe_url: Optional[str] = None
    exe_name: Optional[str] = None
    sums_url: Optional[str] = None
    sig_url: Optional[str] = None

    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not url:
            continue
        low = name.lower()
        if name == SUMS_NAME:
            sums_url = url
        elif name == SIG_NAME:
            sig_url = url
        elif low.endswith(".zip") and "claude-quota-tray" in low and source_zip_url is None:
            source_zip_url, source_zip_name = url, name
        elif low.endswith(".exe") and "claudequotatray" in low.replace("-", "").replace("_", ""):
            exe_url = url
            exe_name = name
        elif low.endswith(".zip") and source_zip_url is None and "source" in low:
            source_zip_url, source_zip_name = url, name

    # GitHub's auto-generated zipball is deliberately not used: it is not
    # listed in the signed SHA256SUMS, so it can't be verified.
    return ReleaseInfo(
        version=version,
        tag=tag,
        html_url=html_url,
        source_zip_url=source_zip_url,
        exe_url=exe_url,
        exe_name=exe_name,
        source_zip_name=source_zip_name,
        sums_url=sums_url,
        sig_url=sig_url,
        owner=owner,
        repo=repo,
    )


# --- Verification -------------------------------------------------------------

def _public_keys() -> list[str]:
    from update_keys import UPDATE_PUBLIC_KEYS
    return [k.strip().lower() for k in UPDATE_PUBLIC_KEYS if k and k.strip()]


def _decode_signature(sig: bytes) -> bytes:
    if len(sig) == 64:
        return sig
    try:
        raw = base64.b64decode(sig.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise UpdateVerificationError("SHA256SUMS.sig is not a valid signature file")
    if len(raw) != 64:
        raise UpdateVerificationError("SHA256SUMS.sig is not an Ed25519 signature")
    return raw


def verify_signature(sums: bytes, sig: bytes, public_keys: Optional[list[str]] = None) -> None:
    """Raise UpdateVerificationError unless ``sig`` signs ``sums`` with a trusted key."""
    keys = _public_keys() if public_keys is None else public_keys
    if not keys:
        raise UpdateVerificationError(
            "this build has no update-signing key, so updates can't be verified"
        )
    try:
        from Crypto.Signature import eddsa
    except ImportError:
        raise UpdateVerificationError("pycryptodome is required to verify updates")
    signature = _decode_signature(sig)
    for hex_key in keys:
        try:
            public = eddsa.import_public_key(bytes.fromhex(hex_key))
            eddsa.new(public, "rfc8032").verify(sums, signature)
            return
        except ValueError:
            continue
    raise UpdateVerificationError("SHA256SUMS signature does not match a trusted key")


def parse_sums(sums: bytes) -> tuple[str, dict[str, str]]:
    """Return (version, {file name: sha256 hex}) from a SHA256SUMS file."""
    try:
        text = sums.decode("utf-8")
    except UnicodeDecodeError:
        raise UpdateVerificationError("SHA256SUMS is not UTF-8 text")
    version: Optional[str] = None
    entries: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip("\r")
        if not line.strip():
            continue
        if line.startswith("version:"):
            if version is not None:
                raise UpdateVerificationError("SHA256SUMS has more than one version line")
            version = line.split(":", 1)[1].strip()
            continue
        m = _SUMS_LINE_RE.match(line)
        if not m:
            raise UpdateVerificationError(f"unexpected line in SHA256SUMS: {line[:80]!r}")
        entries[m.group(2)] = m.group(1).lower()
    if version is None:
        raise UpdateVerificationError("SHA256SUMS has no version line")
    return version, entries


def verify_release(sums: bytes, sig: bytes, release: ReleaseInfo,
                   current_version: Optional[str] = None,
                   public_keys: Optional[list[str]] = None) -> dict[str, str]:
    """Check signature, version and tag; return the trusted file hashes."""
    verify_signature(sums, sig, public_keys)
    version, entries = parse_sums(sums)
    try:
        signed = _parse_version(version)
        tagged = _parse_version(release.tag)
    except ValueError as e:
        raise UpdateVerificationError(str(e))
    if signed != tagged:
        raise UpdateVerificationError(
            f"signed version {version} does not match release tag {release.tag}"
        )
    current = current_version or config.APP_VERSION
    if not _version_gt(version, current):
        raise UpdateNotNewerError(
            f"release {version} is not newer than the installed {current}"
        )
    return entries


def verify_asset(path: Path, expected_sha256: str, name: Optional[str] = None) -> None:
    """``name`` is the release asset name for messages (``path`` may be a
    temp/staging file such as ``release.zip`` or ``….exe.new``)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if not hmac.compare_digest(h.hexdigest(), (expected_sha256 or "").lower()):
        raise UpdateVerificationError(
            f"{name or path.name} does not match its signed SHA-256"
        )


def _fetch_signature_files(release: ReleaseInfo) -> tuple[bytes, bytes]:
    if not release.sums_url or not release.sig_url:
        raise UpdateVerificationError(
            f"release {release.tag} has no {SUMS_NAME} / {SIG_NAME}, so it can't be verified"
        )
    for url in (release.sums_url, release.sig_url):
        _check_asset_url(url, release.owner, release.repo)
    return _download_small(release.sums_url), _download_small(release.sig_url)


def _expected_hash(entries: dict[str, str], name: Optional[str]) -> str:
    if not name or name not in entries:
        raise UpdateVerificationError(f"{name or 'asset'} is not listed in {SUMS_NAME}")
    return entries[name]


# --- Check ------------------------------------------------------------------

def check_for_update(repo_spec: str, current_version: str | None = None) -> UpdateCheckResult:
    """Report an update only when it is newer *and* its signature checks out."""
    current = current_version or config.APP_VERSION
    try:
        owner, repo = parse_github_repo(repo_spec)
        latest = fetch_latest_release(owner, repo)
    except HTTPError as e:
        # 404 on releases/latest means the repo simply has no published
        # release yet — treat as a benign "nothing to update to" state.
        if e.code == 404:
            return UpdateCheckResult(current_version=current, latest=None,
                                     update_available=False, no_releases=True)
        return UpdateCheckResult(current_version=current, latest=None,
                                 update_available=False, error=str(e))
    except (URLError, OSError, ValueError, json.JSONDecodeError, KeyError) as e:
        # OSError also covers socket timeouts during the response read.
        return UpdateCheckResult(current_version=current, latest=None,
                                 update_available=False, error=str(e))

    try:
        newer = _version_gt(latest.tag, current)
    except ValueError:
        newer = False  # e.g. a v0.4.0-rc1 tag: not something we install
    if not newer:
        return UpdateCheckResult(current_version=current, latest=latest,
                                 update_available=False)
    if not latest.sums_url or not latest.sig_url:
        return UpdateCheckResult(current_version=current, latest=latest,
                                 update_available=False, unsigned=True)
    try:
        sums, sig = _fetch_signature_files(latest)
        verify_release(sums, sig, latest, current)
    except UpdateVerificationError as e:
        return UpdateCheckResult(current_version=current, latest=latest,
                                 update_available=False, verify_error=str(e))
    except (URLError, OSError) as e:
        return UpdateCheckResult(current_version=current, latest=latest,
                                 update_available=False, error=str(e))
    return UpdateCheckResult(current_version=current, latest=latest,
                             update_available=True)


# --- Apply ------------------------------------------------------------------

def _copy_tree(src: Path, dest: Path) -> None:
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if item.name in _SKIP_FILES:
            continue
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _find_extracted_root(extract_dir: Path) -> Path:
    """Release source zips contain one top-level folder."""
    children = [p for p in extract_dir.iterdir() if p.name not in _SKIP_DIRS]
    if len(children) == 1 and children[0].is_dir():
        inner = children[0]
        if (inner / "src" / "main.py").is_file() or (inner / "requirements.txt").is_file():
            return inner
    if (extract_dir / "src" / "main.py").is_file():
        return extract_dir
    for child in children:
        if child.is_dir() and (child / "src" / "main.py").is_file():
            return child
    raise RuntimeError("Downloaded archive does not look like claude-quota-tray source")


def apply_source_update(project_dir: Path, release: ReleaseInfo,
                        entries: dict[str, str]) -> str:
    if not release.source_zip_url:
        raise UpdateVerificationError("No source .zip asset on the latest release")
    expected = _expected_hash(entries, release.source_zip_name)
    _check_asset_url(release.source_zip_url, release.owner, release.repo)

    with tempfile.TemporaryDirectory(prefix="cqt-update-") as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "release.zip"
        _download(release.source_zip_url, zip_path)
        verify_asset(zip_path, expected, release.source_zip_name)
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        src_root = _find_extracted_root(extract_dir)
        _copy_tree(src_root, project_dir)

    venv_py = project_dir / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        cmd = pip_install_command(venv_py, project_dir)
        if cmd:
            subprocess.run(cmd, cwd=project_dir, check=True)
    return f"Updated to {release.tag}"


def pip_install_command(python: Path, project_dir: Path) -> Optional[list[str]]:
    """Install dependencies, hash-checked when requirements.lock is present.

    The lock came out of the release zip, which was verified against the
    signed SHA256SUMS, so its hashes are trustworthy.
    """
    base = [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    lock = project_dir / "requirements.lock"
    if lock.is_file():
        return base + ["--require-hashes", "-r", str(lock)]
    req = project_dir / "requirements.txt"
    if req.is_file():
        return base + ["-r", str(req)]
    return None


def apply_exe_update(project_dir: Path, release: ReleaseInfo,
                     entries: dict[str, str]) -> str:
    if not release.exe_url or not release.exe_name:
        raise UpdateVerificationError("No .exe asset on the latest release")
    expected = _expected_hash(entries, release.exe_name)
    _check_asset_url(release.exe_url, release.owner, release.repo)

    dest = project_dir / release.exe_name
    staging = project_dir / f"{release.exe_name}.new"
    try:
        _download(release.exe_url, staging)
        verify_asset(staging, expected, release.exe_name)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    running = Path(sys.executable).resolve()
    if dest.exists() and running == dest.resolve():
        bat = project_dir / "Apply Claude Quota Tray update.bat"
        bat.write_text(
            "@echo off\n"
            'cd /d "%~dp0"\n'
            "timeout /t 3 /nobreak >nul\n"
            f'move /y "{staging.name}" "{dest.name}"\n'
            f'start "" "{dest.name}"\n'
            'del "%~f0"\n',
            encoding="utf-8",
        )
        if sys.platform == "win32":
            os.startfile(bat)  # type: ignore[attr-defined]
        return "Update will apply after you quit the app."
    if staging.exists():
        shutil.move(str(staging), str(dest))
    return f"Saved {dest.name}"


def apply_update(project_dir: Path, repo_spec: str, *, prefer_exe: bool = False) -> str:
    """Verify the latest release completely, then install it.

    Raises UpdateNotNewerError / UpdateVerificationError (nothing touched)
    when the release is unsigned, tampered with, mis-tagged or not newer.
    """
    owner, repo = parse_github_repo(repo_spec)
    release = fetch_latest_release(owner, repo)
    sums, sig = _fetch_signature_files(release)
    entries = verify_release(sums, sig, release)
    if prefer_exe and release.exe_url:
        return apply_exe_update(project_dir, release, entries)
    if (project_dir / "src" / "main.py").is_file():
        return apply_source_update(project_dir, release, entries)
    if release.exe_url:
        return apply_exe_update(project_dir, release, entries)
    raise RuntimeError("Cannot determine install type for update")
