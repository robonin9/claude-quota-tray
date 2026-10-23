"""New app instances must not inherit the old .exe's PyInstaller state."""

import os
import unittest
from unittest import mock

import _path  # noqa: F401

import app_paths
import main


class NewInstanceEnvTests(unittest.TestCase):
    def test_sets_reset_flag_and_keeps_the_rest(self):
        with mock.patch.dict(os.environ, {"CQT_PROBE": "x"}):
            env = app_paths.new_instance_env()
        self.assertEqual(env[app_paths.PYI_RESET_ENV], "1")
        self.assertEqual(env["CQT_PROBE"], "x")
        self.assertNotIn(app_paths.PYI_RESET_ENV, os.environ)


class RestartTests(unittest.TestCase):
    def test_frozen_restart_resets_pyinstaller_env(self):
        with mock.patch.object(main.sys, "frozen", True, create=True), \
                mock.patch.object(main.subprocess, "Popen") as popen, \
                mock.patch.object(main.time, "sleep"), \
                mock.patch.object(main, "_stop_tray"), \
                mock.patch.object(main.state.stop, "set"), \
                mock.patch.object(main.state.force_refresh, "set"):
            main._restart_app(icon=None)
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env[app_paths.PYI_RESET_ENV], "1")


if __name__ == "__main__":
    unittest.main()
