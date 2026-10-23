"""macOS-specific platform helpers."""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


_LOCK_FD = None
_LOCK_PATH: Optional[Path] = None


def acquire_single_instance(wait_seconds: float = 0.0) -> bool:
    global _LOCK_FD, _LOCK_PATH
    import time
    try:
        lock_dir = Path.home() / "Library" / "Application Support" / "claude-quota-tray"
        lock_dir.mkdir(parents=True, exist_ok=True)
        _LOCK_PATH = lock_dir / "instance.lock"
        _LOCK_FD = open(_LOCK_PATH, "a+")
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            try:
                fcntl.flock(_LOCK_FD.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        _LOCK_FD.seek(0)
        _LOCK_FD.truncate()
        _LOCK_FD.write(str(os.getpid()))
        _LOCK_FD.flush()
        return True
    except (OSError, BlockingIOError):
        return False
    except Exception:
        return True


def already_running_message(app_name: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "Already running" with title "{app_name}"'],
            check=False, capture_output=True,
        )
    except Exception:
        pass


def play_alert() -> None:
    try:
        subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"], check=False, capture_output=True)
    except Exception:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass


def open_path(path: Path) -> None:
    subprocess.Popen(["open", str(path)], close_fds=True)


def open_claude_desktop() -> bool:
    for name in ("Claude", "Claude Desktop"):
        try:
            subprocess.Popen(["open", "-a", name], close_fds=True)
            return True
        except Exception:
            continue
    return False


def venv_pythonw(project_root: Path) -> Optional[Path]:
    py = project_root / ".venv" / "bin" / "python3"
    return py if py.is_file() else None


def venv_python(project_root: Path) -> Optional[Path]:
    return venv_pythonw(project_root)


def detached_popen_kwargs() -> dict:
    return {}


def hide_dock_icon() -> None:
    """Run as a menu-bar-only app: no Dock icon, no app menu.

    Must run on the main thread after Tk has created the NSApplication. The
    bundled .app also sets LSUIElement (build.sh); this covers running from
    source, where Python would otherwise show a rocket in the Dock.
    """
    try:
        import AppKit
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory
        )
    except Exception:
        pass
