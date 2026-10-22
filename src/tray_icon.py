"""
pystray Icon whose native menu is rebuilt every time it is opened.

pystray's win32 backend builds the HMENU only in ``_update_menu()`` and shows
that cached handle on right-click, so callables in the menu (live % text,
checked radios, the account list) are not re-evaluated unless someone calls
``update_menu()`` — which is only safe on pystray's own thread. Rebuilding on
WM_RBUTTONUP runs on exactly that thread, right before the menu is shown.

This relies on pystray internals (``_on_notify``, ``_update_menu``), pinned in
requirements.txt; if they are missing we fall back to plain pystray.Icon.
"""

from __future__ import annotations

import sys

import pystray

REBUILDS_MENU_ON_OPEN = False

TrayIcon = pystray.Icon

if sys.platform == "win32":
    try:
        from pystray._util import win32 as _win32
    except ImportError:  # pragma: no cover — pystray layout changed
        _win32 = None

    if (
        _win32 is not None
        and hasattr(_win32, "WM_RBUTTONUP")
        and hasattr(pystray.Icon, "_on_notify")
        and hasattr(pystray.Icon, "_update_menu")
    ):
        class TrayIcon(pystray.Icon):  # type: ignore[no-redef]
            def _on_notify(self, wparam, lparam):
                if lparam == _win32.WM_RBUTTONUP:
                    try:
                        self._update_menu()
                    except Exception:
                        sys.stderr.write("[tray_icon] menu rebuild failed\n")
                return super()._on_notify(wparam, lparam)

        REBUILDS_MENU_ON_OPEN = True
    else:  # pragma: no cover
        sys.stderr.write(
            "[tray_icon] pystray internals changed; tray menu will not "
            "refresh on open\n"
        )
