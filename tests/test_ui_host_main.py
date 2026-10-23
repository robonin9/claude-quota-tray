"""ui_host.run_forever(on_ready=...) on the main thread — the macOS layout.

Runs on every OS (the unittest main thread is a valid Tk thread everywhere).
"""

import sys
import threading
import unittest
from unittest import mock

import _path  # noqa: F401

import ui_host


class RunForeverOnMainThreadTests(unittest.TestCase):
    def setUp(self):
        if ui_host._started:
            ui_host._reset_for_tests()

    def tearDown(self):
        if ui_host._root is None and ui_host._started:
            ui_host._reset_for_tests()

    def test_on_ready_runs_on_the_calling_thread_then_queued_work(self):
        events = []
        worker_result = {}

        def worker():
            # Queued before/while Tk starts: must run after on_ready, not be dropped.
            worker_result["value"] = ui_host.call_sync(lambda: "from worker", timeout=10)
            ui_host.call(lambda: events.append("queued"))
            ui_host.call(ui_host.shutdown)

        def on_ready():
            events.append(("on_ready", threading.current_thread() is threading.main_thread(),
                           ui_host.is_ui_thread()))
            threading.Thread(target=worker, daemon=True).start()

        try:
            ui_host.run_forever(on_ready=on_ready)
        except RuntimeError as e:  # pragma: no cover
            self.skipTest(f"Tk not available: {e}")
        if not events:
            self.skipTest("Tk not available (no display?)")
        self.assertEqual(events[0], ("on_ready", True, True))
        self.assertIn("queued", events)
        self.assertEqual(worker_result["value"], "from worker")
        self.assertFalse(ui_host.is_alive())

    def test_calls_before_tk_is_ready_are_kept(self):
        # Mark the host as starting (as run_forever does) without Tk yet:
        # call() must queue instead of refusing or starting a thread.
        ui_host._started = True
        try:
            self.assertTrue(ui_host.call(lambda: None))
        finally:
            ui_host._release_pending("test")
            ui_host._started = False

    def test_no_background_thread_on_macos(self):
        # On macOS call() must never start a Tk thread by itself.
        with mock.patch.object(ui_host, "_AUTO_START", False), \
                mock.patch.object(ui_host, "start") as start:
            self.assertFalse(ui_host.call(lambda: None))
            start.assert_not_called()

    def test_auto_start_is_off_on_macos(self):
        self.assertEqual(ui_host._AUTO_START, sys.platform != "darwin")


if __name__ == "__main__":
    unittest.main()
