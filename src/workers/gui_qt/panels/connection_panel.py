"""Serial input settings and connection lifecycle controls."""

import time

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from .base_panel import (
    BasePanelQt,
    BUTTON_EXTRA_HEIGHT,
    BUTTON_MIN_HEIGHT,
    CONTENT_MARGINS,
    DEFAULT_SPACING,
)
from .two_line_button import TwoLineButton

from src.config.config import (
    DEFAULT_SERIAL_BAUD,
    DEFAULT_SERIAL_PORT,
    QUEUE_PUT_TIMEOUT,
    SERIAL_BAUD_RATES,
)
from src.util.error_utils import safe_queue_put


class ConnectionPanelQt(BasePanelQt):
    """Configure the serial sensor input; output controls live in OutputPanelQt."""

    def __init__(
        self,
        parent,
        serial_control_queue,
        message_callback,
        padding=6,
        on_serial_stop=None,
        on_sensor_settings=None,
    ):
        self.serial_control_queue = serial_control_queue
        self.on_serial_stop = on_serial_stop
        self.on_sensor_settings = on_sensor_settings
        self.padding = padding
        self._port_value = DEFAULT_SERIAL_PORT
        self._baud_value = str(DEFAULT_SERIAL_BAUD)
        self._is_running = False
        self._connection_status = "stopped"
        self._fusion_processing = False
        self._serial_rate_text = "0.0 msg/s"
        self._detection_active = False
        self._data_activity_timer = QTimer()
        self._data_activity_timer.timeout.connect(self._on_data_timeout)
        self._data_activity_timer.setSingleShot(True)
        self._last_data_time = 0.0
        super().__init__(parent, "Serial Settings", message_callback=message_callback)

    def setup_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(*CONTENT_MARGINS)
        outer_layout.setSpacing(DEFAULT_SPACING)

        content_frame = QFrame(self)
        layout = QVBoxLayout(content_frame)
        layout.setContentsMargins(*CONTENT_MARGINS)
        layout.setSpacing(DEFAULT_SPACING)
        self.content_layout = layout
        outer_layout.addWidget(content_frame)

        serial_row = QHBoxLayout()
        label = QLabel("Serial Port:")
        label.setToolTip("Serial port to open (e.g., COM3)")
        serial_row.addWidget(label)
        serial_row.addStretch()
        self.port_combo = QComboBox()
        self.port_combo.addItems([f"COM{index}" for index in range(100)])
        self.port_combo.setCurrentText(self._port_value)
        self.port_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.port_combo.currentTextChanged.connect(self._on_port_changed)
        serial_row.addWidget(self.port_combo)
        self.detect_button = QPushButton("Detect sensor")
        self.detect_button.setToolTip(
            "Probe serial ports for compatible Orienta sensor firmware. "
            "The probe sends a read-only settings request. Opening a USB "
            "serial port may reset its device."
        )
        self.detect_button.clicked.connect(self.detect_sensor)
        layout.addLayout(serial_row)

        baud_row = QHBoxLayout()
        baud_row.addWidget(QLabel("Baud:"))
        baud_row.addStretch()
        self.baud_combo = QComboBox()
        self.baud_combo.addItems([str(rate) for rate in SERIAL_BAUD_RATES])
        self.baud_combo.setCurrentText(self._baud_value)
        self.baud_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.baud_combo.currentTextChanged.connect(self._on_baud_changed)
        baud_row.addWidget(self.baud_combo)
        layout.addLayout(baud_row)

        self.toggle_button = TwoLineButton("Start Serial", self._serial_rate_text)
        self.toggle_button.setMinimumHeight(
            BUTTON_MIN_HEIGHT + BUTTON_EXTRA_HEIGHT
        )
        self.toggle_button.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred,
        )
        self.toggle_button.setProperty("status", "")
        self.toggle_button.clicked.connect(self.toggle_serial)
        self.sensor_settings_button = QPushButton("Sensor settings")
        self.sensor_settings_button.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )
        self.sensor_settings_button.clicked.connect(self._open_sensor_settings)
        self.detect_button.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )
        sensor_actions_row = QHBoxLayout()
        sensor_actions_row.setSpacing(DEFAULT_SPACING)
        sensor_actions_row.setContentsMargins(0, 0, 0, 0)
        sensor_actions_row.addWidget(self.detect_button)
        sensor_actions_row.addWidget(self.sensor_settings_button)
        layout.addStretch()
        layout.addLayout(sensor_actions_row)
        layout.addWidget(self.toggle_button)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _open_sensor_settings(self):
        if callable(self.on_sensor_settings):
            self.on_sensor_settings()

    @property
    def is_connected(self):
        return self._is_running and self._connection_status == "connected"

    def _on_port_changed(self, port):
        self._port_value = port

    def _on_baud_changed(self, baud):
        self._baud_value = baud

    def toggle_serial(self):
        if self._detection_active:
            return
        if self._is_running:
            self._stop_serial()
        else:
            self._start_serial()

    def detect_sensor(self):
        if self._is_running:
            self.log_message("Stop serial before detecting a sensor")
            return
        if self._detection_active:
            if safe_queue_put(
                self.serial_control_queue,
                ("cancel_detection",),
                timeout=QUEUE_PUT_TIMEOUT,
            ):
                self.detect_button.setText("Cancelling…")
                self.detect_button.setEnabled(False)
            else:
                self.log_message("Failed to cancel sensor detection")
            return

        try:
            preferred_baud = int(self.baud_combo.currentText())
        except ValueError:
            preferred_baud = DEFAULT_SERIAL_BAUD
        if not safe_queue_put(
            self.serial_control_queue,
            ("detect", preferred_baud),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            self.log_message("Failed to request sensor detection")
            return

        self._detection_active = True
        self.detect_button.setText("Cancel detection")
        self.port_combo.setEnabled(False)
        self.baud_combo.setEnabled(False)
        self.toggle_button.setEnabled(False)
        self.log_message("Scanning serial ports for a compatible Orienta sensor...")

    def handle_detection_result(self, result):
        self._detection_active = False
        self.detect_button.setText("Detect sensor")
        self.detect_button.setEnabled(not self._is_running)
        self.port_combo.setEnabled(not self._is_running)
        self.baud_combo.setEnabled(not self._is_running)
        self.toggle_button.setEnabled(True)

        if not isinstance(result, (tuple, list)) or not result:
            self.log_message("Received an invalid sensor detection result")
            return

        state = result[0]
        if state == "found" and len(result) >= 2:
            try:
                port, baud, settings = result[1]
                port = str(port)
                baud = str(int(baud))
            except (TypeError, ValueError, OverflowError):
                self.log_message("Received malformed sensor detection details")
                return
            if self.port_combo.findText(port) < 0:
                self.port_combo.addItem(port)
            if self.baud_combo.findText(baud) < 0:
                self.baud_combo.addItem(baud)
            self.port_combo.setCurrentText(port)
            self.baud_combo.setCurrentText(baud)
            self.log_message(
                f"Detected compatible sensor on {port} at {baud} baud "
                f"(gyro ±{settings['gyro_range']}°/s, "
                f"accel ±{settings['accel_range']} g)"
            )
        elif state == "not_found":
            self.log_message(
                "No compatible sensor configuration was found on the available ports"
            )
        elif state == "cancelled":
            self.log_message("Sensor detection cancelled")
        elif state == "error" and len(result) >= 2:
            self.log_message(f"Sensor detection failed: {result[1]}")
        else:
            self.log_message(f"Unexpected sensor detection result: {state}")

    def _start_serial(self):
        port = self.port_combo.currentText()
        try:
            baud = int(self.baud_combo.currentText())
        except ValueError:
            baud = DEFAULT_SERIAL_BAUD

        if not safe_queue_put(
            self.serial_control_queue,
            ("start", port, baud),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            self.log_message("Failed to request serial start")
            return

        self._is_running = True
        self._connection_status = "starting"
        self._set_serial_button("Stop Serial", "warning")
        self.port_combo.setEnabled(False)
        self.baud_combo.setEnabled(False)
        self.log_message(f"Start requested on {port} @ {baud}")

    def _stop_serial(self):
        if not safe_queue_put(
            self.serial_control_queue,
            ("stop",),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            self.log_message("Failed to request serial stop")
            return

        self._is_running = False
        self._connection_status = "stopped"
        self._set_serial_button("Start Serial", "")
        self.port_combo.setEnabled(True)
        self.baud_combo.setEnabled(True)
        self._data_activity_timer.stop()
        self.log_message("Stop requested")
        if callable(self.on_serial_stop):
            try:
                self.on_serial_stop()
            except Exception as error:
                self.log_message(f"Serial stop callback failed: {error}")

    def _set_serial_button(self, label, status):
        self.toggle_button.setParts(label, self._serial_rate_text)
        self.toggle_button.setProperty("status", status)
        self.toggle_button.style().polish(self.toggle_button)

    def update_connection_status(self, status):
        self._connection_status = status
        if status in ("starting", "reconnecting", "connected"):
            self._is_running = True
            self._set_serial_button("Stop Serial", "warning")
            self.port_combo.setEnabled(False)
            self.baud_combo.setEnabled(False)
            self.detect_button.setEnabled(False)
            self.toggle_button.setEnabled(not self._detection_active)
        elif status in ("error", "stopped"):
            self._is_running = False
            self.port_combo.setEnabled(True)
            self.baud_combo.setEnabled(True)
            self.detect_button.setEnabled(not self._detection_active)
            self.toggle_button.setEnabled(not self._detection_active)
            self._data_activity_timer.stop()
            self._set_serial_button(
                "Start Serial",
                "error" if status == "error" else "",
            )
            if status == "stopped":
                self.reset_status()
        else:
            self.toggle_button.style().polish(self.toggle_button)

    def update_data_activity(self):
        if not self._is_running or self._connection_status != "connected":
            return
        self._last_data_time = time.time()
        if self._fusion_processing:
            self.toggle_button.setProperty("status", "enabled")
            self.toggle_button.style().polish(self.toggle_button)
        self._data_activity_timer.start(5000)

    def _on_data_timeout(self):
        if self._is_running and self._connection_status == "connected":
            self.toggle_button.setProperty("status", "warning")
            self.toggle_button.style().polish(self.toggle_button)

    def update_fusion_status(self, is_active):
        self._fusion_processing = bool(is_active)
        if self._is_running and self._connection_status == "connected":
            status = "enabled" if is_active else "warning"
            self.toggle_button.setProperty("status", status)
            self.toggle_button.style().polish(self.toggle_button)

    def update_message_rate(self, rate):
        self._serial_rate_text = f"{float(rate):.1f} msg/s"
        label = "Stop Serial" if self._is_running else "Start Serial"
        self._set_serial_button(label, self.toggle_button.property("status") or "")

    def reset_status(self):
        self.update_message_rate(0.0)

    def get_prefs(self):
        return {
            "com_port": self.port_combo.currentText(),
            "baud_rate": self.baud_combo.currentText(),
        }

    def set_prefs(self, prefs):
        if not isinstance(prefs, dict):
            return
        serial = prefs.get("serial", prefs)
        if not isinstance(serial, dict):
            self.log_message("Ignoring invalid serial preferences")
            return
        port = serial.get("com_port")
        if port:
            port = str(port)
            if self.port_combo.findText(port) < 0:
                self.port_combo.addItem(port)
            self.port_combo.setCurrentText(port)
            self._port_value = port
        baud = serial.get("baud_rate")
        if baud:
            baud = str(baud)
            self.baud_combo.setCurrentText(baud)
            self._baud_value = baud
