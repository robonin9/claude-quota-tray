"""Windows-specific platform helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


_MUTEX_NAME = "Local\\ClaudeQuotaTray_v1"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle = None  # keep a reference so the mutex lives as long as the process


def acquire_single_instance(wait_seconds: float = 0.0) -> bool:
    """Take the single-instance mutex.

    ``wait_seconds`` > 0 retries while another instance still holds it — used
    by Restart / language change, where the old process is shutting down
    while the new one starts.
    """
    global _mutex_handle
    try:
        import ctypes
        import time
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
            err = ctypes.get_last_error()
            if handle and err != _ERROR_ALREADY_EXISTS:
                _mutex_handle = handle
                return True
            if handle:
                kernel32.CloseHandle(handle)
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)
    except Exception:
        return True


def already_running_message(app_name: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"{app_name} is already running.\nCheck the system tray (hidden icons area).",
            app_name,
            0x40,
        )
    except Exception:
        pass


def play_alert() -> None:
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def open_path(path: Path) -> None:
    os.startfile(path)  # type: ignore[attr-defined]


def open_claude_desktop() -> bool:
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = []
    if local:
        candidates.append(Path(local) / "Programs" / "claude" / "Claude.exe")
        candidates.append(Path(local) / "Programs" / "Claude" / "Claude.exe")
    for exe in candidates:
        if exe.is_file():
            try:
                subprocess.Popen(
                    [str(exe)],
                    close_fds=True,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                return True
            except Exception:
                pass
    try:
        subprocess.Popen(["explorer.exe", "shell:AppsFolder"], close_fds=True)
        return True
    except Exception:
        return False


def venv_pythonw(project_root: Path) -> Optional[Path]:
    pyw = project_root / ".venv" / "Scripts" / "pythonw.exe"
    return pyw if pyw.is_file() else None


def venv_python(project_root: Path) -> Optional[Path]:
    py = project_root / ".venv" / "Scripts" / "python.exe"
    return py if py.is_file() else None


def detached_popen_kwargs() -> dict:
    return {
        "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    }


def set_app_identity(app_user_model_id: str) -> None:
    """Separate this app from generic pythonw.exe in the taskbar and toasts."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_user_model_id)
    except Exception:
        pass
