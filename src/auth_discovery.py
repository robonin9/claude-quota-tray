"""
Unified OAuth discovery for Claude Quota Tray.

Tries multiple sources in platform-aware order so the app works whether the
user signs in via Claude Desktop, Claude Code CLI, or a manual env var.

Each provider returns None on "not applicable / not found" and raises
TokenError only for unexpected failures worth surfacing.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from token_reader import (
    TokenError,
    _credentials_from_data,
    _format_plan,
    _read_credentials_files,
    _read_env_token,
    _read_windows_credential_manager,
)


@dataclass(frozen=True)
class AuthProbeResult:
    name: str
    ok: bool
    detail: str


Provider = Callable[[], Optional[dict]]


def _try_desktop() -> Optional[dict]:
    """Claude Desktop token. Returns None only when Desktop isn't installed;
    otherwise a failure raises TokenError with the real reason so the
    summary / --probe can show *why* (missing key, new encryption, …)."""
    from desktop_auth import claude_desktop_dirs, read_desktop_credentials

    if not claude_desktop_dirs():
        return None
    return read_desktop_credentials()


def _try_macos_keychain() -> Optional[dict]:
    if sys.platform != "darwin":
        return None
    import subprocess

    for service in ("Claude Code-credentials", "Claude Code"):
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-w"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            text = proc.stdout.strip()
            if text.startswith("{"):
                return _credentials_from_data(
                    json.loads(text), f"mac-keychain:{service}"
                )
            if text.startswith("sk-ant-"):
                return {
                    "token": text,
                    "plan": None,
                    "raw": {"service": service},
                    "source": f"mac-keychain:{service}",
                }
        except (OSError, json.JSONDecodeError, subprocess.SubprocessError, TokenError):
            continue
    return None


def _providers_for_platform() -> list[tuple[str, Provider]]:
    """(label, callable) in try order — first success wins."""
    from token_store import read_app_credentials

    chain: list[tuple[str, Provider]] = [
        ("environment", _read_env_token),
        # Token saved via tray menu → "Connect Claude account" (setup-token).
        ("app-token", read_app_credentials),
        ("claude-desktop", _try_desktop),
    ]
    if sys.platform == "win32":
        chain.append(("windows-credential-manager", _read_windows_credential_manager))
    elif sys.platform == "darwin":
        chain.append(("macos-keychain", _try_macos_keychain))
    chain.append(("credential-files", _read_credentials_files))
    return chain


def read_credentials(exclude_tokens: Optional[set] = None) -> dict:
    """
    Return {"token", "plan", "raw", "source"} from the first working provider.

    ``exclude_tokens`` lets the caller skip token values already known to be
    invalid (e.g. a source whose token returned 401), so discovery falls
    through to the next source instead of getting stuck on a stale token.

    Raises TokenError with a summary of everything that was tried.
    """
    from token_reader import find_expiry_ms

    exclude = exclude_tokens or set()
    failures: list[str] = []
    skipped_bad = False
    expired_fallback: Optional[dict] = None
    now_ms = time.time() * 1000.0
    for name, provider in _providers_for_platform():
        try:
            creds = provider()
            if creds and creds.get("token"):
                if creds["token"] in exclude:
                    skipped_bad = True
                    failures.append(f"{name}: token rejected earlier (skipped)")
                    continue
                creds.setdefault("source", name)
                exp = find_expiry_ms(creds.get("raw") or {})
                if exp is not None and exp <= now_ms:
                    # Stale (e.g. Claude Code not run for a while) — prefer a
                    # fresh token from another source, but keep this one as a
                    # last resort since the server is the final judge.
                    failures.append(f"{name}: token expired")
                    if expired_fallback is None:
                        expired_fallback = creds
                    continue
                return creds
            failures.append(f"{name}: not found")
        except TokenError as e:
            failures.append(f"{name}: {e}")
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")

    if expired_fallback is not None:
        return expired_fallback

    summary = _format_failure_summary(failures)
    if skipped_bad:
        summary = (
            "All discovered tokens were rejected (expired or invalid). "
            "Sign in again to Claude Desktop or Claude Code.\n\n" + summary
        )
    raise TokenError(summary)


def _token_fingerprint(token: str) -> str:
    token = token.strip()
    if len(token) <= 12:
        return f"len={len(token)}"
    return f"{token[:6]}...{token[-4:]} (len {len(token)})"


def _expiry_note(creds: dict) -> str:
    """Human note about token expiry, if the source exposes it."""
    from token_reader import find_expiry_ms

    expires_ms = find_expiry_ms(creds.get("raw") or {})
    if expires_ms is None:
        return ""
    secs = expires_ms / 1000.0 - time.time()
    if secs <= 0:
        return ", EXPIRED"
    if secs < 3600:
        return f", expires in {int(secs // 60)}m"
    if secs < 86400:
        return f", expires in {int(secs // 3600)}h"
    return f", expires in {int(secs // 86400)}d"


def probe_auth_sources() -> list[AuthProbeResult]:
    """Try every provider independently (for diagnostics on a new machine).

    Reports, per source: whether anything was found, the resolved sub-source,
    a token fingerprint (so two sources sharing one token are obvious),
    the detected plan, and expiry status when available.
    """
    results: list[AuthProbeResult] = []
    for name, provider in _providers_for_platform():
        try:
            creds = provider()
            if creds and creds.get("token"):
                src = creds.get("source", name)
                plan = creds.get("plan") or "-"
                fp = _token_fingerprint(creds["token"])
                detail = f"OK | {src} | {fp} | plan={plan}{_expiry_note(creds)}"
                results.append(AuthProbeResult(name, True, detail))
            else:
                results.append(AuthProbeResult(name, False, "not found / not applicable"))
        except TokenError as e:
            results.append(AuthProbeResult(name, False, str(e)[:200]))
        except Exception as e:
            results.append(AuthProbeResult(name, False, f"{type(e).__name__}: {e}"[:200]))
    results.extend(_probe_locations())
    return results


def _probe_locations() -> list[AuthProbeResult]:
    """Which credential locations exist (names/sizes only — never contents)."""
    from desktop_auth import claude_desktop_dirs
    from token_reader import _candidate_paths

    rows: list[AuthProbeResult] = []
    for p in _candidate_paths():
        if not p.is_file():
            continue
        detail = f"exists ({p.stat().st_size} bytes)"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Big config files: top-level keys only.
            depth = 1 if p.stat().st_size > 4096 else 3
            detail += f" shape={_redacted_shape(data, depth)}"
        except Exception as e:
            detail += f" unreadable: {type(e).__name__}"
        rows.append(AuthProbeResult(f"file {p}", True, detail))
    for d in claude_desktop_dirs():
        cfg = d / "config.json"
        state = d / "Local State"
        detail = (f"config.json={'yes' if cfg.is_file() else 'no'}, "
                  f"Local State={'yes' if state.is_file() else 'no'}")
        if cfg.is_file():
            try:
                keys = sorted(json.loads(cfg.read_text(encoding="utf-8")).keys())
                detail += f", keys={keys}"
            except Exception as e:
                detail += f", unreadable: {type(e).__name__}"
        try:
            names = sorted(x.name + ("/" if x.is_dir() else "") for x in d.iterdir())
            detail += f", entries={names[:60]}"
        except OSError as e:
            detail += f", cannot list: {e}"
        rows.append(AuthProbeResult(f"desktop dir {d}", True, detail))
    if sys.platform == "win32":
        for target, size in _list_windows_credentials():
            rows.append(AuthProbeResult(f"credential-manager {target}", True,
                                        f"blob {size} bytes"))
    return rows


def _redacted_shape(data, depth: int = 3):
    """Structure of a JSON value with every secret hidden.

    Strings become ``str(len=N)``; token-looking strings also show a short,
    non-secret prefix (``sk-ant-oa…``) so the token *type* is identifiable.
    """
    if depth <= 0:
        return type(data).__name__
    if isinstance(data, dict):
        return {k: _redacted_shape(v, depth - 1) for k, v in list(data.items())[:40]}
    if isinstance(data, list):
        return [f"list(len={len(data)})"] + (
            [_redacted_shape(data[0], depth - 1)] if data else [])
    if isinstance(data, str):
        if data.startswith("sk-ant-"):
            return f"str(len={len(data)}, {data[:9]}…)"
        return f"str(len={len(data)})"
    return type(data).__name__


def _list_windows_credentials() -> list[tuple[str, int]]:
    """(target name, blob size) of Credential Manager entries mentioning
    Claude/Anthropic — names only, never the secret."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.c_void_p), ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
        ]

    out: list[tuple[str, int]] = []
    try:
        advapi32 = ctypes.windll.advapi32
        count = wintypes.DWORD()
        creds = ctypes.POINTER(ctypes.POINTER(CREDENTIALW))()
        if not advapi32.CredEnumerateW(None, 0, ctypes.byref(count), ctypes.byref(creds)):
            return []
        try:
            for i in range(count.value):
                c = creds[i].contents
                name = c.TargetName or ""
                if "claude" in name.lower() or "anthropic" in name.lower():
                    out.append((name, int(c.CredentialBlobSize)))
        finally:
            advapi32.CredFree(creds)
    except Exception:
        return out
    return out


def _format_failure_summary(failures: list[str]) -> str:
    platform = sys.platform
    lines = "\n  - ".join(failures)
    hints = {
        "win32": (
            "Fix: tray menu → Connect Claude account… (runs `claude setup-token` "
            "and stores a long-lived token in Credential Manager). Newer Claude "
            "Desktop builds no longer keep a readable token on disk."
        ),
        "darwin": (
            "On macOS: sign in to Claude Desktop or Claude Code. "
            "Tokens may be in ~/Library/Application Support/Claude/ or Keychain."
        ),
        "linux": (
            "On Linux: sign in to Claude Desktop or Claude Code. "
            "Look for ~/.config/Claude/ or ~/.claude/.credentials.json."
        ),
    }.get(platform, "Sign in to Claude Desktop or Claude Code.")

    return (
        f"Claude OAuth token not found (platform={platform}).\n"
        f"Tried:\n  - {lines}\n\n"
        f"{hints}\n\n"
        "Override: set CLAUDE_CODE_OAUTH_TOKEN or Account → Manage accounts → "
        "pick a credentials file.\n"
        "Diagnostics: python src/token_reader.py --probe"
    )
