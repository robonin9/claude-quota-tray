"""self_test helpers that must work inside a --windowed .exe (no stderr)."""

import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401

import self_test
import settings as user_settings


class HungWatchdogTests(unittest.TestCase):
    def test_exits_2_without_stderr(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {user_settings.DATA_DIR_ENV: tmp}), \
                mock.patch.object(sys, "stderr", None), \
                mock.patch.object(sys, "__stderr__", None), \
                mock.patch.object(self_test.os, "_exit") as exit_:
            self_test._hung()
            exit_.assert_called_once_with(2)
            log = open(os.path.join(tmp, "error.log"), encoding="utf-8").read()
            self.assertIn("hung", log)

    def test_writes_to_stderr_when_present(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {user_settings.DATA_DIR_ENV: tmp}), \
                mock.patch.object(sys, "stderr", buf), \
                mock.patch.object(self_test.os, "_exit") as exit_:
            self_test._hung()
        exit_.assert_called_once_with(2)
        self.assertIn("hung", buf.getvalue())


class OptionalStepTests(unittest.TestCase):
    """A missing tray (CI runner) warns; other failures still fail the run."""

    def _run_checks(self, tray_error=None):
        app = mock.Mock()
        ui = mock.Mock()
        ui._ready.wait.return_value = True
        ui.is_alive.return_value = True
        result = {"problems": [], "lines": []}
        patches = {
            "_fake_state": mock.DEFAULT, "_walk_menu": mock.DEFAULT,
            "_open_windows": mock.DEFAULT, "_settle": mock.DEFAULT,
            "_count_windows": mock.DEFAULT, "_screenshot": mock.DEFAULT,
            "_one_tk": mock.DEFAULT, "_close_windows": mock.DEFAULT,
            "_stop_tray": mock.DEFAULT,
        }
        with mock.patch.multiple(self_test, **patches) as m:
            for fn in m.values():
                fn.return_value = None
            tray = mock.Mock(side_effect=tray_error, return_value="TrayIcon")
            with mock.patch.object(self_test, "_tray", tray):
                self_test._checks(app, ui, ["MainThread"], result)
        return result

    def test_tray_failure_is_a_warning(self):
        result = self._run_checks(tray_error=RuntimeError("no notification area"))
        self.assertEqual(result["problems"], [])
        self.assertTrue(any(l.startswith("[WARN] tray icon") for l in result["lines"]))

    def test_all_ok(self):
        result = self._run_checks()
        self.assertEqual(result["problems"], [])
        self.assertFalse(any(l.startswith("[WARN]") for l in result["lines"]))


if __name__ == "__main__":
    unittest.main()
