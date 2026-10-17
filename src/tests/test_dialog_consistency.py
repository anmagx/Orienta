"""Regression tests for shared dialog surfaces, margins and Close actions."""

import os
import unittest
from unittest.mock import Mock
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QPushButton,
    QWidget,
)

from src.workers.gui_qt.panels.about_panel import AboutPanel
from src.workers.gui_qt.panels.dialog_utils import (
    configure_dialog,
    create_close_button,
)
from src.workers.gui_qt.panels.message_panel import MessagePanelQt
from src.workers.gui_qt.panels.native_output_settings_dialog import (
    NativeOutputSettingsDialog,
)
from src.workers.gui_qt.panels.orientation_panel import OrientationPanelQt
from src.workers.gui_qt.panels.preferences_panel import PreferencesPanel
from src.workers.gui_qt.panels.sensor_settings_dialog import SensorSettingsDialog
from src.managers.theme_manager import ThemeManager


class DialogConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_dialogs_share_shell_margins_and_close_actions(self):
        dialogs = (
            NativeOutputSettingsDialog(),
            SensorSettingsDialog(Mock(return_value=True)),
        )
        for dialog in dialogs:
            with self.subTest(dialog=dialog.windowTitle()):
                dialog.show()
                self.app.processEvents()
                self.assertEqual(dialog.layout().getContentsMargins(), (6, 6, 6, 6))
                self.assertEqual(dialog.layout().spacing(), 6)
                self.assertEqual(dialog.close_button.text(), "Close")
                dialog.close_button.click()
                self.app.processEvents()
                self.assertFalse(dialog.isVisible())
                dialog.close()

    def test_about_panel_uses_titled_frames_and_shared_padding(self):
        panel = AboutPanel()
        self.addCleanup(panel.close)
        groups = {
            group.title(): group
            for group in panel.findChildren(QGroupBox)
        }
        self.assertEqual(
            set(groups),
            {"About Orienta", "Third-party software"},
        )
        self.assertEqual(panel.layout().getContentsMargins(), (6, 6, 6, 6))
        self.assertEqual(panel.layout().spacing(), 6)
        for group in groups.values():
            self.assertEqual(group.layout().getContentsMargins(), (6, 6, 6, 6))
            self.assertEqual(group.layout().spacing(), 6)

    def test_preferences_sections_share_frame_padding_and_have_close_action(self):
        panel = PreferencesPanel()
        self.addCleanup(panel.close)

        groups = panel.findChildren(QGroupBox)
        self.assertGreater(len(groups), 5)
        for group in groups:
            with self.subTest(group=group.title()):
                self.assertEqual(
                    group.layout().getContentsMargins(),
                    (6, 6, 6, 6),
                )
                self.assertEqual(group.layout().spacing(), 6)
        self.assertEqual(panel.reset_btn.text(), "Reset to Defaults")
        self.assertEqual(panel.close_btn.text(), "Close")
        footer = panel.layout().itemAt(1).layout()
        self.assertEqual(footer.getContentsMargins(), (0, 0, 0, 0))
        self.assertEqual(footer.spacing(), 6)

    def test_message_panel_surface_and_text_views_follow_both_themes(self):
        previous_stylesheet = self.app.styleSheet()
        panel = MessagePanelQt(None)
        self.addCleanup(panel.close)
        try:
            for theme, panel_color, text_color in (
                ("dark", "#3c3c3c", "#3c3c3c"),
                ("light", "#ffffff", "#ffffff"),
                ("solarized_dark", "#073642", "#002b36"),
                ("solarized_light", "#fdf6e3", "#fdf6e3"),
            ):
                ThemeManager(self.app).load_theme(theme)
                self.app.processEvents()
                for widget, expected_color in (
                    (panel, panel_color),
                    (panel.serial_text, text_color),
                    (panel.message_text, text_color),
                ):
                    widget.ensurePolished()
                    self.assertEqual(
                        widget.palette().color(QPalette.Window).name(),
                        expected_color,
                    )
                self.assertEqual(
                    panel.layout().getContentsMargins(),
                    (6, 6, 6, 6),
                )
                self.assertEqual(panel.layout().spacing(), 6)
                for group in panel.findChildren(QGroupBox):
                    self.assertEqual(
                        group.layout().getContentsMargins(),
                        (6, 6, 6, 6),
                    )
                    self.assertEqual(group.layout().spacing(), 6)
        finally:
            self.app.setStyleSheet(previous_stylesheet)

    def test_support_dialogs_share_shell_and_have_close_buttons(self):
        panel = OrientationPanelQt()
        self.addCleanup(panel.close)
        for key, opener in (
            ("monitor", panel._open_monitor_window),
            ("about", panel._open_about_window),
        ):
            with self.subTest(dialog=key):
                opener()
                dialog = panel._support_dialogs[key]
                self.addCleanup(dialog.close)
                self.app.processEvents()
                self.assertEqual(
                    dialog.layout().getContentsMargins(),
                    (6, 6, 6, 6),
                )
                self.assertEqual(dialog.layout().spacing(), 6)
                close_button = next(
                    button
                    for button in dialog.findChildren(QPushButton)
                    if button.text() == "Close"
                )
                close_button.click()
                self.app.processEvents()
                self.assertFalse(dialog.isVisible())

    def test_visualization_settings_frame_has_the_standard_inset(self):
        panel = OrientationPanelQt()
        self.addCleanup(panel.close)
        observed = []

        def inspect_dialog(dialog):
            content = dialog.findChild(QWidget, "visualizationSettingsContent")
            group = next(
                group for group in dialog.findChildren(QGroupBox)
                if group.title() == "Popout Settings"
            )
            observed.append((
                dialog.layout().getContentsMargins(),
                content.layout().getContentsMargins(),
                group.layout().getContentsMargins(),
                group.parentWidget() is content,
            ))

        class InspectableDialog(QDialog):
            def exec_(self):
                inspect_dialog(self)
                return QDialog.Accepted

        with patch(
            "src.workers.gui_qt.panels.orientation_panel.QDialog",
            InspectableDialog,
        ):
            panel._open_viz_settings()

        self.assertEqual(
            observed,
            [((6, 6, 6, 6), (6, 6, 6, 6), (6, 6, 6, 6), True)],
        )

    def test_visualization_popout_surface_has_no_frame_border(self):
        panel = OrientationPanelQt()
        self.addCleanup(panel.close)
        manager = ThemeManager(self.app)

        for theme, expected_surface in (
            ("dark", "#3c3c3c"),
            ("solarized_dark", "#073642"),
        ):
            with self.subTest(theme=theme):
                manager.load_theme(theme)
                popup = panel._create_popup_window()[0]
                self.assertIsNotNone(popup)
                self.addCleanup(popup.close)
                self.app.processEvents()
                self.assertEqual(
                    popup.objectName(),
                    "visualizationPopupWindow",
                )
                image = popup.grab().toImage()
                self.assertEqual(
                    image.pixelColor(image.width() // 2, 0).name(),
                    expected_surface,
                )

    def test_dialog_shell_helper_applies_shared_style_and_close_button(self):
        dialog = QDialog()
        layout = configure_dialog(dialog, "testDialog", "Test dialog")
        close_button = create_close_button(dialog)
        layout.addWidget(close_button)
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.objectName(), "testDialog")
        self.assertEqual(dialog.windowTitle(), "Test dialog")
        self.assertTrue(dialog.testAttribute(Qt.WA_StyledBackground))
        self.assertEqual(layout.getContentsMargins(), (6, 6, 6, 6))
        self.assertEqual(layout.spacing(), 6)


if __name__ == "__main__":
    unittest.main()
