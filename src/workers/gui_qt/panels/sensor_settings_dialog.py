"""Runtime sensor settings dialog for firmware that supports the Orienta protocol."""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from src.config.config import DEFAULT_SPACING, DIALOG_CONTENT_MARGIN
from .dialog_utils import (
    configure_dialog,
    configure_section_layout,
    create_dialog_content,
    create_close_button,
)
from src.util.sensor_settings import (
    DEFAULT_SENSOR_SETTINGS,
    SENSOR_SETTING_PRESETS,
    normalize_sensor_settings,
    parse_sensor_settings_response,
)


_SETTING_ROWS = (
    ("gyro_range", "Gyroscope range", "°/s"),
    ("accel_range", "Accelerometer range", "g"),
    ("sensor_rate", "Sensor update rate", "Hz"),
    ("gyro_lpf", "Gyroscope low-pass filter", "Hz"),
    ("accel_lpf", "Accelerometer low-pass filter", "Hz"),
    ("output_rate", "Serial output rate", "Hz"),
)


class SensorSettingsDialog(QDialog):
    """Select sensor presets and display settings confirmed by the firmware."""

    preferences_changed = pyqtSignal(dict)

    def __init__(self, send_command, preferences=None, connected=False, parent=None):
        super().__init__(parent, Qt.Window)
        layout = configure_dialog(self, "sensorSettingsDialog", "Sensor settings")
        content_layout = create_dialog_content(
            self, layout, "sensorSettingsContent"
        )
        self.send_command = send_command
        self.connected = bool(connected)
        self._loading = True
        self._pending_apply = False
        self._requested_settings = None
        self.settings = normalize_sensor_settings(
            preferences or DEFAULT_SENSOR_SETTINGS
        )
        self.combos = {}
        self.setMinimumWidth(430)

        settings_group = QGroupBox("Sensor configuration", self)
        settings_layout = QVBoxLayout(settings_group)
        configure_section_layout(settings_layout)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(DEFAULT_SPACING)
        for key, label, unit in _SETTING_ROWS:
            combo = QComboBox(self)
            for value in SENSOR_SETTING_PRESETS[key]:
                combo.addItem(f"{value} {unit}", value)
            combo.setCurrentIndex(combo.findData(self.settings[key]))
            combo.currentIndexChanged.connect(self._on_selection_changed)
            self.combos[key] = combo
            form.addRow(label, combo)
        settings_layout.addLayout(form)
        content_layout.addWidget(settings_group)

        self.actual_settings_label = QLabel("Not read from the device yet.")
        self.actual_settings_label.setWordWrap(True)
        device_group = QGroupBox("Device-reported settings", self)
        device_layout = QVBoxLayout(device_group)
        configure_section_layout(device_layout)
        device_layout.addWidget(self.actual_settings_label)
        content_layout.addWidget(device_group)

        self.status_label = QLabel("Connect a compatible sensor and read its settings.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        self.read_button = QPushButton("Read device settings")
        self.read_button.clicked.connect(self.request_settings)
        buttons.addWidget(self.read_button)
        buttons.addStretch()
        self.apply_button = QPushButton("Apply to sensor")
        self.apply_button.clicked.connect(self.apply_settings)
        buttons.addWidget(self.apply_button)
        self.close_button = create_close_button(self)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self._response_timer = QTimer(self)
        self._response_timer.setSingleShot(True)
        self._response_timer.setInterval(3000)
        self._response_timer.timeout.connect(self._on_response_timeout)
        self._loading = False
        self._update_controls()

    def selected_settings(self):
        return {
            key: int(combo.currentData())
            for key, combo in self.combos.items()
        }

    def _on_selection_changed(self, _index):
        if self._loading:
            return
        self.settings = self.selected_settings()
        self.preferences_changed.emit(dict(self.settings))
        self._update_controls()

    def _update_controls(self):
        valid_rate_pair = (
            self.combos["output_rate"].currentData()
            <= self.combos["sensor_rate"].currentData()
        )
        self.read_button.setEnabled(self.connected and not self._response_timer.isActive())
        self.apply_button.setEnabled(
            self.connected and valid_rate_pair and not self._response_timer.isActive()
        )
        if not valid_rate_pair:
            self.status_label.setText(
                "Output rate cannot exceed the sensor update rate."
            )

    def set_connected(self, connected, read_on_connect=False):
        was_connected = self.connected
        self.connected = bool(connected)
        if not self.connected:
            self._response_timer.stop()
            self._pending_apply = False
            self.status_label.setText("Sensor disconnected.")
        elif read_on_connect or not was_connected:
            self.request_settings()
        self._update_controls()

    def request_settings(self):
        if not self.connected:
            self.status_label.setText("Connect the sensor before reading settings.")
            return
        if not self.send_command(("GET",)):
            self.status_label.setText("Could not send the settings request.")
            return
        self._pending_apply = False
        self._response_timer.start()
        self.status_label.setText("Waiting for the device to report its settings…")
        self._update_controls()

    def apply_settings(self):
        if not self.connected:
            self.status_label.setText("Connect the sensor before applying settings.")
            return
        self._requested_settings = self.selected_settings()
        if self._requested_settings["output_rate"] > self._requested_settings["sensor_rate"]:
            self.status_label.setText(
                "Output rate cannot exceed the sensor update rate."
            )
            return
        if not self.send_command(("SET", self._requested_settings)):
            self.status_label.setText("Could not send sensor settings.")
            return
        self._pending_apply = True
        self._response_timer.start()
        self.status_label.setText("Applying settings; waiting for device confirmation…")
        self._update_controls()

    def handle_device_response(self, line):
        self._response_timer.stop()
        try:
            actual = parse_sensor_settings_response(line)
        except ValueError as error:
            self._pending_apply = False
            self.status_label.setText(f"Device response: {error}")
            self._update_controls()
            return

        self.actual_settings_label.setText(
            "Gyro ±{gyro_range}°/s; accel ±{accel_range} g; sensor {sensor_rate} Hz; "
            "gyro LPF {gyro_lpf} Hz; accel LPF {accel_lpf} Hz; output {output_rate} Hz."
            .format(**actual)
        )
        if self._pending_apply:
            if actual == self._requested_settings:
                self._loading = True
                for key, value in actual.items():
                    self.combos[key].setCurrentIndex(
                        self.combos[key].findData(value)
                    )
                self._loading = False
                self.settings = dict(actual)
                self.preferences_changed.emit(dict(actual))
                self.status_label.setText("Settings applied and confirmed by the device.")
            else:
                self.status_label.setText(
                    "Device reported settings different from the requested values."
                )
        else:
            self.status_label.setText("Device settings read successfully.")
        self._pending_apply = False
        self._update_controls()

    def handle_transport_error(self, message):
        self._response_timer.stop()
        self._pending_apply = False
        self.status_label.setText(str(message))
        self._update_controls()

    def _on_response_timeout(self):
        self._pending_apply = False
        self.status_label.setText(
            "No response. Check the firmware supports the Orienta sensor settings protocol."
        )
        self._update_controls()
