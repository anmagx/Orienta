"""Tests for the runtime sensor settings protocol and dialog."""

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)

from src.util.sensor_settings import (
    DEFAULT_SENSOR_SETTINGS,
    normalize_sensor_settings,
    parse_sensor_settings_response,
    sensor_settings_command,
)
from src.workers.gui_qt.panels.sensor_settings_dialog import SensorSettingsDialog
from src.managers.theme_manager import ThemeManager


class SensorSettingsProtocolTests(unittest.TestCase):
    def test_get_command_uses_the_firmware_protocol(self):
        self.assertEqual(sensor_settings_command("GET"), b"@SENSOR,GET\n")

    def test_set_command_serializes_all_settings_in_protocol_order(self):
        settings = dict(DEFAULT_SENSOR_SETTINGS)
        settings.update({
            "gyro_range": 500,
            "accel_range": 4,
            "sensor_rate": 500,
            "gyro_lpf": 98,
            "accel_lpf": 92,
            "output_rate": 250,
        })
        self.assertEqual(
            sensor_settings_command("SET", settings),
            b"@SENSOR,SET,500,4,500,98,92,250\n",
        )

    def test_set_command_rejects_output_rate_above_sensor_rate(self):
        settings = dict(DEFAULT_SENSOR_SETTINGS)
        settings["sensor_rate"] = 200
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            sensor_settings_command("SET", settings)

    def test_set_command_rejects_invalid_presets_instead_of_defaulting(self):
        settings = dict(DEFAULT_SENSOR_SETTINGS)
        settings["gyro_range"] = 999
        with self.assertRaisesRegex(ValueError, "Unsupported gyro_range"):
            sensor_settings_command("SET", settings)

    def test_invalid_saved_values_fall_back_to_presets(self):
        settings = normalize_sensor_settings({
            "gyro_range": "999",
            "output_rate": "invalid",
        })
        self.assertEqual(settings["gyro_range"], DEFAULT_SENSOR_SETTINGS["gyro_range"])
        self.assertEqual(settings["output_rate"], DEFAULT_SENSOR_SETTINGS["output_rate"])

    def test_settings_response_is_parsed_and_validated(self):
        response = (
            "!SENSOR,SETTINGS,gyro_range=500,accel_range=4,sensor_rate=500,"
            "gyro_lpf=98,accel_lpf=92,output_rate=250"
        )
        self.assertEqual(
            parse_sensor_settings_response(response),
            {
                "gyro_range": 500,
                "accel_range": 4,
                "sensor_rate": 500,
                "gyro_lpf": 98,
                "accel_lpf": 92,
                "output_rate": 250,
            },
        )

    def test_settings_response_rejects_malformed_and_error_replies(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            parse_sensor_settings_response("!SENSOR,SETTINGS,gyro_range=1000")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            parse_sensor_settings_response("!SENSOR,ERROR,Unsupported setting preset")

    def test_serial_worker_writes_validated_commands(self):
        from src.workers.serial_wrk import _send_sensor_command

        serial_port = Mock()
        _send_sensor_command(
            serial_port,
            ("sensor_command", "GET"),
            None,
            None,
        )
        serial_port.write.assert_called_once_with(b"@SENSOR,GET\n")


class SensorSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_sends_apply_and_uses_device_report_for_actual_state(self):
        send_command = Mock(return_value=True)
        dialog = SensorSettingsDialog(send_command, connected=True)
        self.addCleanup(dialog.close)

        dialog.combos["gyro_range"].setCurrentIndex(
            dialog.combos["gyro_range"].findData(500)
        )
        dialog.combos["sensor_rate"].setCurrentIndex(
            dialog.combos["sensor_rate"].findData(500)
        )
        dialog.combos["gyro_lpf"].setCurrentIndex(
            dialog.combos["gyro_lpf"].findData(98)
        )
        dialog.combos["accel_range"].setCurrentIndex(
            dialog.combos["accel_range"].findData(4)
        )
        dialog.combos["accel_lpf"].setCurrentIndex(
            dialog.combos["accel_lpf"].findData(92)
        )
        dialog.apply_settings()
        requested = {
            "gyro_range": 500,
            "accel_range": 4,
            "sensor_rate": 500,
            "gyro_lpf": 98,
            "accel_lpf": 92,
            "output_rate": 250,
        }
        send_command.assert_called_once_with(("SET", requested))

        dialog.handle_device_response(
            "!SENSOR,SETTINGS,gyro_range=500,accel_range=4,sensor_rate=500,"
            "gyro_lpf=98,accel_lpf=92,output_rate=250"
        )
        self.assertIn("confirmed", dialog.status_label.text())
        self.assertIn("sensor 500 Hz", dialog.actual_settings_label.text())

    def test_dialog_keeps_device_state_separate_from_saved_selection(self):
        send_command = Mock(return_value=True)
        dialog = SensorSettingsDialog(
            send_command,
            preferences={"gyro_range": 500},
            connected=True,
        )
        self.addCleanup(dialog.close)
        dialog.request_settings()
        dialog.handle_device_response(
            "!SENSOR,SETTINGS,gyro_range=1000,accel_range=2,sensor_rate=1000,"
            "gyro_lpf=42,accel_lpf=41,output_rate=250"
        )
        self.assertEqual(dialog.combos["gyro_range"].currentData(), 500)
        self.assertIn("Gyro ±1000", dialog.actual_settings_label.text())

    def test_dialog_background_follows_light_and_dark_themes(self):
        app = QApplication.instance()
        previous_stylesheet = app.styleSheet()
        dialog = SensorSettingsDialog(Mock(return_value=True))
        self.addCleanup(dialog.close)
        try:
            for theme, expected_color in (
                ("dark", "#3c3c3c"),
                ("light", "#ffffff"),
                ("solarized_dark", "#073642"),
                ("solarized_light", "#fdf6e3"),
            ):
                ThemeManager(app).load_theme(theme)
                dialog.ensurePolished()
                content = dialog.findChild(QWidget, "sensorSettingsContent")
                self.assertIsNotNone(content)
                content.ensurePolished()
                self.assertEqual(
                    dialog.palette().color(QPalette.Window).name(),
                    expected_color,
                )
                self.assertEqual(
                    content.palette().color(QPalette.Window).name(),
                    expected_color,
                )
        finally:
            app.setStyleSheet(previous_stylesheet)

    def test_sensor_controls_are_contained_in_heading_group_frames(self):
        dialog = SensorSettingsDialog(Mock(return_value=True))
        self.addCleanup(dialog.close)

        groups = {
            group.title(): group
            for group in dialog.findChildren(QGroupBox)
        }
        self.assertIn("Sensor configuration", groups)
        self.assertIn("Device-reported settings", groups)
        for combo in dialog.combos.values():
            self.assertIs(groups["Sensor configuration"], combo.parentWidget())
        self.assertIs(
            groups["Device-reported settings"],
            dialog.actual_settings_label.parentWidget(),
        )
        settings_layout = groups["Sensor configuration"].layout()
        device_layout = groups["Device-reported settings"].layout()
        content = dialog.findChild(QWidget, "sensorSettingsContent")
        self.assertIsNotNone(content)
        self.assertEqual(dialog.layout().getContentsMargins(), (6, 6, 6, 6))
        self.assertIs(dialog.layout().itemAt(0).widget(), content)
        self.assertEqual(content.layout().getContentsMargins(), (6, 6, 6, 6))
        self.assertIs(groups["Sensor configuration"].parentWidget(), content)
        self.assertIs(groups["Device-reported settings"].parentWidget(), content)
        self.assertIsInstance(settings_layout, QVBoxLayout)
        self.assertIsInstance(device_layout, QVBoxLayout)
        for group_layout in (settings_layout, device_layout):
            self.assertEqual(
                group_layout.getContentsMargins(),
                (6, 6, 6, 6),
            )
            self.assertEqual(group_layout.spacing(), 6)
        form = settings_layout.itemAt(0).layout()
        self.assertIsInstance(form, QFormLayout)
        self.assertEqual(form.getContentsMargins(), (0, 0, 0, 0))
        self.assertEqual(form.spacing(), 6)


if __name__ == "__main__":
    unittest.main()
