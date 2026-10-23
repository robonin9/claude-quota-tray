"""ui_host: one Tk root, one UI thread, safe cross-thread calls.

The host is started with run_forever() on a thread the test owns, which is
how a platform that needs Tk on the main thread would drive it.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import tkinter
import unittest

import _path  # noqa: F401

import history
import settings as user_settings
import ui_host

_tk_inits = []
_orig_tk_init = tkinter.Tk.__init__
_ui_thread = None
_tmp = None
_saved = {}


def _counting_init(self, *args, **kwargs):
    _tk_inits.append(threading.current_thread().name)
    _orig_tk_init(self, *args, **kwargs)


def setUpModule():
    global _ui_thread, _tmp
    if sys.platform == "darwin":
        # These tests host Tk on a worker thread, which AppKit aborts on.
        # macOS is covered by test_ui_host_main.py and `main.py --self-test`.
        raise unittest.SkipTest("Tk must own the main thread on macOS")
    if ui_host._started:
        raise unittest.SkipTest("ui_host already started by another test")
    _tmp = tempfile.mkdtemp(prefix="cqt-test-")
    _saved.update(
        env=os.environ.get(user_settings.DATA_DIR_ENV),
        cache=user_settings._cache, conn=history._conn,
    )
    os.environ[user_settings.DATA_DIR_ENV] = _tmp
    user_settings._cache = None
    history._conn = None

    tkinter.Tk.__init__ = _counting_init
    _ui_thread = threading.Thread(
        target=ui_host.run_forever, name="test-ui-main", daemon=True)
    _ui_thread.start()
    ui_host._ready.wait(timeout=10)
    if not ui_host.is_alive():
        tkinter.Tk.__init__ = _orig_tk_init
        raise unittest.SkipTest("Tk is not available (no display?)")


def tearDownModule():
    tkinter.Tk.__init__ = _orig_tk_init
    if ui_host.is_alive():
        ui_host.shutdown()
    if _ui_thread is not None:
        _ui_thread.join(timeout=5)
    ui_host._reset_for_tests()
    if history._conn is not None:
        history._conn.close()
    if _saved["env"] is None:
        os.environ.pop(user_settings.DATA_DIR_ENV, None)
    else:
        os.environ[user_settings.DATA_DIR_ENV] = _saved["env"]
    user_settings._cache = _saved["cache"]
    history._conn = _saved["conn"]
    shutil.rmtree(_tmp, ignore_errors=True)


def _settle():
    """Let queued work and the windows' first after() callbacks run."""
    for _ in range(3):
        ui_host.call_sync(lambda: ui_host.root().update())
        time.sleep(0.15)


class CallTests(unittest.TestCase):
    def test_call_sync_returns_value_from_other_thread(self):
        self.assertEqual(ui_host.call_sync(lambda: 6 * 7), 42)

    def test_call_sync_reraises(self):
        def boom():
            raise ValueError("nope")
        with self.assertRaises(ValueError):
            ui_host.call_sync(boom)

    def test_call_sync_on_ui_thread_does_not_deadlock(self):
        nested = lambda: ui_host.call_sync(lambda: "inner", timeout=1)
        self.assertEqual(ui_host.call_sync(nested, timeout=3), "inner")

    def test_is_ui_thread_only_on_the_run_forever_thread(self):
        self.assertFalse(ui_host.is_ui_thread())
        self.assertTrue(ui_host.call_sync(ui_host.is_ui_thread))
        name = ui_host.call_sync(lambda: threading.current_thread().name)
        self.assertEqual(name, "test-ui-main")

    def test_root_refuses_other_threads(self):
        with self.assertRaises(RuntimeError):
            ui_host.root()

    def test_fire_and_forget_call_runs(self):
        done = threading.Event()
        self.assertTrue(ui_host.call(done.set))
        self.assertTrue(done.wait(timeout=3))


class SingleTkTests(unittest.TestCase):
    def test_every_window_shares_one_tk(self):
        import desktop_widget
        import history_window
        import settings_dialogs
        import status_window

        self.assertTrue(status_window.show("Test", lambda: {"session_pct": 42}))
        history_window.show("acct", "Test", get_data=lambda: {})
        settings_dialogs.open_thresholds(lambda: None)
        settings_dialogs.open_accounts(lambda: None)
        settings_dialogs.open_connect_claude(lambda: None)
        if desktop_widget.is_supported():
            desktop_widget.show(lambda: {"session_pct": 10}, lambda: None)
        _settle()

        def _toplevels():
            return sum(isinstance(w, tkinter.Toplevel)
                       for w in ui_host.root().winfo_children())

        self.assertGreaterEqual(ui_host.call_sync(_toplevels), 5)
        self.assertEqual(len(_tk_inits), 1, _tk_inits)
        self.assertEqual(_tk_inits, ["test-ui-main"])

        # Showing again focuses instead of opening duplicates.
        before = ui_host.call_sync(_toplevels)
        status_window.show("Test", lambda: {})
        settings_dialogs.open_thresholds(lambda: None)
        _settle()
        self.assertEqual(ui_host.call_sync(_toplevels), before)

        def _close_all():
            for w in list(ui_host.root().winfo_children()):
                w.destroy()

        ui_host.call_sync(_close_all)
        desktop_widget.hide()
        _settle()
        self.assertEqual(ui_host.call_sync(_toplevels), 0)
        # Closed windows can be opened again (stale handles are dropped).
        self.assertTrue(status_window.show("Test", lambda: {}))
        _settle()
        self.assertEqual(ui_host.call_sync(_toplevels), 1)
        ui_host.call_sync(_close_all)
        self.assertEqual(len(_tk_inits), 1)


if __name__ == "__main__":
    unittest.main()
