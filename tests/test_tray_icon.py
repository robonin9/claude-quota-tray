"""Tray menu stays live: rebuilt on right-click, values read at build time."""

import sys
import unittest
from unittest import mock

import _path  # noqa: F401

import pystray

import main
import tray_icon


@unittest.skipUnless(sys.platform == "win32", "win32 pystray backend only")
class RebuildOnOpenTests(unittest.TestCase):
    def setUp(self):
        from pystray._util import win32
        self.win32 = win32
        self.calls = []
        # Built without __init__ (no real tray window); give pystray's
        # __del__ the attributes it reads.
        self.icon = tray_icon.TrayIcon.__new__(tray_icon.TrayIcon)
        self.icon._running = False
        self.icon._release_icon = lambda: None
        self.icon._update_menu = lambda: self.calls.append("update")

    def _notify(self, lparam):
        base = lambda _self, _w, _l: self.calls.append("base")
        with mock.patch.object(pystray.Icon, "_on_notify", base):
            self.icon._on_notify(0, lparam)

    def test_override_is_active(self):
        self.assertTrue(tray_icon.REBUILDS_MENU_ON_OPEN)
        self.assertIsNot(tray_icon.TrayIcon._on_notify, pystray.Icon._on_notify)

    def test_right_click_rebuilds_before_showing(self):
        self._notify(self.win32.WM_RBUTTONUP)
        self.assertEqual(self.calls, ["update", "base"])

    def test_left_click_does_not_rebuild(self):
        self._notify(self.win32.WM_LBUTTONUP)
        self.assertEqual(self.calls, ["base"])


class LiveMenuValuesTests(unittest.TestCase):
    def _names(self, menu):
        return [i.text for i in menu.items if i is not pystray.Menu.SEPARATOR]

    def test_account_list_follows_settings(self):
        menu = main._build_account_menu()
        with mock.patch.object(main.accounts, "list_accounts",
                               return_value=[{"id": "a", "name": "Alpha"}]):
            self.assertIn("Alpha", self._names(menu))
        with mock.patch.object(main.accounts, "list_accounts",
                               return_value=[{"id": "a", "name": "Alpha"},
                                             {"id": "b", "name": "Beta"}]):
            self.assertIn("Beta", self._names(menu))

    def test_active_account_check_is_live(self):
        accts = [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}]
        saved = main.state.active_account
        try:
            with mock.patch.object(main.accounts, "list_accounts",
                                   return_value=accts):
                menu = main._build_account_menu()
                main.state.active_account = accts[1]
                checked = {i.text: i.checked for i in menu.items
                           if i is not pystray.Menu.SEPARATOR}
                self.assertTrue(checked["Beta"])
                self.assertFalse(checked["Alpha"])
        finally:
            main.state.active_account = saved

    def test_threshold_radio_is_live(self):
        menu = main._build_settings_menu()
        presets = menu.items[0].submenu

        def checked():
            return [i.checked for i in presets.items
                    if i is not pystray.Menu.SEPARATOR and i.checked is not None]

        with mock.patch.object(main, "_thresholds", return_value=[95]):
            self.assertEqual(checked(), [True, False, False])
        with mock.patch.object(main, "_thresholds", return_value=[80, 95]):
            self.assertEqual(checked(), [False, True, False])


if __name__ == "__main__":
    unittest.main()
