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
    from desktop_auth import try_read_desktop_credentials

    return try_read_desktop_credentials()


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
    chain: list[tuple[str, Provider]] = [
        ("environment", _read_env_token),
        ("claude-desktop", _try_desktop),
    ]
    if sys.platform == "win32":
        chain.append(("windows-credential-manager", _read_windows_credential_manager))
    elif sys.platform == "darwin":
        chain.append(("macos-keychain", _try_macos_keychain))
    chain.append(("credential-files", _read_credentials_files))
    return chain


def read_credentials() -> dict:
    """
    Return {"token", "plan", "raw", "source"} from the first working provider.

    Raises TokenError with a summary of everything that was tried.
    """
    failures: list[str] = []
    for name, provider in _providers_for_platform():
        try:
            creds = provider()
            if creds and creds.get("token"):
                creds.setdefault("source", name)
                return creds
            failures.append(f"{name}: not found")
        except TokenError as e:
            failures.append(f"{name}: {e}")
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")

    raise TokenError(_format_failure_summary(failures))


def probe_auth_sources() -> list[AuthProbeResult]:
    """Try every provider independently (for diagnostics on a new machine)."""
    results: list[AuthProbeResult] = []
    for name, provider in _providers_for_platform():
        try:
            creds = provider()
            if creds and creds.get("token"):
                src = creds.get("source", name)
                plan = creds.get("plan") or "—"
                results.append(
                    AuthProbeResult(name, True, f"OK ({src}, plan={plan})")
                )
            else:
                results.append(AuthProbeResult(name, False, "not found"))
        except TokenError as e:
            results.append(AuthProbeResult(name, False, str(e)[:200]))
        except Exception as e:
            results.append(AuthProbeResult(name, False, f"{type(e).__name__}: {e}"[:200]))
    return results


def _format_failure_summary(failures: list[str]) -> str:
    platform = sys.platform
    lines = "\n  - ".join(failures)
    hints = {
        "win32": (
            "On Windows: sign in to Claude Desktop (MSIX app) or Claude Code CLI. "
            "Paths include %LOCALAPPDATA%\\Packages\\Claude_*\\...\\Claude\\config.json "
            "and Credential Manager."
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
