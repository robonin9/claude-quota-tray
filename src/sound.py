"""
Cross-platform alert sound. Best-effort, silent on failure.
"""

from __future__ import annotations

import sys


def play_alert() -> None:
    if sys.platform == "win32":
        try:
            import winsound  # type: ignore
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
        except Exception:
            pass
    # POSIX: try the terminal bell as a last resort
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass
