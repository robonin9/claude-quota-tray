"""CQT_DATA_DIR is honoured at call time, even when set after import."""

import os
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401

import history
import settings as user_settings
import token_store


class DataDirTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get(user_settings.DATA_DIR_ENV)

    def tearDown(self):
        if self._env is None:
            os.environ.pop(user_settings.DATA_DIR_ENV, None)
        else:
            os.environ[user_settings.DATA_DIR_ENV] = self._env

    def test_env_set_after_import_moves_every_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[user_settings.DATA_DIR_ENV] = tmp
            root = Path(tmp)
            self.assertEqual(user_settings.data_dir(), root)
            self.assertEqual(user_settings.settings_path(), root / "settings.json")
            self.assertEqual(user_settings.error_log_path(), root / "error.log")
            self.assertEqual(history.db_path(), root / "history.db")
            self.assertEqual(token_store._file_path(), root / "token")

    def test_default_is_home_folder(self):
        os.environ.pop(user_settings.DATA_DIR_ENV, None)
        self.assertEqual(user_settings.data_dir(), user_settings.SETTINGS_DIR)
        self.assertEqual(user_settings.settings_path(), user_settings.SETTINGS_PATH)

    def test_load_and_save_use_the_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[user_settings.DATA_DIR_ENV] = tmp
            saved = user_settings._cache
            user_settings._cache = None
            try:
                user_settings.load()
                self.assertTrue((Path(tmp) / "settings.json").is_file())
            finally:
                user_settings._cache = saved


if __name__ == "__main__":
    unittest.main()
