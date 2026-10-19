"""Tests for verified sensor discovery and serial reconnect behavior."""

import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from serial import SerialException

from src.util import serial_discovery
from src.workers.gui_qt.panels.connection_panel import ConnectionPanelQt
from src.workers.serial_wrk import serial_thread


VALID_SETTINGS_LINE = (
    b"!SENSOR,SETTINGS,gyro_range=1000,accel_range=2,sensor_rate=1000,"
    b"gyro_lpf=42,accel_lpf=41,output_rate=250\n"
)
VALID_IMU_LINE = b"0.000000,0.0,0.0,1.0,0.0,0.0,0.0\n"


class _ProbeSerial:
    def __init__(self, responses):
        self.responses = (
            list(responses)
            if isinstance(responses, (tuple, list))
            else [responses]
        )
        self.closed = False
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def write(self, payload):
        self.writes.append(payload)

    def readline(self):
        return self.responses.pop(0) if self.responses else b""

    def close(self):
        self.closed = True


class _StreamingSerial:
    def __init__(self, lines=(), disconnected=False):
        self.lines = list(lines)
        self.disconnected = disconnected
        self.closed = False
        self.port = "COM7"

    @property
    def in_waiting(self):
        if self.disconnected:
            raise SerialException("USB serial device disconnected")
        return len(self.lines[0]) if self.lines else 0

    def readline(self):
        return self.lines.pop(0)

    def close(self):
        self.closed = True


class SerialDiscoveryTests(unittest.TestCase):
    def test_probes_preferred_then_supported_bauds_for_valid_settings(self):
        opened = []
        connections = []

        def serial_factory(device, baud, **_kwargs):
            opened.append((device, baud))
            response = (
                [
                    VALID_SETTINGS_LINE[:47],
                    VALID_SETTINGS_LINE[47:],
                ]
                if baud == 500000
                else b""
            )
            connection = _ProbeSerial(response)
            connections.append(connection)
            return connection

        with (
            patch.object(
                serial_discovery.list_ports,
                "comports",
                return_value=[SimpleNamespace(device="COM7")],
            ),
            patch.object(serial_discovery.serial, "Serial", side_effect=serial_factory),
            patch.object(serial_discovery, "SERIAL_BAUD_RATES", (500000,)),
            patch.object(serial_discovery, "PROBE_TIMEOUT_S", 0.01),
            patch.object(serial_discovery, "PROBE_QUERY_INTERVAL_S", 0.001),
        ):
            result = serial_discovery.detect_orienta_sensor(115200)

        self.assertEqual(opened, [("COM7", 115200), ("COM7", 500000)])
        self.assertEqual(result[0:2], ("COM7", 500000))
        self.assertEqual(result[2]["gyro_range"], 1000)
        self.assertTrue(all(connection.closed for connection in connections))

    def test_cancelled_probe_does_not_open_any_ports(self):
        cancel_event = threading.Event()
        cancel_event.set()
        with (
            patch.object(
                serial_discovery.list_ports,
                "comports",
                return_value=[SimpleNamespace(device="COM7")],
            ),
            patch.object(serial_discovery.serial, "Serial") as serial_factory,
        ):
            result = serial_discovery.detect_orienta_sensor(115200, cancel_event)

        self.assertIsNone(result)
        serial_factory.assert_not_called()


class SerialReconnectTests(unittest.TestCase):
    def test_stop_cancels_a_pending_open_retry_promptly(self):
        commands = queue.Queue()
        commands.put(("start", "COM7", 500000))
        stop_event = threading.Event()
        statuses = queue.Queue()
        attempts = []

        def serial_factory(port, baud, **_kwargs):
            attempts.append((port, baud))
            raise SerialException("port not present")

        thread = threading.Thread(
            target=serial_thread,
            args=(
                queue.Queue(),
                queue.Queue(),
                queue.Queue(),
                stop_event,
                commands,
                statuses,
                None,
                None,
            ),
        )

        with (
            patch("src.workers.serial_wrk.serial.Serial", side_effect=serial_factory),
            patch("src.workers.serial_wrk.SERIAL_RETRY_DELAY", 10.0),
        ):
            thread.start()
            deadline = time.monotonic() + 1.0
            while not attempts and time.monotonic() < deadline:
                time.sleep(0.01)
            commands.put(("stop",))
            thread.join(timeout=1.0)
            if thread.is_alive():
                stop_event.set()
                thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(attempts, [("COM7", 500000)])
        self.assertIn(
            ("serial_connection", "stopped"),
            list(statuses.queue),
        )

    def test_reconnects_and_excludes_startup_text_from_fusion_queue(self):
        first = _StreamingSerial(disconnected=True)
        second = _StreamingSerial(
            lines=[
                VALID_SETTINGS_LINE,
                b"ERROR: MPU6500 initialization message\n",
                VALID_IMU_LINE,
            ]
        )
        serial_instances = iter((first, second))
        serial_factory_calls = []

        def serial_factory(port, baud, **_kwargs):
            serial_factory_calls.append((port, baud))
            return next(serial_instances)

        commands = queue.Queue()
        commands.put(("start", "COM7", 500000))
        stop_event = threading.Event()
        serial_queue = queue.Queue()
        display_queue = queue.Queue()
        statuses = queue.Queue()
        ui_statuses = queue.Queue()
        thread = threading.Thread(
            target=serial_thread,
            args=(
                queue.Queue(),
                serial_queue,
                display_queue,
                stop_event,
                commands,
                statuses,
                None,
                ui_statuses,
            ),
        )

        with (
            patch("src.workers.serial_wrk.serial.Serial", side_effect=serial_factory),
            patch("src.workers.serial_wrk.SERIAL_RETRY_DELAY", 0.01),
            patch("src.workers.serial_wrk.SERIAL_RETRY_MAX_DELAY", 0.02),
        ):
            thread.start()
            deadline = time.monotonic() + 2.0
            while serial_queue.empty() and time.monotonic() < deadline:
                time.sleep(0.01)
            stop_event.set()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(serial_factory_calls, [("COM7", 500000), ("COM7", 500000)])
        self.assertEqual(serial_queue.get_nowait(), VALID_IMU_LINE.decode().strip())
        self.assertTrue(serial_queue.empty())
        self.assertEqual(
            [display_queue.get_nowait() for _ in range(3)],
            [
                VALID_SETTINGS_LINE.decode().strip(),
                "ERROR: MPU6500 initialization message",
                VALID_IMU_LINE.decode().strip(),
            ],
        )
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        connection_states = []
        while not statuses.empty():
            item = statuses.get_nowait()
            if item[0] == "serial_connection":
                connection_states.append(item[1])
        self.assertIn("reconnecting", connection_states)
        self.assertIn("connected", connection_states)
        self.assertIn(
            ("sensor_settings_response", VALID_SETTINGS_LINE.decode().strip()),
            list(ui_statuses.queue),
        )


class ConnectionPanelDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.commands = queue.Queue()
        self.panel = ConnectionPanelQt(None, self.commands, lambda _message: None)
        self.addCleanup(self.panel.close)

    def test_detect_selects_verified_port_and_baud_without_starting_serial(self):
        self.panel.detect_sensor()
        self.assertEqual(self.commands.get_nowait(), ("detect", 115200))
        self.assertFalse(self.panel.toggle_button.isEnabled())

        self.panel.handle_detection_result((
            "found",
            (
                "COM7",
                500000,
                {"gyro_range": 1000, "accel_range": 2},
            ),
        ))

        self.assertEqual(self.panel.port_combo.currentText(), "COM7")
        self.assertEqual(self.panel.baud_combo.currentText(), "500000")
        self.assertFalse(self.panel.is_connected)
        self.assertEqual(self.panel.toggle_button.text(), "Start Serial")

    def test_detect_button_cancels_an_active_scan(self):
        self.panel.detect_sensor()
        self.panel.detect_sensor()

        self.assertEqual(self.commands.get_nowait(), ("detect", 115200))
        self.assertEqual(self.commands.get_nowait(), ("cancel_detection",))
        self.assertFalse(self.panel.detect_button.isEnabled())

        self.panel.handle_detection_result(("cancelled", None))
        self.assertTrue(self.panel.detect_button.isEnabled())

    def test_reconnecting_keeps_stop_control_available(self):
        self.panel._start_serial()
        self.panel.update_connection_status("reconnecting")

        self.assertTrue(self.panel._is_running)
        self.assertFalse(self.panel.port_combo.isEnabled())
        self.assertFalse(self.panel.detect_button.isEnabled())
        self.assertEqual(self.panel.toggle_button.text(), "Stop Serial")


class SavedSettingsAutoApplyTests(unittest.TestCase):
    def make_gui(self, settings):
        from src.workers.gui_wrk import TabbedGUIWorker

        connection_panel = SimpleNamespace(
            is_connected=True,
            update_connection_status=Mock(),
            update_message_rate=Mock(),
            update_data_activity=Mock(),
        )
        orientation_panel = SimpleNamespace(
            update_serial_connection_status=Mock(),
            update_processing_status=Mock(),
            clear_calibration_state=Mock(),
            update_drift_status=Mock(),
            update_device_status=Mock(),
        )
        gui = SimpleNamespace(
            connection_panel=connection_panel,
            orientation_panel=orientation_panel,
            sensor_settings_dialog=None,
            sensor_settings_preferences=settings,
            serial_control_queue=queue.Queue(),
            log_queue=queue.Queue(),
            _sensor_settings_apply_waiting_for_samples=False,
            _sensor_settings_apply_expected=None,
            _sensor_settings_apply_timer=Mock(),
            _log_message=Mock(),
            _safe_panel_call=lambda panel_name, method_name, *args: getattr(
                getattr(gui, panel_name),
                method_name,
            )(*args),
            _clear_orientation_indicators=Mock(),
        )
        gui._send_sensor_settings_command = (
            TabbedGUIWorker._send_sensor_settings_command.__get__(gui)
        )
        gui._apply_sensor_settings_on_connect = (
            TabbedGUIWorker._apply_sensor_settings_on_connect.__get__(gui)
        )
        gui._clear_sensor_settings_apply = (
            TabbedGUIWorker._clear_sensor_settings_apply.__get__(gui)
        )
        gui._handle_sensor_settings_apply_response = (
            TabbedGUIWorker._handle_sensor_settings_apply_response.__get__(gui)
        )
        return gui

    def test_connected_status_applies_stored_settings(self):
        settings = {
            "gyro_range": 500,
            "accel_range": 4,
            "sensor_rate": 500,
            "gyro_lpf": 98,
            "accel_lpf": 92,
            "output_rate": 250,
        }
        gui = self.make_gui(settings)

        from src.workers.gui_wrk import TabbedGUIWorker

        TabbedGUIWorker._route_status_update(
            gui,
            "serial_connection",
            "connected",
            ui_status=True,
        )

        self.assertTrue(gui.serial_control_queue.empty())
        self.assertTrue(gui._sensor_settings_apply_waiting_for_samples)
        TabbedGUIWorker._route_status_update(
            gui,
            "sensor_settings_response",
            (
                "!SENSOR,SETTINGS,gyro_range=1000,accel_range=2,"
                "sensor_rate=1000,gyro_lpf=42,accel_lpf=41,output_rate=250"
            ),
            ui_status=True,
        )
        self.assertTrue(gui.serial_control_queue.empty())
        TabbedGUIWorker._route_status_update(
            gui,
            "serial_data",
            True,
            ui_status=True,
        )
        self.assertEqual(
            gui.serial_control_queue.get_nowait(),
            ("sensor_command", "SET", settings),
        )
        self.assertEqual(gui._sensor_settings_apply_expected, settings)
        gui._sensor_settings_apply_timer.start.assert_called_once()

        gui._handle_sensor_settings_apply_response(
            "!SENSOR,SETTINGS,gyro_range=1000,accel_range=2,sensor_rate=1000,"
            "gyro_lpf=42,accel_lpf=41,output_rate=250"
        )
        self.assertEqual(gui._sensor_settings_apply_expected, settings)

        gui._handle_sensor_settings_apply_response(
            "!SENSOR,SETTINGS,gyro_range=500,accel_range=4,sensor_rate=500,"
            "gyro_lpf=98,accel_lpf=92,output_rate=250"
        )
        self.assertIsNone(gui._sensor_settings_apply_expected)
        gui._sensor_settings_apply_timer.stop.assert_called_once()

    def test_saved_profile_is_applied_again_after_reconnect(self):
        settings = {
            "gyro_range": 1000,
            "accel_range": 2,
            "sensor_rate": 1000,
            "gyro_lpf": 42,
            "accel_lpf": 41,
            "output_rate": 250,
        }
        gui = self.make_gui(settings)
        from src.workers.gui_wrk import TabbedGUIWorker

        TabbedGUIWorker._route_status_update(
            gui,
            "serial_connection",
            "connected",
            ui_status=True,
        )
        TabbedGUIWorker._route_status_update(
            gui,
            "serial_data",
            True,
            ui_status=True,
        )
        self.assertEqual(
            gui.serial_control_queue.get_nowait(),
            ("sensor_command", "SET", settings),
        )

        TabbedGUIWorker._route_status_update(
            gui,
            "serial_connection",
            "reconnecting",
            ui_status=True,
        )
        self.assertIsNone(gui._sensor_settings_apply_expected)
        TabbedGUIWorker._route_status_update(
            gui,
            "serial_connection",
            "connected",
            ui_status=True,
        )
        TabbedGUIWorker._route_status_update(
            gui,
            "serial_data",
            True,
            ui_status=True,
        )
        self.assertEqual(
            gui.serial_control_queue.get_nowait(),
            ("sensor_command", "SET", settings),
        )

    def test_no_saved_settings_preserves_device_configuration(self):
        gui = self.make_gui(None)

        from src.workers.gui_wrk import TabbedGUIWorker

        TabbedGUIWorker._route_status_update(
            gui,
            "serial_connection",
            "connected",
            ui_status=True,
        )

        self.assertTrue(gui.serial_control_queue.empty())
        self.assertIsNone(gui._sensor_settings_apply_expected)

    def test_open_settings_dialog_does_not_queue_get_before_saved_set(self):
        settings = {
            "gyro_range": 1000,
            "accel_range": 2,
            "sensor_rate": 1000,
            "gyro_lpf": 42,
            "accel_lpf": 41,
            "output_rate": 250,
        }
        gui = self.make_gui(settings)
        gui.sensor_settings_dialog = Mock()
        gui.sensor_settings_dialog.apply_settings.return_value = True

        from src.workers.gui_wrk import TabbedGUIWorker

        TabbedGUIWorker._route_status_update(
            gui,
            "serial_connection",
            "connected",
            ui_status=True,
        )

        gui.sensor_settings_dialog.set_connected.assert_called_once_with(
            True,
            read_on_connect=False,
        )
        gui.sensor_settings_dialog.apply_settings.assert_not_called()
        TabbedGUIWorker._route_status_update(
            gui,
            "serial_data",
            True,
            ui_status=True,
        )
        gui.sensor_settings_dialog.apply_settings.assert_called_once_with(settings)
        self.assertTrue(gui.serial_control_queue.empty())

    def test_invalid_saved_settings_are_not_sent(self):
        settings = {
            "gyro_range": 500,
            "accel_range": 4,
            "sensor_rate": 100,
            "gyro_lpf": 98,
            "accel_lpf": 92,
            "output_rate": 250,
        }
        gui = self.make_gui(settings)

        self.assertFalse(
            gui._apply_sensor_settings_on_connect()
        )

        self.assertTrue(gui.serial_control_queue.empty())
        gui._log_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
