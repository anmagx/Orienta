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
            "native_output": {
                "curves": (
                    '{"yaw":[[0,0],[45,90],[90,180]],'
                    '"pitch":[[0,0],[45,90],[90,180]],'
                    '"roll":[[0,0],[45,90],[90,180]]}'
                ),
                "input_limit_yaw_deg": "37.5",
                "input_limit_custom_yaw": "true",
                "input_limit_pitch_deg": "45",
                "input_limit_custom_pitch": "false",
                "filter_enabled": "true",
                "filter_min_cutoff_hz": "1.0",
                "filter_beta": "0.03",
                "filter_derivative_cutoff_hz": "1.0",
                "offset_yaw_deg": "2.0",
                "offset_x_mm": "12.5",
            },
        }
        self.assertTrue(manager.save(preferences))
        self.assertEqual(PreferencesManager().load(), preferences)
        self.assertFalse(Path(manager.config_path + ".tmp").exists())

    def test_generic_preference_methods_use_gui_section_and_preserve_sections(self):
        manager = PreferencesManager(config_dir=str(self.root / "manager"))
        initial = {
            "gui": {"theme": "dark"},
            "native_output": {"filter_beta": "0.03"},
            "network": {"udp_port": "4243"},
        }
        self.assertTrue(manager.save(initial))
        self.assertTrue(manager.set("custom_value", "kept"))
        self.assertEqual(manager.get("custom_value"), "kept")
        self.assertTrue(manager.update({"another_value": "also kept"}))
        self.assertTrue(manager.delete("custom_value"))
        loaded = manager.load()
        self.assertNotIn("custom_value", loaded["gui"])
        self.assertEqual(loaded["gui"]["another_value"], "also kept")
        self.assertEqual(loaded["native_output"], initial["native_output"])
        self.assertEqual(loaded["network"], initial["network"])

    def test_failed_atomic_replace_preserves_existing_preferences(self):
        manager = PreferencesManager(config_dir=str(self.root / "atomic"))
        existing = {
            "gui": {"theme": "dark"},
            "native_output": {"filter_beta": "0.03"},
        }
        self.assertTrue(manager.save(existing))
        with patch(
            "src.managers.preferences_manager.os.replace",
            side_effect=PermissionError("locked"),
        ):
            with self.assertLogs(level="ERROR"):
                self.assertFalse(manager.save({"gui": {"theme": "light"}}))
        self.assertEqual(manager.load(), existing)
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
