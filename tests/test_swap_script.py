"""The batch file that swaps in a downloaded .exe after the app quits."""

import hashlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _path  # noqa: F401

import updater

EXE = "ClaudeQuotaTray.exe"
NEW = EXE + ".new"


class SwapScriptTextTests(unittest.TestCase):
    def setUp(self):
        self.text = updater.swap_script(NEW, EXE, Path(r"C:\logs\error.log"))

    def test_uses_crlf_only(self):
        self.assertNotIn("\n", self.text.replace("\r\n", ""))

    def test_retries_instead_of_a_fixed_wait(self):
        self.assertNotIn("timeout /t 3", self.text)
        self.assertIn(":retry", self.text)
        self.assertIn("if not errorlevel 1 goto swapped", self.text)
        self.assertIn(f"GEQ {updater.SWAP_TIMEOUT_SECONDS} goto failed", self.text)

    def test_starts_new_version_only_after_swap(self):
        swapped = self.text.index(":swapped")
        failed = self.text.index(":failed")
        start = self.text.index(f'start "" "{EXE}"')
        self.assertTrue(swapped < start < failed)
        self.assertEqual(self.text.count("start "), 1)

    def test_failure_keeps_script_and_logs(self):
        failure = self.text[self.text.index(":failed"):]
        self.assertNotIn('del "%~f0"', failure)
        self.assertIn(r'>>"C:\logs\error.log" echo', failure)

    def test_percent_in_paths_is_escaped(self):
        text = updater.swap_script(NEW, EXE, Path(r"C:\100%\error.log"))
        self.assertIn(r"C:\100%%\error.log", text)

    def test_profile_folder_goes_through_userprofile(self):
        with mock.patch.dict(updater.os.environ, {"USERPROFILE": r"C:\Users\สมชาย"}):
            text = updater.swap_script(
                NEW, EXE, Path(r"C:\Users\สมชาย\.claude-quota-tray\error.log"))
        self.assertIn(r'>>"%USERPROFILE%\.claude-quota-tray\error.log"', text)
        self.assertTrue(text.isascii())

    def test_non_ascii_log_falls_back_next_to_exe(self):
        with mock.patch.dict(updater.os.environ, {"USERPROFILE": r"C:\Users\nia"}):
            text = updater.swap_script(NEW, EXE, Path(r"D:\ข้อมูล\error.log"))
        self.assertIn(f'>>"%~dp0{updater.SWAP_FALLBACK_LOG}"', text)
        self.assertTrue(text.isascii())


@unittest.skipUnless(sys.platform == "win32", "runs cmd.exe")
class SwapScriptRunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "ทดสอบ 100%"
        self.dir.mkdir()
        (self.dir / EXE).write_bytes(b"old")
        (self.dir / NEW).write_bytes(b"new")
        self.log = Path(tmp.name) / "logs 100%" / "error.log"
        self.log.parent.mkdir()

    def _run(self, tries):
        bat = self.dir / updater.SWAP_SCRIPT_NAME
        bat.write_bytes(updater.swap_script(
            NEW, EXE, self.log, tries=tries, launch=False, pause=False,
        ).encode("utf-8"))
        proc = subprocess.run(["cmd", "/c", str(bat)], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=60)
        return proc, bat

    def _lock_exe(self, seconds):
        handle = open(self.dir / EXE, "rb")
        if seconds is None:
            self.addCleanup(handle.close)
            return
        timer = threading.Timer(seconds, handle.close)
        timer.start()
        self.addCleanup(timer.join)

    def test_waits_until_exe_is_released(self):
        self._lock_exe(2.5)
        started = time.monotonic()
        proc, bat = self._run(tries=15)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertGreater(time.monotonic() - started, 2.0)
        self.assertEqual((self.dir / EXE).read_bytes(), b"new")
        self.assertFalse((self.dir / NEW).exists())
        self.assertFalse(bat.exists())
        self.assertFalse(self.log.exists())

    def test_gives_up_keeps_new_file_and_logs(self):
        self._lock_exe(None)
        proc, bat = self._run(tries=2)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual((self.dir / EXE).read_bytes(), b"old")
        self.assertEqual((self.dir / NEW).read_bytes(), b"new")
        self.assertTrue(bat.exists())
        log = self.log.read_text(encoding="utf-8")
        self.assertIn(f"could not replace {EXE}", log)
        self.assertIn(NEW, log)

    def test_non_ascii_log_path_lands_next_to_exe(self):
        self.log = self.dir / "บันทึก" / "error.log"
        self._lock_exe(None)
        proc, _ = self._run(tries=1)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        fallback = self.dir / updater.SWAP_FALLBACK_LOG
        self.assertIn(f"could not replace {EXE}", fallback.read_text(encoding="utf-8"))


class ApplyExeUpdateTests(unittest.TestCase):
    def test_running_exe_writes_swap_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / EXE).write_bytes(b"old")
            payload = b"new exe"
            log = project / "data" / "error.log"
            release = updater.ReleaseInfo(
                version="9.9.9", tag="v9.9.9", html_url="",
                source_zip_url=None,
                exe_url=f"https://github.com/o/r/releases/download/v9.9.9/{EXE}",
                exe_name=EXE, owner="o", repo="r",
            )

            def fake_download(url, dest, timeout=120.0):
                Path(dest).write_bytes(payload)

            with mock.patch.object(updater, "_download", fake_download), \
                    mock.patch.object(updater.sys, "executable", str(project / EXE)), \
                    mock.patch.object(updater.user_settings, "error_log_path",
                                      return_value=log), \
                    mock.patch.object(updater.os, "startfile", create=True) as start:
                msg = updater.apply_exe_update(
                    project, release, {EXE: hashlib.sha256(payload).hexdigest()})

            bat = project / updater.SWAP_SCRIPT_NAME
            self.assertIn("after you quit", msg)
            self.assertEqual(bat.read_bytes(),
                             updater.swap_script(NEW, EXE, log).encode("utf-8"))
            self.assertEqual((project / NEW).read_bytes(), payload)
            if sys.platform == "win32":
                start.assert_called_once_with(bat)


if __name__ == "__main__":
    unittest.main()
