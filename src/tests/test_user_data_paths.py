"""Regression tests for source and packaged per-user persistence."""

import os
from pathlib import Path
from queue import Queue
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.config.config import LOG_FILE_NAME, PREFS_FILE_NAME
from src.managers.preferences_manager import PreferencesManager
from src.util.paths import get_app_data_dir
from src.workers.gui_wrk import TabbedGUIWorker
from src.workers.process_man import ProcessHandler


class UserDataPathsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.local_app_data = self.root / "Local AppData"
        env_patch = patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.app_dir = self.local_app_data / "Orienta"

    def test_app_data_directory_is_created(self):
        self.assertEqual(get_app_data_dir(), str(self.app_dir))
        self.assertTrue(self.app_dir.is_dir())
        self.assertEqual(get_app_data_dir(), str(self.app_dir))

    def test_missing_localappdata_uses_home_not_working_directory(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            with patch("src.util.paths.os.path.expanduser", return_value=str(self.root)):
                expected = self.root / "AppData" / "Local" / "Orienta"
                self.assertEqual(get_app_data_dir(), str(expected))
                self.assertTrue(expected.is_dir())

    def test_preferences_round_trip_in_app_data(self):
        manager = PreferencesManager()
        self.assertEqual(manager.config_path, str(self.app_dir / PREFS_FILE_NAME))
        self.assertEqual(manager.load(), {})
        preferences = {
            "gui": {"theme": "dark"},
            "orientation": {"alpha_pitch": "0.98"},
        }
        self.assertTrue(manager.save(preferences))
        self.assertEqual(PreferencesManager().load(), preferences)
        self.assertFalse(Path(manager.config_path + ".tmp").exists())

    def test_explicit_preferences_directory_is_preserved(self):
        custom_dir = self.root / "custom"
        manager = PreferencesManager(config_dir=str(custom_dir))
        self.assertEqual(manager.config_path, str(custom_dir / PREFS_FILE_NAME))
        self.assertTrue(manager.set_theme("dark"))
        self.assertEqual(manager.get_theme(), "dark")
        self.assertFalse(self.app_dir.exists())

    def test_frozen_run_does_not_write_to_bundle_or_working_directory(self):
        bundle_dir = self.root / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / PREFS_FILE_NAME).write_text("[gui]\ntheme = dark\n", encoding="utf-8")
        previous_cwd = os.getcwd()
        os.chdir(bundle_dir)
        self.addCleanup(os.chdir, previous_cwd)
        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, "_MEIPASS", str(bundle_dir), create=True):
                manager = PreferencesManager()
                self.assertEqual(manager.load(), {})
                self.assertTrue(manager.set_theme("light"))
                self.run_log_writer()
        self.assertEqual(
            (bundle_dir / PREFS_FILE_NAME).read_text(encoding="utf-8"),
            "[gui]\ntheme = dark\n",
        )
        self.assertFalse((bundle_dir / LOG_FILE_NAME).exists())
        self.assertTrue((self.app_dir / PREFS_FILE_NAME).is_file())
        self.assertTrue((self.app_dir / LOG_FILE_NAME).is_file())

    def test_directory_creation_failure_is_not_silently_ignored(self):
        with patch("src.util.paths.os.makedirs", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                PreferencesManager()

    def test_preferences_save_failure_is_reported(self):
        manager = PreferencesManager()
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with self.assertLogs(level="ERROR") as captured:
                self.assertFalse(manager.set_theme("dark"))
        self.assertIn(manager.config_path, captured.output[0])

    def run_log_writer(self):
        handler = ProcessHandler.__new__(ProcessHandler)
        handler.logQueue = Queue()
        handler.logQueue.put(("INFO", "TestWorker", "AppData log entry"))
        handler.stop_event = Mock()
        handler.stop_event.is_set.side_effect = [False, True]
        handler._log_writer()
        return handler

    def test_log_is_written_and_appended_in_app_data(self):
        self.run_log_writer()
        self.run_log_writer()
        content = (self.app_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
        self.assertEqual(content.count("AppData log entry"), 2)
        self.assertIn("[INFO ", content)
        self.assertIn("[TestWorker", content)

    def test_rotated_logs_stay_in_app_data(self):
        self.app_dir.mkdir(parents=True)
        log_path = self.app_dir / LOG_FILE_NAME
        log_path.write_text("old log", encoding="utf-8")
        with patch("src.workers.process_man.LOG_FILE_MAX_SIZE", 1):
            self.run_log_writer()
        backups = list(self.app_dir.glob("orienta_*.log"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "old log")
        self.assertIn("AppData log entry", log_path.read_text(encoding="utf-8"))

    def test_log_directory_failure_is_reported(self):
        with patch("src.util.paths.os.makedirs", side_effect=PermissionError("denied")):
            with self.assertLogs(level="ERROR") as captured:
                self.run_log_writer()
        self.assertIn("Log writer error", captured.output[0])
        self.assertIn("denied", captured.output[0])

    def test_gui_does_not_report_success_after_failed_save(self):
        gui = SimpleNamespace(
            connection_panel=SimpleNamespace(),
            orientation_panel=SimpleNamespace(),
            preferences_panel=SimpleNamespace(),
            theme_manager=SimpleNamespace(get_current_theme=lambda: "dark"),
            preferences_manager=Mock(),
            log_queue=Queue(),
        )
        gui.preferences_manager.save.return_value = False
        TabbedGUIWorker.save_preferences(gui)
        gui.preferences_manager.save.assert_called_once_with(
            {"gui": {"selected_tab": "0", "theme": "dark"}}
        )
        self.assertEqual(
            gui.log_queue.get_nowait(),
            ("ERROR", "GUI", "Failed to save preferences"),
        )
        self.assertTrue(gui.log_queue.empty())


if __name__ == "__main__":
    unittest.main()
