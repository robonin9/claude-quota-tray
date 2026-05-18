"""
Detect the system colour theme (light/dark) for tray icon rendering.

On Windows we read the registry. On other platforms we fall back to
"light" since pystray's behaviour is less predictable elsewhere.
"""

from __future__ import annotations

import sys


def detect_system_theme() -> str:
    """Return 'light' or 'dark' based on OS preference."""
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            try:
                value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
                return "light" if int(value) == 1 else "dark"
            finally:
                winreg.CloseKey(key)
        except (OSError, ValueError):
            return "light"

    if sys.platform == "darwin":
        try:
            import subprocess
            r = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=2,
            )
            return "dark" if "Dark" in r.stdout else "light"
        except Exception:
            return "light"

    return "light"


def effective_theme(setting: str) -> str:
    """Resolve the 'auto' setting to a concrete theme."""
    if setting in ("light", "dark"):
        return setting
    return detect_system_theme()
