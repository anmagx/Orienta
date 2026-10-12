"""Protocol selection and lifecycle controls for orientation output."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from .native_output_settings_dialog import NativeOutputSettingsDialog
from .two_line_button import TwoLineButton

from src.config.config import (
    DEFAULT_OUTPUT_PROTOCOL,
    DEFAULT_UDP_IP,
    DEFAULT_UDP_PORT,
    OUTPUT_PROTOCOL_FREETRACK,
    OUTPUT_PROTOCOL_NPCLIENT,
    OUTPUT_PROTOCOL_UDP,
    QUEUE_PUT_TIMEOUT,
)
from src.util.error_utils import safe_queue_put


class OutputPanelQt(BasePanelQt):
    """Own output selection, UDP configuration, and client registrations."""

    def __init__(
        self,
        parent,
        output_control_queue,
        message_callback,
        native_output_dialog=None,
        freetrack_client_registration=None,
        npclient_registration=None,
    ):
        self.output_control_queue = output_control_queue
        self.freetrack_client_registration = freetrack_client_registration
        self.npclient_registration = npclient_registration
        self.native_output_dialog = native_output_dialog or NativeOutputSettingsDialog(
            output_control_queue,
            message_callback,
            parent,
        )
        self._udp_ip = DEFAULT_UDP_IP
        self._udp_port = str(DEFAULT_UDP_PORT)
        self.output_protocol = DEFAULT_OUTPUT_PROTOCOL
        self.output_enabled = False
        self._output_rate_text = "0.0 msg/s"
        super().__init__(parent, "Output", message_callback=message_callback)

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

        protocol_row = QHBoxLayout()
        protocol_row.addWidget(QLabel("Protocol:"))
        self.output_protocol_combo = QComboBox()
        self.output_protocol_combo.addItem("UDP (opentrack)", OUTPUT_PROTOCOL_UDP)
        self.output_protocol_combo.addItem(
            "FreeTrack 2.0 (Direct)",
            OUTPUT_PROTOCOL_FREETRACK,
        )
        self.output_protocol_combo.addItem(
            "NPClient (TrackIR-compatible)",
            OUTPUT_PROTOCOL_NPCLIENT,
        )
        protocol_row.addWidget(self.output_protocol_combo, 1)
        layout.addLayout(protocol_row)

        self.udp_settings_widget = QFrame(self)
        udp_layout = QVBoxLayout(self.udp_settings_widget)
        udp_layout.setContentsMargins(0, 0, 0, 0)
        udp_layout.setSpacing(DEFAULT_SPACING)
        self.udp_ip_entry = self._add_udp_field(
            udp_layout,
            "IP:",
            self._udp_ip,
        )
        self.udp_ip_entry.setToolTip(
            "Destination IP for UDP packets (e.g., 127.0.0.1)"
        )
        self.udp_ip_entry.textChanged.connect(self._on_ip_changed)
        self.udp_port_entry = self._add_udp_field(
            udp_layout,
            "Port:",
            self._udp_port,
        )
        self.udp_port_entry.setValidator(QIntValidator(1, 65535))
        self.udp_port_entry.textChanged.connect(self._on_udp_port_changed)
        layout.addWidget(self.udp_settings_widget)

        self.native_output_settings_btn = QPushButton(
            "Native Output Settings..."
        )
        self.native_output_settings_btn.clicked.connect(
            self._open_native_output_settings
        )
        layout.addWidget(self.native_output_settings_btn)

        layout.addStretch()
        self.output_toggle_btn = TwoLineButton(
            "Start UDP",
            self._output_rate_text,
        )
        self.output_toggle_btn.setMinimumHeight(
            BUTTON_MIN_HEIGHT + BUTTON_EXTRA_HEIGHT
        )
        self.output_toggle_btn.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred,
        )
        self.output_toggle_btn.setProperty("status", "")
        self.output_toggle_btn.clicked.connect(self.toggle_output)
        self.udp_toggle_btn = self.output_toggle_btn
        layout.addWidget(self.output_toggle_btn)

        self.output_protocol_combo.currentIndexChanged.connect(
            self._on_output_protocol_changed
        )
        self._update_output_controls()
        self._refresh_output_button_text()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    @staticmethod
    def _add_udp_field(layout, label_text, value):
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row.addWidget(label)
        row.addStretch()
        entry = QLineEdit()
        entry.setText(value)
        entry.setAlignment(Qt.AlignRight)
        entry.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(entry)
        layout.addLayout(row)
        return entry

    def _on_ip_changed(self, text):
        self._udp_ip = text

    def _on_udp_port_changed(self, text):
        self._udp_port = text

    def _on_output_protocol_changed(self, index):
        requested = self.output_protocol_combo.itemData(index)
        if requested not in (
            OUTPUT_PROTOCOL_UDP,
            OUTPUT_PROTOCOL_FREETRACK,
            OUTPUT_PROTOCOL_NPCLIENT,
        ):
            return
        if requested == self.output_protocol:
            self._update_output_controls()
            return

        previous = self.output_protocol
        was_enabled = self.output_enabled
        if was_enabled and not self._send_backend_enabled(previous, False):
            self._restore_output_protocol(previous)
            return
        if not self._set_output_protocol(requested):
            if was_enabled:
                if not self._send_backend_enabled(previous, True):
                    self.output_enabled = False
            self._restore_output_protocol(previous)
            return
        if was_enabled and not self._send_backend_enabled(requested, True):
            route_restored = self._set_output_protocol(previous)
            backend_restored = self._send_backend_enabled(previous, True)
            if not route_restored or not backend_restored:
                self.output_enabled = False
            self._restore_output_protocol(previous)
            return

        self.output_protocol = requested
        self._update_output_controls()
        self.log_message(
            f"Output protocol set to {self._protocol_label(requested)}"
        )

    @staticmethod
    def _protocol_label(protocol):
        labels = {
            OUTPUT_PROTOCOL_UDP: "UDP",
            OUTPUT_PROTOCOL_FREETRACK: "FreeTrack 2.0",
            OUTPUT_PROTOCOL_NPCLIENT: "NPClient",
        }
        return labels.get(protocol, str(protocol))

    def _client_registration(self, protocol):
        if protocol == OUTPUT_PROTOCOL_FREETRACK:
            return self.freetrack_client_registration
        if protocol == OUTPUT_PROTOCOL_NPCLIENT:
            return self.npclient_registration
        return None

    def _restore_output_protocol(self, protocol):
        self.output_protocol_combo.blockSignals(True)
        index = self.output_protocol_combo.findData(protocol)
        if index >= 0:
            self.output_protocol_combo.setCurrentIndex(index)
        self.output_protocol_combo.blockSignals(False)
        self.output_protocol = protocol
        self._update_output_controls()

    def _set_output_protocol(self, protocol):
        if not safe_queue_put(
            self.output_control_queue,
            ("set_protocol", protocol),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            self.log_message("Failed to switch the orientation output protocol")
            return False
        return True

    def _send_backend_enabled(self, protocol, enabled):
        if protocol not in (
            OUTPUT_PROTOCOL_UDP,
            OUTPUT_PROTOCOL_FREETRACK,
            OUTPUT_PROTOCOL_NPCLIENT,
        ):
            self.log_message(f"Unsupported output protocol: {protocol}")
            return False

        registration = self._client_registration(protocol)
        if enabled and protocol == OUTPUT_PROTOCOL_UDP:
            try:
                ip = str(self.udp_ip_entry.text()).strip()
                port = int(self.udp_port_entry.text())
            except ValueError:
                self.log_message("Invalid UDP port number")
                return False
            if not safe_queue_put(
                self.output_control_queue,
                ("set_udp", ip, port),
                timeout=QUEUE_PUT_TIMEOUT,
            ):
                self.log_message("Failed to send UDP configuration")
                return False
        elif enabled and registration is not None:
            try:
                registration.register()
            except (OSError, RuntimeError) as error:
                self.log_message(
                    f"Could not register {self._protocol_label(protocol)} "
                    f"game client: {error}. If a game is running, close it "
                    "before updating the client DLLs."
                )
                return False

        if not safe_queue_put(
            self.output_control_queue,
            ("set_enabled", enabled),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            self.log_message(
                f"Failed to {'enable' if enabled else 'disable'} "
                f"{self._protocol_label(protocol)} output"
            )
            if enabled and registration is not None:
                try:
                    registration.restore()
                except OSError as error:
                    self.log_message(
                        f"Could not restore the previous "
                        f"{self._protocol_label(protocol)} registration: {error}"
                    )
            return False

        if not enabled and registration is not None:
            try:
                registration.restore()
            except OSError as error:
                self.log_message(
                    f"Could not restore the previous "
                    f"{self._protocol_label(protocol)} registration: {error}"
                )
        return True

    def toggle_output(self):
        if self.output_enabled:
            if not self._send_backend_enabled(self.output_protocol, False):
                return
            self.output_enabled = False
            self.log_message(
                f"{self._protocol_label(self.output_protocol)} output disabled"
            )
        else:
            if not self._send_backend_enabled(self.output_protocol, True):
                return
            self.output_enabled = True
            self.log_message(
                f"{self._protocol_label(self.output_protocol)} output enabled"
            )
        self._update_output_controls()

    def _update_output_controls(self):
        is_udp = self.output_protocol == OUTPUT_PROTOCOL_UDP
        self.udp_settings_widget.setVisible(is_udp)
        self.native_output_settings_btn.setVisible(not is_udp)
        self.udp_ip_entry.setEnabled(is_udp and not self.output_enabled)
        self.udp_port_entry.setEnabled(is_udp and not self.output_enabled)
        self.output_toggle_btn.setProperty(
            "status",
            "enabled" if self.output_enabled else "",
        )
        self.output_toggle_btn.style().polish(self.output_toggle_btn)
        self._refresh_output_button_text()

    def stable_height_hint(self):
        """Return enough height for either UDP or direct-output controls."""
        initial_udp_hidden = self.udp_settings_widget.isHidden()
        initial_native_hidden = self.native_output_settings_btn.isHidden()
        heights = [
            self.sizeHint().height(),
            self.minimumSizeHint().height(),
        ]
        try:
            for show_udp, show_native in ((True, False), (False, True)):
                self.udp_settings_widget.setVisible(show_udp)
                self.native_output_settings_btn.setVisible(show_native)
                self.content_layout.invalidate()
                self.content_layout.activate()
                self.layout().invalidate()
                self.layout().activate()
                heights.append(
                    max(
                        self.sizeHint().height(),
                        self.minimumSizeHint().height(),
                    )
                )
        finally:
            self.udp_settings_widget.setVisible(not initial_udp_hidden)
            self.native_output_settings_btn.setVisible(
                not initial_native_hidden
            )
            self.content_layout.invalidate()
            self.content_layout.activate()
            self.layout().invalidate()
            self.layout().activate()
        # Leave room for Qt frame/layout rounding after the parent geometry settles.
        return max(heights) + 2

    def _open_native_output_settings(self):
        if self.output_protocol != OUTPUT_PROTOCOL_UDP:
            self.native_output_dialog.open_window()

    def update_output_status(self, status):
        if status == "ready":
            if not self.sync_output_protocol():
                self.output_enabled = False
                self._update_output_controls()
                return
            if self.output_enabled and not self._send_backend_enabled(
                self.output_protocol,
                True,
            ):
                self.output_enabled = False
                self._update_output_controls()
            return
        if status == "error":
            self.output_enabled = False
            registration = self._client_registration(self.output_protocol)
            if registration is not None:
                try:
                    registration.restore()
                except OSError as error:
                    self.log_message(
                        f"Could not restore the previous "
                        f"{self._protocol_label(self.output_protocol)} "
                        f"registration: {error}"
                    )
            self._update_output_controls()
            self.output_toggle_btn.setProperty("status", "error")
            self.output_toggle_btn.style().polish(self.output_toggle_btn)
            self.log_message(
                f"{self._protocol_label(self.output_protocol)} output failed; "
                "see the application log"
            )

    def sync_output_protocol(self):
        return self._set_output_protocol(self.output_protocol)

    def restore_client_registrations(self):
        errors = []
        for registration in (
            self.freetrack_client_registration,
            self.npclient_registration,
        ):
            if registration is not None:
                try:
                    registration.restore()
                except OSError as error:
                    errors.append(error)
        if errors:
            raise errors[0]

    def set_udp_config(self, ip, port):
        try:
            self._udp_ip = str(ip)
            self._udp_port = str(port)
            self.udp_ip_entry.setText(self._udp_ip)
            self.udp_port_entry.setText(self._udp_port)
            if self.output_enabled and self.output_protocol == OUTPUT_PROTOCOL_UDP:
                self._send_backend_enabled(OUTPUT_PROTOCOL_UDP, True)
        except (TypeError, ValueError):
            self.log_message("Invalid UDP configuration")

    def get_udp_config(self):
        try:
            return str(self.udp_ip_entry.text()), int(self.udp_port_entry.text())
        except ValueError:
            return DEFAULT_UDP_IP, DEFAULT_UDP_PORT

    def is_udp_enabled(self):
        return self.output_enabled and self.output_protocol == OUTPUT_PROTOCOL_UDP

    def enable_udp(self):
        if self.output_protocol == OUTPUT_PROTOCOL_UDP and not self.output_enabled:
            self.toggle_output()

    def disable_udp(self):
        if self.is_udp_enabled():
            self.toggle_output()

    def get_output_protocol(self):
        return self.output_protocol

    def get_prefs(self):
        return {
            "udp_ip": self.udp_ip_entry.text(),
            "udp_port": self.udp_port_entry.text(),
            "output_protocol": self.output_protocol,
        }

    def set_prefs(self, prefs):
        if not isinstance(prefs, dict):
            return
        network = prefs.get("network", prefs)
        if not isinstance(network, dict):
            self.log_message("Ignoring invalid network preferences")
            return
        if network.get("udp_ip"):
            self._udp_ip = str(network["udp_ip"])
            self.udp_ip_entry.setText(self._udp_ip)
        if network.get("udp_port"):
            self._udp_port = str(network["udp_port"])
            self.udp_port_entry.setText(self._udp_port)
        protocol = network.get("output_protocol", DEFAULT_OUTPUT_PROTOCOL)
        index = self.output_protocol_combo.findData(protocol)
        if index < 0:
            self.log_message(
                f"Ignoring unsupported output protocol preference: {protocol}"
            )
            index = self.output_protocol_combo.findData(DEFAULT_OUTPUT_PROTOCOL)
        self.output_protocol_combo.setCurrentIndex(index)

    def _refresh_output_button_text(self):
        protocol = self._protocol_label(self.output_protocol)
        action = "Stop" if self.output_enabled else "Start"
        main_label = f"{action} {protocol}"
        if hasattr(self.output_toggle_btn, "setParts"):
            self.output_toggle_btn.setParts(main_label, self._output_rate_text)
        else:
            self.output_toggle_btn.setText(
                f"{main_label} - {self._output_rate_text}"
            )

    def update_send_rate(self, rate):
        self._output_rate_text = f"{float(rate):.1f} msg/s"
        self._refresh_output_button_text()

    def reset_status(self):
        self.update_send_rate(0.0)
