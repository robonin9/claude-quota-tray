"""
App-owned storage for a Claude OAuth token obtained via ``claude setup-token``.

The token is kept in the OS secret store, never in settings.json:
  - Windows: Credential Manager (generic credential, DPAPI-protected per user)
  - macOS:   login Keychain
  - other:   ~/.claude-quota-tray/token with 0600 permissions
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

TARGET = "ClaudeQuotaTray/oauth-token"
_MAC_SERVICE = "ClaudeQuotaTray"
_MAC_ACCOUNT = "oauth-token"

_TOKEN_RE = re.compile(r"sk-ant-oat\d\d-[A-Za-z0-9_\-]+")
_TOKEN_CONT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def extract_token(text: str) -> Optional[str]:
    """Find an OAuth token in pasted / clipboard text.

    Handles a token that the terminal wrapped over several lines: lines that
    directly follow the match and consist only of token characters are joined.
    """
    if not text:
        return None
    lines = text.replace("\r", "").split("\n")
    for i, line in enumerate(lines):
        m = _TOKEN_RE.search(line)
        if not m:
            continue
        token = m.group(0)
        if m.end() == len(line.rstrip()):
            for nxt in lines[i + 1:]:
                nxt = nxt.strip()
                if nxt and _TOKEN_CONT_RE.match(nxt):
                    token += nxt
                else:
                    break
        return token if len(token) >= 40 else None
    return None


def looks_valid(token: str) -> bool:
    return bool(token) and _TOKEN_RE.fullmatch(token) is not None and len(token) >= 40


# --- Windows Credential Manager -------------------------------------------

def _win_api():
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    return ctypes, CREDENTIALW, ctypes.WinDLL("advapi32", use_last_error=True)


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


def _win_save(token: str) -> None:
    ctypes, CREDENTIALW, advapi32 = _win_api()
    blob = token.encode("utf-8")
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred = CREDENTIALW()
    cred.Type = _CRED_TYPE_GENERIC
    cred.TargetName = TARGET
    cred.Comment = "Claude Quota Tray - Claude OAuth token (claude setup-token)"
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
    cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = "claude"
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(f"CredWriteW failed (error {ctypes.get_last_error()})")


def _win_load() -> Optional[str]:
    ctypes, CREDENTIALW, advapi32 = _win_api()
    pcred = ctypes.POINTER(CREDENTIALW)()
    if not advapi32.CredReadW(TARGET, _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return None
    try:
        c = pcred.contents
        if not c.CredentialBlobSize:
            return None
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-8").strip()
    finally:
        advapi32.CredFree(pcred)


def _win_delete() -> None:
    ctypes, _C, advapi32 = _win_api()
    advapi32.CredDeleteW(TARGET, _CRED_TYPE_GENERIC, 0)


# --- macOS Keychain --------------------------------------------------------

def _mac_save(token: str) -> None:
    import subprocess
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", _MAC_SERVICE,
         "-a", _MAC_ACCOUNT, "-w", token],
        check=True, capture_output=True, timeout=10,
    )


def _mac_load() -> Optional[str]:
    import subprocess
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", _MAC_SERVICE,
         "-a", _MAC_ACCOUNT, "-w"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _mac_delete() -> None:
    import subprocess
    subprocess.run(
        ["security", "delete-generic-password", "-s", _MAC_SERVICE, "-a", _MAC_ACCOUNT],
        capture_output=True, timeout=10, check=False,
    )


# --- File fallback ---------------------------------------------------------

def _file_path() -> Path:
    import settings as user_settings
    return user_settings.data_dir() / "token"


def _file_save(token: str) -> None:
    p = _file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _file_load() -> Optional[str]:
    p = _file_path()
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _file_delete() -> None:
    try:
        _file_path().unlink()
    except OSError:
        pass


# --- Public API ------------------------------------------------------------

def save_token(token: str) -> None:
    token = token.strip()
    if not looks_valid(token):
        raise ValueError("Not a Claude OAuth token (expected sk-ant-oat01-…)")
    if sys.platform == "win32":
        _win_save(token)
    elif sys.platform == "darwin":
        _mac_save(token)
    else:
        _file_save(token)


def load_token() -> Optional[str]:
    try:
        if sys.platform == "win32":
            return _win_load()
        if sys.platform == "darwin":
            return _mac_load()
        return _file_load()
    except Exception:
        return None


def delete_token() -> None:
    try:
        if sys.platform == "win32":
            _win_delete()
        elif sys.platform == "darwin":
            _mac_delete()
        else:
            _file_delete()
    except Exception:
        pass


def read_app_credentials() -> Optional[dict]:
    """Auth-discovery provider: the token saved by "Connect Claude account"."""
    token = load_token()
    if not token:
        return None
    return {
        "token": token,
        "plan": None,
        "raw": {"source": "app-token", "read_at": time.time()},
        "source": "app-token",
    }


# --- Claude Code CLI -------------------------------------------------------

def find_claude_cli() -> Optional[str]:
    """Path to the `claude` CLI, or None when Claude Code isn't installed."""
    import shutil

    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude"),
        home / ".claude" / "local" / ("claude.exe" if sys.platform == "win32" else "claude"),
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "npm" / "claude.cmd")
    else:
        candidates += [Path("/usr/local/bin/claude"), Path("/opt/homebrew/bin/claude")]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def launch_setup_token(cli: str) -> None:
    """Open a terminal running `claude setup-token` (browser login → token)."""
    import subprocess

    if sys.platform == "win32":
        # /k keeps the window open so the printed token stays visible.
        subprocess.Popen(
            ["cmd.exe", "/k", cli, "setup-token"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            close_fds=True,
        )
    elif sys.platform == "darwin":
        script = f'tell application "Terminal" to do script "\\"{cli}\\" setup-token"'
        subprocess.Popen(["osascript", "-e", script], close_fds=True)
    else:
        for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["xterm", "-e"]):
            try:
                subprocess.Popen(term + [cli, "setup-token"], close_fds=True)
                return
            except OSError:
                continue
        raise OSError("No terminal emulator found to run `claude setup-token`")
