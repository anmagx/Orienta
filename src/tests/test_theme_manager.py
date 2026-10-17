"""Tests for available application themes and Solarized stylesheet layering."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QGroupBox,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from src.managers.theme_manager import THEME_OPTIONS, ThemeManager
from src.workers.gui_qt.panels.preferences_panel import PreferencesPanel
from src.workers.gui_qt.panels.visualization_widget import (
    OrientationVisualizationWidget,
)


class ThemeManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_solarized_themes_layer_their_palette_over_complete_base_styles(self):
        manager = ThemeManager(self.app)
        window = QMainWindow()
        content = QWidget()
        content.setObjectName("nativeOutputSettingsContent")
        group = QGroupBox("Frame")
        try:
            for theme, window_color, content_color, frame_color, accent in (
                (
                    "solarized_dark",
                    "#002b36",
                    "#073642",
                    "#073642",
                    "#268bd2",
                ),
                (
                    "solarized_light",
                    "#fdf6e3",
                    "#fdf6e3",
                    "#eee8d5",
                    "#268bd2",
                ),
            ):
                with self.subTest(theme=theme):
                    manager.load_theme(theme)
                    self.app.processEvents()
                    stylesheet = self.app.styleSheet()
                    self.assertIn("QMainWindow", stylesheet)
                    self.assertIn("QComboBox", stylesheet)
                    self.assertIn(accent, stylesheet)
                    window.ensurePolished()
                    content.ensurePolished()
                    group.ensurePolished()
                    self.assertEqual(
                        window.palette().color(QPalette.Window).name(),
                        window_color,
                    )
                    self.assertEqual(
                        content.palette().color(QPalette.Window).name(),
                        content_color,
                    )
                    self.assertEqual(
                        group.palette().color(QPalette.Window).name(),
                        frame_color,
                    )
                    self.assertEqual(manager.get_current_theme(), theme)
        finally:
            window.close()
            content.close()
            group.close()

    def test_preferences_selector_uses_friendly_names_and_theme_ids(self):
        panel = PreferencesPanel(preferences_manager=Mock())
        self.addCleanup(panel.close)
        changed = []
        panel.theme_changed.connect(changed.append)

        self.assertEqual(
            [panel.theme_combo.itemText(i) for i in range(panel.theme_combo.count())],
            [option[1] for option in THEME_OPTIONS],
        )
        for theme, label in THEME_OPTIONS:
            index = panel.theme_combo.findData(theme)
            self.assertGreaterEqual(index, 0)
            self.assertEqual(panel.theme_combo.itemText(index), label)

        solarized_index = panel.theme_combo.findData("solarized_dark")
        panel.theme_combo.setCurrentIndex(solarized_index)
        self.assertEqual(changed[-1], "solarized_dark")

    def test_orientation_visualization_uses_each_theme_background_palette(self):
        manager = ThemeManager(self.app)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        widget = OrientationVisualizationWidget(host)
        widget.setFixedSize(160, 160)
        host_layout.addWidget(widget)
        self.addCleanup(host.close)

        for theme, expected_background, expected_text in (
            ("dark", "#3c3c3c", "#c8c8c8"),
            ("light", "#ffffff", "#323232"),
            ("solarized_dark", "#002b36", "#c8c8c8"),
            ("solarized_light", "#eee8d5", "#323232"),
        ):
            with self.subTest(theme=theme):
                manager.load_theme(theme)
                widget.ensurePolished()
                self.assertEqual(
                    widget.palette().color(QPalette.Window).name(),
                    expected_background,
                )
                self.assertEqual(
                    widget._get_theme_colors()["text"].name(),
                    expected_text,
                )
                host.show()
                self.app.processEvents()
                self.assertEqual(
                    widget.grab().toImage().pixelColor(80, 0).name(),
                    expected_background,
                )

    def test_toggle_switches_between_matching_light_and_dark_variants(self):
        manager = ThemeManager(self.app)
        with patch.object(manager, "load_theme") as load_theme:
            manager.current_theme = "solarized_light"
            self.assertEqual(manager.toggle_theme(), "solarized_dark")
            load_theme.assert_called_once_with("solarized_dark")


if __name__ == "__main__":
    unittest.main()
