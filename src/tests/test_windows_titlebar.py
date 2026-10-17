"""Tests for synchronizing native title bars with Windows app mode."""

import unittest
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication, QDialog

from src.util.windows_titlebar import apply_windows_titlebar_theme
from src.util.windows_titlebar import install_windows_titlebar_theme_sync


class _Window:
    def winId(self):
        return 1234


class WindowsTitlebarThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_system_dark_mode_sets_dwm_dark_titlebar(self):
        set_dark_mode = Mock()
        with patch(
            "src.util.windows_titlebar._uses_native_windows_platform",
            return_value=True,
        ), patch(
            "src.util.windows_titlebar._read_apps_use_light_theme",
            return_value=False,
        ), patch(
            "src.util.windows_titlebar._set_dwm_title_bar_dark_mode",
            set_dark_mode,
        ):
            self.assertTrue(apply_windows_titlebar_theme(_Window()))

        set_dark_mode.assert_called_once_with(1234, True)

    def test_system_light_mode_sets_dwm_light_titlebar(self):
        set_dark_mode = Mock()
        with patch(
            "src.util.windows_titlebar._uses_native_windows_platform",
            return_value=True,
        ), patch(
            "src.util.windows_titlebar._read_apps_use_light_theme",
            return_value=True,
        ), patch(
            "src.util.windows_titlebar._set_dwm_title_bar_dark_mode",
            set_dark_mode,
        ):
            self.assertTrue(apply_windows_titlebar_theme(_Window()))

        set_dark_mode.assert_called_once_with(1234, False)

    def test_non_windows_platform_does_not_apply_dwm_settings(self):
        set_dark_mode = Mock()
        with patch(
            "src.util.windows_titlebar._uses_native_windows_platform",
            return_value=False,
        ), patch(
            "src.util.windows_titlebar._set_dwm_title_bar_dark_mode",
            set_dark_mode,
        ):
            self.assertFalse(apply_windows_titlebar_theme(_Window()))

        set_dark_mode.assert_not_called()

    def test_dwm_failure_is_reported_to_the_gui_log_callback(self):
        warn = Mock()
        with patch(
            "src.util.windows_titlebar._uses_native_windows_platform",
            return_value=True,
        ), patch(
            "src.util.windows_titlebar._read_apps_use_light_theme",
            return_value=False,
        ), patch(
            "src.util.windows_titlebar._set_dwm_title_bar_dark_mode",
            side_effect=OSError("unsupported titlebar mode"),
        ):
            self.assertFalse(
                apply_windows_titlebar_theme(
                    _Window(),
                    warning_callback=warn,
                )
            )

        warn.assert_called_once()
        self.assertIn("unsupported titlebar mode", warn.call_args.args[0])

    def test_theme_watcher_updates_open_windows_when_windows_mode_changes(self):
        window = QDialog()
        self.addCleanup(window.close)
        read_theme = Mock(side_effect=(False, True))
        apply_theme = Mock(return_value=True)

        with patch(
            "src.util.windows_titlebar._uses_native_windows_platform",
            return_value=True,
        ), patch(
            "src.util.windows_titlebar._read_apps_use_light_theme",
            read_theme,
        ), patch(
            "src.util.windows_titlebar.apply_windows_titlebar_theme",
            apply_theme,
        ):
            timer = install_windows_titlebar_theme_sync(self.app)
            self.addCleanup(timer.stop)
            self.addCleanup(
                lambda: delattr(self.app, "_windows_titlebar_theme_timer")
                if hasattr(self.app, "_windows_titlebar_theme_timer")
                else None
            )
            timer.timeout.emit()

        self.assertTrue(any(
            call.args[0] is window and call.kwargs["is_light"] is False
            for call in apply_theme.call_args_list
        ))
        self.assertTrue(any(
            call.args[0] is window and call.kwargs["is_light"] is True
            for call in apply_theme.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
