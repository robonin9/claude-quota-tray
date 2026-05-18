"""
Cross-platform notification helper.

On Windows we prefer the Windows.UI.Notifications API (via the
windows-toasts package) because it lets us specify the source app name
that shows in the toast banner. With pystray's built-in `notify()` the
banner always shows "Python" because the OS uses the running .exe's
identity, and pythonw.exe is what's running.

On other platforms (or if windows-toasts isn't installed) we fall back
to pystray's notify(), which still works — it just won't carry our
custom app label.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional


# A function f(title: str, message: str) -> bool, or None if unavailable.
_send: Optional[Callable[[str, str], bool]] = None


def _try_setup_windows_toaster(app_name: str) -> Optional[Callable[[str, str], bool]]:
    """Initialise windows-toasts. Returns a send function or None on failure."""
    if sys.platform != "win32":
        return None
    try:
        from windows_toasts import WindowsToaster, Toast  # type: ignore
    except ImportError:
        return None
    except Exception:
        # windows-toasts can also fail on import if winsdk has issues
        return None

    try:
        toaster = WindowsToaster(app_name)
    except Exception:
        return None

    def send(title: str, message: str) -> bool:
        try:
            toast = Toast(text_fields=[title, message])
            toaster.show_toast(toast)
            return True
        except Exception:
            return False

    return send


def init(app_name: str) -> None:
    """Call once at app startup with the desired source name."""
    global _send
    _send = _try_setup_windows_toaster(app_name)


def notify(icon, title: str, message: str) -> None:
    """
    Send a notification. `icon` is the pystray Icon (used only for the
    fallback path). Failure is silent — notifications are best-effort.
    """
    if _send is not None:
        if _send(title, message):
            return

    # Fallback: use pystray. Note that on Windows this will show "Python"
    # as the source app, but at least the content gets through.
    if icon is not None:
        try:
            icon.notify(message, title)
        except Exception:
            pass


def is_using_native() -> bool:
    """True if the native (custom-app-name) backend is active."""
    return _send is not None
