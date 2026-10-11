"""
PyQt connection panel for serial input and selectable orientation output.

"""

from PyQt5.QtWidgets import (
    QLabel, QComboBox, QLineEdit, QFrame, QGridLayout, QHBoxLayout,
    QVBoxLayout, QSizePolicy
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFontMetrics, QIntValidator

from .base_panel import (
    BasePanelQt, CONTENT_MARGINS, DEFAULT_SPACING, BUTTON_EXTRA_HEIGHT,
    BUTTON_MIN_HEIGHT, LINE_THICKNESS
)
from .two_line_button import TwoLineButton

from src.config.config import (
    DEFAULT_SERIAL_PORT,
    DEFAULT_SERIAL_BAUD,
    DEFAULT_UDP_IP,
    DEFAULT_UDP_PORT,
    DEFAULT_OUTPUT_PROTOCOL,
    OUTPUT_PROTOCOL_UDP,
    OUTPUT_PROTOCOL_FREETRACK,
    OUTPUT_PROTOCOL_NPCLIENT,
    QUEUE_PUT_TIMEOUT,
)
from src.util.error_utils import safe_queue_put


class ConnectionPanelQt(BasePanelQt):
    """Input serial settings and one selectable output protocol."""

    def __init__(
        self,
        parent,
        serial_control_queue,
        output_control_queue,
        message_callback,
        padding=6,
        on_serial_stop=None,
        freetrack_client_registration=None,
        npclient_registration=None,
    ):
        """
        Initialize the PyQt Connection Panel.

        Args:
            parent: Parent PyQt widget
            serial_control_queue: Queue for sending commands to serial worker
            output_control_queue: Queue for output selection, configuration and state
            freetrack_client_registration: Optional per-user client DLL registration
            npclient_registration: Optional per-user NPClient DLL registration
            message_callback: Callable to display messages
            padding: Padding for the frame (default: 6)
            on_serial_stop: Optional callback invoked when serial is stopped
        """
        self.serial_control_queue = serial_control_queue
        self.output_control_queue = output_control_queue
        self.freetrack_client_registration = freetrack_client_registration
        self.npclient_registration = npclient_registration
        self.on_serial_stop = on_serial_stop
        self.padding = padding

        # Serial state (same as former SerialPanelQt)
        self._port_value = DEFAULT_SERIAL_PORT
        self._baud_value = str(DEFAULT_SERIAL_BAUD)
        self._is_running = False
        self._connection_status = "stopped"  # stopped, starting, connected, error
        self._fusion_processing = False  # Track if fusion worker is actively processing

        # Timer for data activity timeout
        self._data_activity_timer = QTimer()
        self._data_activity_timer.timeout.connect(self._on_data_timeout)
        self._data_activity_timer.setSingleShot(True)
        self._last_data_time = 0

        # Output state
        self._udp_ip = DEFAULT_UDP_IP
        self._udp_port = str(DEFAULT_UDP_PORT)
        self.output_protocol = DEFAULT_OUTPUT_PROTOCOL
        self.output_enabled = False

        # Rate text placeholders (shown inside buttons)
        self._serial_rate_text = "0.0 msg/s"
        self._output_rate_text = "0.0 msg/s"

        super().__init__(parent, "Connection", message_callback=message_callback)

    def setup_ui(self):
        """Build the combined connection panel UI."""
        main_layout = QGridLayout(self)
        main_layout.setContentsMargins(*CONTENT_MARGINS)
        main_layout.setSpacing(DEFAULT_SPACING)

        # Build a compact two-column layout: left = inputs, right = buttons
        controls_frame = QFrame()
        controls_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Use consistent padding with Orientation panel
        # Reduce internal padding to match Orientation panel
        # Clear widget margins here: main_layout already applies CONTENT_MARGINS
        controls_frame.setContentsMargins(0, 0, 0, 0)

        # Horizontal split: left inputs, right controls
        from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout
        outer = QHBoxLayout(controls_frame)
        # Keep outer layout compact; inner widgets manage their own spacing
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(DEFAULT_SPACING)

        # Left: inputs area split into two equal columns with a vertical divider
        left_widget = QFrame()
        left_widget_layout = QHBoxLayout(left_widget)
        left_widget_layout.setContentsMargins(0, 0, 0, 0)
        left_widget_layout.setSpacing(DEFAULT_SPACING)

        # Input: serial settings and connection control
        left_col = QFrame()
        left_col_layout = QVBoxLayout(left_col)
        # Match orientation panel inner padding so inputs align visually with viz/value panels
        left_col_layout.setContentsMargins(*CONTENT_MARGINS)
        left_col_layout.setSpacing(DEFAULT_SPACING)
        left_col_layout.addWidget(QLabel("Input"))

        # Serial row
        serial_h = QHBoxLayout()
        serial_h.setContentsMargins(0, 0, 0, 0)
        lbl_serial = QLabel("Serial Port:")
        lbl_serial.setToolTip("Serial port to open (e.g., COM3)")
        lbl_serial.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lbl_serial.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        serial_h.addWidget(lbl_serial)
        # Push the input widget to the right within the column
        serial_h.addStretch()

        self.port_combo = QComboBox()
        ports = [f"COM{i}" for i in range(100)]
        self.port_combo.addItems(ports)
        self.port_combo.setCurrentText(self._port_value)
        self.port_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.port_combo.currentTextChanged.connect(self._on_port_changed)
        serial_h.addWidget(self.port_combo)
        left_col_layout.addLayout(serial_h)

        # Baud row
        baud_h = QHBoxLayout()
        baud_h.setContentsMargins(0, 0, 0, 0)
        lbl_baud = QLabel("Baud:")
        lbl_baud.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lbl_baud.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        baud_h.addWidget(lbl_baud)
        baud_h.addStretch()

        self.baud_combo = QComboBox()
        baud_rates = ["9600", "19200", "38400", "57600", "115200", "230400", "250000", "500000", "1000000"]
        self.baud_combo.addItems(baud_rates)
        self.baud_combo.setCurrentText(self._baud_value)
        self.baud_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.baud_combo.currentTextChanged.connect(self._on_baud_changed)
        baud_h.addWidget(self.baud_combo)
        left_col_layout.addLayout(baud_h)

        self.toggle_button = TwoLineButton("Start Serial", self._serial_rate_text)
        self.toggle_button.setMinimumHeight(BUTTON_MIN_HEIGHT + BUTTON_EXTRA_HEIGHT)
        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toggle_button.setProperty('status', '')
        self.toggle_button.clicked.connect(self.toggle_serial)
        left_col_layout.addWidget(self.toggle_button)
        left_col_layout.addStretch()

        # Output: protocol-specific settings
        right_col = QFrame()
        right_col_layout = QVBoxLayout(right_col)
        # Match orientation panel internal padding for symmetric layout
        right_col_layout.setContentsMargins(*CONTENT_MARGINS)
        right_col_layout.setSpacing(DEFAULT_SPACING)
        right_col_layout.addWidget(QLabel("Output"))

        protocol_h = QHBoxLayout()
        protocol_h.addWidget(QLabel("Protocol:"))
        self.output_protocol_combo = QComboBox()
        self.output_protocol_combo.addItem("UDP (opentrack)", OUTPUT_PROTOCOL_UDP)
        self.output_protocol_combo.addItem(
            "FreeTrack 2.0 (Direct)", OUTPUT_PROTOCOL_FREETRACK
        )
        self.output_protocol_combo.addItem(
            "NPClient (TrackIR-compatible)", OUTPUT_PROTOCOL_NPCLIENT
        )
        protocol_h.addWidget(self.output_protocol_combo, 1)
        right_col_layout.addLayout(protocol_h)

        self.udp_settings_widget = QFrame(right_col)
        udp_settings_layout = QVBoxLayout(self.udp_settings_widget)
        udp_settings_layout.setContentsMargins(0, 0, 0, 0)
        udp_settings_layout.setSpacing(DEFAULT_SPACING)

        # IP row
        ip_h = QHBoxLayout()
        ip_h.setContentsMargins(0, 0, 0, 0)
        lbl_ip = QLabel("IP:")
        lbl_ip.setToolTip("Destination IP for UDP packets (e.g., 127.0.0.1)")
        lbl_ip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lbl_ip.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        ip_h.addWidget(lbl_ip)
        ip_h.addStretch()

        self.udp_ip_entry = QLineEdit()
        self.udp_ip_entry.setText(self._udp_ip)
        self.udp_ip_entry.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.udp_ip_entry.setAlignment(Qt.AlignRight)
        self.udp_ip_entry.textChanged.connect(self._on_ip_changed)
        ip_h.addWidget(self.udp_ip_entry)
        udp_settings_layout.addLayout(ip_h)

        # UDP Port row
        port_h = QHBoxLayout()
        port_h.setContentsMargins(0, 0, 0, 0)
        lbl_udp_port = QLabel("Port:")
        lbl_udp_port.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lbl_udp_port.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        port_h.addWidget(lbl_udp_port)
        port_h.addStretch()

        self.udp_port_entry = QLineEdit()
        self.udp_port_entry.setText(self._udp_port)
        self.udp_port_entry.setValidator(QIntValidator(1, 65535))
        self.udp_port_entry.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.udp_port_entry.setAlignment(Qt.AlignRight)
        self.udp_port_entry.textChanged.connect(self._on_udp_port_changed)
        port_h.addWidget(self.udp_port_entry)
        udp_settings_layout.addLayout(port_h)
        right_col_layout.addWidget(self.udp_settings_widget)

        # Align the input and UDP setting labels consistently.
        try:
            fm = QFontMetrics(lbl_serial.font())
            labels = [lbl_serial, lbl_baud, lbl_ip, lbl_udp_port]
            maxw = max(fm.horizontalAdvance(lbl.text()) for lbl in labels) + 8
            for lbl in labels:
                lbl.setFixedWidth(maxw)
                lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        except Exception:
            pass

        # Keep all serial controls on the input side of the panel.
        left_widget_layout.addWidget(left_col)

        # Output protocol controls and selected worker lifecycle.
        right_widget = QFrame()
        right_layout = QVBoxLayout(right_widget)
        # Apply the same content margins used by Orientation values column
        right_layout.setContentsMargins(*CONTENT_MARGINS)
        right_layout.setSpacing(DEFAULT_SPACING)
        # Top-align controls so buttons sit at the top of the right column
        right_layout.setAlignment(Qt.AlignTop)

        right_layout.addWidget(right_col)

        self.output_toggle_btn = TwoLineButton("Start UDP", self._output_rate_text)
        self.output_toggle_btn.setMinimumHeight(BUTTON_MIN_HEIGHT + BUTTON_EXTRA_HEIGHT)
        self.output_toggle_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.output_toggle_btn.setProperty('status', '')
        self.output_toggle_btn.clicked.connect(self.toggle_output)
        self.udp_toggle_btn = self.output_toggle_btn
        right_layout.addWidget(self.output_toggle_btn)

        # Add a bottom stretch so remaining space is left below the buttons
        right_layout.addStretch()

        outer.addWidget(left_widget, 1)

        # vertical divider between left and right (keep as visual separator)
        vline = QFrame()
        vline.setFrameShape(QFrame.VLine)
        vline.setFrameShadow(QFrame.Sunken)
        vline.setFixedWidth(LINE_THICKNESS)
        vline.setStyleSheet("background-color: rgba(120,120,120,0.25);")
        outer.addWidget(vline)

        outer.addWidget(right_widget, 1)

        main_layout.addWidget(controls_frame, 0, 0)
        # Adjust panel height to accommodate larger buttons while keeping it reasonably slim
        # controls_frame.setFixedHeight(120)  # removed to allow flexible layout sizing
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.output_protocol_combo.currentIndexChanged.connect(
            self._on_output_protocol_changed
        )
        self._update_output_controls()
        self._refresh_serial_button_text()
        self._refresh_output_button_text()

    # ------------------------------------------------------------------
    # Serial handling (identical logic to former SerialPanelQt)
    # ------------------------------------------------------------------

    def _on_port_changed(self, port):
        """Handle port selection change."""
        self._port_value = port

    def _on_baud_changed(self, baud):
        """Handle baud rate selection change."""
        self._baud_value = baud

    def toggle_serial(self):
        """Toggle serial port start/stop."""
        if not self._is_running:
            self._start_serial()
        else:
            self._stop_serial()

    def _start_serial(self):
        """Start serial communication."""
        port = self.port_combo.currentText()
        try:
            baud = int(self.baud_combo.currentText())
        except ValueError:
            baud = DEFAULT_SERIAL_BAUD

        if not safe_queue_put(
            self.serial_control_queue,
            ('start', port, baud),
            timeout=QUEUE_PUT_TIMEOUT
        ):
            self.log_message("Failed to request serial start")
            return

        self._is_running = True
        self._connection_status = "starting"
        # Use two-line parts so the rate stays on the sub-line
        try:
            if hasattr(self.toggle_button, 'setParts'):
                self.toggle_button.setParts("Stop Serial", self._serial_rate_text)
            else:
                self.toggle_button.setText("Stop Serial")
        except Exception:
            try:
                self.toggle_button.setText("Stop Serial")
            except Exception:
                pass
        self.toggle_button.setProperty('status', 'warning')
        self.toggle_button.style().polish(self.toggle_button)
        # Ensure rate text remains visible when changing base text
        try:
            self._refresh_serial_button_text()
        except Exception:
            pass

        self.port_combo.setEnabled(False)
        self.baud_combo.setEnabled(False)

        self.log_message(f"Start requested on {port} @ {baud}")
    def _stop_serial(self):
        """Stop serial communication."""
        if not safe_queue_put(
            self.serial_control_queue,
            ('stop',),
            timeout=QUEUE_PUT_TIMEOUT
        ):
            self.log_message("Failed to request serial stop")
            return

        self._is_running = False
        self._connection_status = "stopped"
        try:
            if hasattr(self.toggle_button, 'setParts'):
                self.toggle_button.setParts("Start Serial", self._serial_rate_text)
            else:
                self.toggle_button.setText("Start Serial")
        except Exception:
            try:
                self.toggle_button.setText("Start Serial")
            except Exception:
                pass
        # Neutral visual state so button stays visibly pressable when stopped
        self.toggle_button.setProperty('status', '')
        self.toggle_button.style().polish(self.toggle_button)
        try:
            self._refresh_serial_button_text()
        except Exception:
            pass

        self.port_combo.setEnabled(True)
        self.baud_combo.setEnabled(True)

        self._data_activity_timer.stop()
        self.log_message("Stop requested")

        try:
            if callable(self.on_serial_stop):
                self.on_serial_stop()
        except Exception:
            try:
                self.log_message("on_stop handler raised an exception")
            except Exception:
                pass

    def update_connection_status(self, status: str):
        """Update connection status from serial worker.

        This method must update the UI even if the panel's internal _is_running
        flag is False; sometimes the serial worker can stop externally and the
        GUI needs to reflect that state immediately. Handle 'stopped',
        'connected', and 'error' explicitly.
        """
        # Always reflect the latest reported connection status
        self._connection_status = status
        port = self.port_combo.currentText() if hasattr(self, 'port_combo') else None
        baud = self.baud_combo.currentText() if hasattr(self, 'baud_combo') else None

        if status == "connected":
            # Mark as running if not already
            self._is_running = True
            # Show waiting state on the button until fusion is active
            self.toggle_button.setProperty('status', 'warning')
            self.toggle_button.style().polish(self.toggle_button)
        elif status == "error":
            # Treat error as a stopped state from the UI perspective
            self._is_running = False

            # Re-enable port and baud selection on error so user can try different settings
            try:
                self.port_combo.setEnabled(True)
                self.baud_combo.setEnabled(True)
            except Exception:
                pass

            # Reflect error on the button
            try:
                if hasattr(self.toggle_button, 'setParts'):
                    self.toggle_button.setParts("Start Serial", self._serial_rate_text)
                else:
                    self.toggle_button.setText("Start Serial")
            except Exception:
                try:
                    self.toggle_button.setText("Start Serial")
                except Exception:
                    pass
            try:
                self.toggle_button.setProperty('status', 'error')
                self.toggle_button.style().polish(self.toggle_button)
            except Exception:
                pass
            try:
                self._refresh_serial_button_text()
            except Exception:
                pass
            try:
                self._data_activity_timer.stop()
            except Exception:
                pass
        elif status == "stopped":
            # External stop: ensure UI resets to stopped state immediately
            self._is_running = False
            try:
                if hasattr(self.toggle_button, 'setParts'):
                    self.toggle_button.setParts("Start Serial", self._serial_rate_text)
                else:
                    self.toggle_button.setText("Start Serial")
            except Exception:
                try:
                    self.toggle_button.setText("Start Serial")
                except Exception:
                    pass
            try:
                self.toggle_button.setProperty('status', '')
                self.toggle_button.style().polish(self.toggle_button)
            except Exception:
                pass
            try:
                self.port_combo.setEnabled(True)
                self.baud_combo.setEnabled(True)
            except Exception:
                pass
            try:
                self._data_activity_timer.stop()
            except Exception:
                pass
            try:
                self.reset_status()
            except Exception:
                pass
        else:
            # Unknown status: just store it and attempt a safe UI refresh
            try:
                self.toggle_button.style().polish(self.toggle_button)
            except Exception:
                pass

    def update_data_activity(self):
        """Called when data is received - indicates active connection."""
        if not self._is_running or self._connection_status != "connected":
            return

        import time
        self._last_data_time = time.time()

        # Only show green when both connected AND fusion is processing
        if self._fusion_processing:
            # Show enabled/green state on the button when fusion is active
            self.toggle_button.setProperty('status', 'enabled')
            self.toggle_button.style().polish(self.toggle_button)

        # Reset timeout timer (5 seconds without data = back to waiting)
        self._data_activity_timer.start(5000)

    def _on_data_timeout(self):
        """Called when no data received for timeout period."""
        if not self._is_running or self._connection_status != "connected":
            return

        # When data times out, show waiting state on the button
        self.toggle_button.setProperty('status', 'warning')
        self.toggle_button.style().polish(self.toggle_button)

    def update_fusion_status(self, is_active):
        """Update fusion processing status from statusQueue."""
        self._fusion_processing = is_active

        if self._is_running and self._connection_status == "connected":
            if is_active:
                # Show enabled on button
                self.toggle_button.setProperty('status', 'enabled')
            else:
                self.toggle_button.setProperty('status', 'warning')
            self.toggle_button.style().polish(self.toggle_button)

    # ------------------------------------------------------------------
    # Output selection and lifecycle
    # ------------------------------------------------------------------

    def _on_ip_changed(self, text):
        """Handle IP address entry change."""
        self._udp_ip = text

    def _on_udp_port_changed(self, text):
        """Handle UDP port entry change."""
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
        self.log_message(f"Output protocol set to {self._protocol_label(requested)}")

    def _protocol_label(self, protocol):
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
            ('set_protocol', protocol),
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
                    f"Could not register {self._protocol_label(protocol)} game client: {error}. "
                    "If a game is running, close it before updating the client DLLs."
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
                        f"Could not restore the previous {self._protocol_label(protocol)} registration: {error}"
                    )
            return False
        if not enabled and registration is not None:
            try:
                registration.restore()
            except OSError as error:
                self.log_message(
                    f"Could not restore the previous {self._protocol_label(protocol)} registration: {error}"
                )
        return True

    def toggle_output(self):
        """Start or stop the currently selected output protocol."""
        if self.output_enabled:
            if not self._send_backend_enabled(self.output_protocol, False):
                return
            self.output_enabled = False
            self.log_message(f"{self._protocol_label(self.output_protocol)} output disabled")
        else:
            if not self._send_backend_enabled(self.output_protocol, True):
                return
            self.output_enabled = True
            self.log_message(f"{self._protocol_label(self.output_protocol)} output enabled")
        self._update_output_controls()

    def _update_output_controls(self):
        is_udp = self.output_protocol == OUTPUT_PROTOCOL_UDP
        self.udp_settings_widget.setVisible(is_udp)
        self.udp_ip_entry.setEnabled(is_udp and not self.output_enabled)
        self.udp_port_entry.setEnabled(is_udp and not self.output_enabled)
        self.output_toggle_btn.setProperty(
            'status',
            'enabled' if self.output_enabled else '',
        )
        self.output_toggle_btn.style().polish(self.output_toggle_btn)
        self._refresh_output_button_text()

    def update_output_status(self, status):
        """Reflect output worker startup and backend failures."""
        if status == "ready":
            if not self.sync_output_protocol():
                self.output_enabled = False
                self._update_output_controls()
                return
            if self.output_enabled and not self._send_backend_enabled(self.output_protocol, True):
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
                        f"Could not restore the previous {self._protocol_label(self.output_protocol)} registration: {error}"
                    )
            self._update_output_controls()
            self.output_toggle_btn.setProperty('status', 'error')
            self.output_toggle_btn.style().polish(self.output_toggle_btn)
            self.log_message(
                f"{self._protocol_label(self.output_protocol)} output failed; "
                "see the application log"
            )

    def sync_output_protocol(self):
        """Reapply the selected protocol after the output worker restarts."""
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
        """Set UDP configuration programmatically."""
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
        """Get current UDP configuration."""
        try:
            ip = str(self.udp_ip_entry.text())
            port = int(self.udp_port_entry.text())
            return (ip, port)
        except ValueError:
            return (DEFAULT_UDP_IP, DEFAULT_UDP_PORT)

    def is_udp_enabled(self):
        """Check if UDP is currently enabled."""
        return self.output_enabled and self.output_protocol == OUTPUT_PROTOCOL_UDP

    def enable_udp(self):
        """Enable UDP if currently disabled."""
        if self.output_protocol == OUTPUT_PROTOCOL_UDP and not self.output_enabled:
            self.toggle_output()

    def disable_udp(self):
        """Disable UDP if currently enabled."""
        if self.is_udp_enabled():
            self.toggle_output()

    def get_output_protocol(self):
        return self.output_protocol

    # ------------------------------------------------------------------
    # Preferences (combined; preserves the existing 'serial'/'network'
    # config sections so saved preference files remain compatible)
    # ------------------------------------------------------------------

    def get_prefs(self):
        """
        Get current preferences for persistence.

        Returns:
            dict: {'serial': {...}, 'network': {...}} matching the former
            per-panel preference structure.
        """
        return {
            'serial': {
                'com_port': self.port_combo.currentText(),
                'baud_rate': self.baud_combo.currentText()
            },
            'network': {
                'udp_ip': self.udp_ip_entry.text(),
                'udp_port': self.udp_port_entry.text(),
                'output_protocol': self.output_protocol,
            }
        }

    def set_prefs(self, prefs):
        """
        Apply saved preferences.

        Args:
            prefs: Dictionary with optional 'serial' and 'network' keys,
            each holding the same structure as the former per-panel prefs.
        """
        if not isinstance(prefs, dict):
            return

        serial_prefs = prefs.get('serial')
        if serial_prefs:
            if 'com_port' in serial_prefs and serial_prefs['com_port']:
                port = serial_prefs['com_port']
                if self.port_combo.findText(port) == -1:
                    self.port_combo.addItem(port)
                self.port_combo.setCurrentText(port)
                self._port_value = port

            if 'baud_rate' in serial_prefs and serial_prefs['baud_rate']:
                baud = serial_prefs['baud_rate']
                self.baud_combo.setCurrentText(str(baud))
                self._baud_value = str(baud)

        network_prefs = prefs.get('network')
        if network_prefs:
            if 'udp_ip' in network_prefs and network_prefs['udp_ip']:
                self._udp_ip = network_prefs['udp_ip']
                self.udp_ip_entry.setText(self._udp_ip)

            if 'udp_port' in network_prefs and network_prefs['udp_port']:
                self._udp_port = network_prefs['udp_port']
                self.udp_port_entry.setText(self._udp_port)

            protocol = network_prefs.get(
                'output_protocol',
                DEFAULT_OUTPUT_PROTOCOL,
            )
            protocol_index = self.output_protocol_combo.findData(protocol)
            if protocol_index < 0:
                self.log_message(
                    f"Ignoring unsupported output protocol preference: {protocol}"
                )
                protocol_index = self.output_protocol_combo.findData(
                    DEFAULT_OUTPUT_PROTOCOL
                )
            self.output_protocol_combo.setCurrentIndex(protocol_index)

    # -------------------------
    # Status-area helpers
    # -------------------------
    def _refresh_serial_button_text(self):
        """Refresh the serial button to show current main label and the rate on the sub-line."""
        try:
            base = getattr(self.toggle_button, '_main', self.toggle_button.text())
            # Ensure we only keep the primary label in main and put rate in subline
            main_label = base.split(' - ')[0].split('\n')[0]
            if hasattr(self.toggle_button, 'setParts'):
                self.toggle_button.setParts(main_label, self._serial_rate_text)
            else:
                self.toggle_button.setText(f"{main_label} - {self._serial_rate_text}")
        except Exception:
            pass

    def _refresh_output_button_text(self):
        """Refresh the output button label and published sample rate."""
        try:
            protocol_label = self._protocol_label(self.output_protocol)
            action = "Stop" if self.output_enabled else "Start"
            main_label = f"{action} {protocol_label}"
            if hasattr(self.output_toggle_btn, 'setParts'):
                self.output_toggle_btn.setParts(main_label, self._output_rate_text)
            else:
                self.output_toggle_btn.setText(
                    f"{main_label} - {self._output_rate_text}"
                )
        except Exception:
            pass

    def update_message_rate(self, rate):
        """
        Update the message rate display (shown inside Start/Stop Serial button).

        Args:
            rate: Messages per second (float)
        """
        try:
            self._serial_rate_text = f"{float(rate):.1f} msg/s"
            self._refresh_serial_button_text()
        except Exception:
            pass

    def update_send_rate(self, rate):
        """
        Update the selected output's send rate display.

        Args:
            rate: Packets per second (float)
        """
        try:
            self._output_rate_text = f"{float(rate):.1f} msg/s"
            self._refresh_output_button_text()
        except Exception:
            pass

    def update_all(self, msg_rate=None, send_rate=None):
        """
        Update multiple metrics at once.
        """
        if msg_rate is not None:
            self.update_message_rate(msg_rate)
        if send_rate is not None:
            self.update_send_rate(send_rate)

    def reset_status(self):
        """Reset message/send rates to zero (used when serial stops)."""
        try:
            self._serial_rate_text = "0.0 msg/s"
            self._output_rate_text = "0.0 msg/s"
            self._refresh_serial_button_text()
            self._refresh_output_button_text()
        except Exception:
            pass

    def get_status_values(self):
        """Return the current status values as a dict."""
        try:
            return {
                'msg_rate': self._serial_rate_text,
                'send_rate': self._output_rate_text
            }
        except Exception:
            return {'msg_rate': None, 'send_rate': None}
