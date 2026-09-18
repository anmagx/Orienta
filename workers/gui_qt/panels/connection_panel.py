"""
PyQt Connection Panel for orienta GUI.

Merges the former Serial Reader and Network Settings panels into a single
panel. Input boxes for serial port, baud rate, UDP IP, and UDP port are laid
out on the left; the Start/Stop Serial and Start/Stop UDP buttons (with their
status indicators) are laid out on the right.
"""

from PyQt5.QtWidgets import (QLabel, QComboBox, QLineEdit, QPushButton,
                             QFrame, QGridLayout, QSizePolicy)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QIntValidator

from .base_panel import BasePanelQt
from config.config import (
    DEFAULT_SERIAL_PORT,
    DEFAULT_SERIAL_BAUD,
    DEFAULT_UDP_IP,
    DEFAULT_UDP_PORT,
    QUEUE_PUT_TIMEOUT
)
from util.error_utils import safe_queue_put


class ConnectionPanelQt(BasePanelQt):
    """Combined serial + UDP connection panel."""

    def __init__(self, parent, serial_control_queue, udp_control_queue, message_callback,
                 padding=6, on_serial_stop=None):
        """
        Initialize the PyQt Connection Panel.

        Args:
            parent: Parent PyQt widget
            serial_control_queue: Queue for sending commands to serial worker
            udp_control_queue: Queue for sending commands to UDP worker
            message_callback: Callable to display messages
            padding: Padding for the frame (default: 6)
            on_serial_stop: Optional callback invoked when serial is stopped
        """
        self.serial_control_queue = serial_control_queue
        self.udp_control_queue = udp_control_queue
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

        # UDP state (same as former NetworkPanelQt)
        self._udp_ip = DEFAULT_UDP_IP
        self._udp_port = str(DEFAULT_UDP_PORT)
        self.udp_enabled = False
        self._udp_btn_text = "Start UDP"
        self._udp_status_text = "UDP Disabled"

        super().__init__(parent, "Connection", message_callback=message_callback)

    def setup_ui(self):
        """Build the combined connection panel UI."""
        main_layout = QGridLayout(self)
        main_layout.setContentsMargins(4, 6, 4, 6)
        main_layout.setSpacing(4)

        # Build a compact two-column layout: left = inputs, right = buttons
        controls_frame = QFrame()
        controls_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Reduce vertical padding so the panel is slimmer
        controls_frame.setContentsMargins(4, 2, 4, 2)

        # Horizontal split: left inputs, right controls
        from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout
        outer = QHBoxLayout(controls_frame)
        outer.setContentsMargins(6, 2, 6, 2)
        outer.setSpacing(12)

        # Left: inputs area (stacked rows)
        left_widget = QFrame()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # Row: Serial inputs (label above inputs)
        serial_row = QVBoxLayout()
        lbl_serial = QLabel("Serial Port")
        lbl_serial.setToolTip("Serial port to open (e.g., COM3)")
        serial_row.addWidget(lbl_serial)
        serial_inputs = QHBoxLayout()
        self.port_combo = QComboBox()
        ports = [f"COM{i}" for i in range(100)]
        self.port_combo.addItems(ports)
        self.port_combo.setCurrentText(self._port_value)
        self.port_combo.setFixedWidth(140)
        self.port_combo.currentTextChanged.connect(self._on_port_changed)
        serial_inputs.addWidget(self.port_combo)

        lbl_baud = QLabel("Baud")
        lbl_baud.setContentsMargins(8, 0, 8, 0)
        serial_inputs.addWidget(lbl_baud)
        self.baud_combo = QComboBox()
        baud_rates = ["9600", "19200", "38400", "57600", "115200", "230400", "250000", "500000", "1000000"]
        self.baud_combo.addItems(baud_rates)
        self.baud_combo.setCurrentText(self._baud_value)
        self.baud_combo.setFixedWidth(140)
        self.baud_combo.currentTextChanged.connect(self._on_baud_changed)
        serial_inputs.addWidget(self.baud_combo)

        serial_row.addLayout(serial_inputs)
        left_layout.addLayout(serial_row)

        # Row: UDP inputs (label above inputs)
        udp_row = QVBoxLayout()
        lbl_ip = QLabel("IP")
        lbl_ip.setToolTip("Destination IP for UDP packets (e.g., 127.0.0.1)")
        udp_row.addWidget(lbl_ip)
        udp_inputs = QHBoxLayout()
        self.udp_ip_entry = QLineEdit()
        self.udp_ip_entry.setText(self._udp_ip)
        self.udp_ip_entry.setFixedWidth(140)
        self.udp_ip_entry.textChanged.connect(self._on_ip_changed)
        udp_inputs.addWidget(self.udp_ip_entry)

        lbl_udp_port = QLabel("UDP Port")
        lbl_udp_port.setContentsMargins(8, 0, 8, 0)
        udp_inputs.addWidget(lbl_udp_port)
        self.udp_port_entry = QLineEdit()
        self.udp_port_entry.setText(self._udp_port)
        self.udp_port_entry.setFixedWidth(140)
        self.udp_port_entry.setValidator(QIntValidator(1, 65535))
        self.udp_port_entry.textChanged.connect(self._on_udp_port_changed)
        udp_inputs.addWidget(self.udp_port_entry)

        udp_row.addLayout(udp_inputs)
        left_layout.addLayout(udp_row)

        # Right: controls area (buttons & status stacked)
        right_widget = QFrame()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        # Serial status + button
        self.status_label = QLabel("Stopped")
        self.status_label.setProperty("status", "disabled")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.toggle_button = QPushButton("Start Serial")
        self.toggle_button.setFixedWidth(120)
        self.toggle_button.clicked.connect(self.toggle_serial)
        right_layout.addWidget(self.status_label)
        right_layout.addWidget(self.toggle_button)

        # Divider between serial and UDP controls (short)
        mid_div = QFrame()
        mid_div.setFrameShape(QFrame.HLine)
        mid_div.setFrameShadow(QFrame.Sunken)
        right_layout.addWidget(mid_div)

        # UDP status + button
        self.udp_status_label = QLabel(self._udp_status_text)
        self.udp_status_label.setProperty("status", "disabled")
        self.udp_status_label.setAlignment(Qt.AlignCenter)
        self.udp_toggle_btn = QPushButton(self._udp_btn_text)
        self.udp_toggle_btn.setFixedWidth(120)
        self.udp_toggle_btn.clicked.connect(self.toggle_udp)
        right_layout.addWidget(self.udp_status_label)
        right_layout.addWidget(self.udp_toggle_btn)

        # Make left and right take equal horizontal space
        outer.addWidget(left_widget, 1)

        # vertical divider between left and right
        vline = QFrame()
        vline.setFrameShape(QFrame.VLine)
        vline.setFrameShadow(QFrame.Sunken)
        vline.setFixedWidth(2)
        vline.setStyleSheet("background-color: rgba(120,120,120,0.4);")
        outer.addWidget(vline)

        outer.addWidget(right_widget, 1)

        main_layout.addWidget(controls_frame, 0, 0)
        # Make the panel slimmer in height
        controls_frame.setFixedHeight(92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

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
        self.toggle_button.setText("Stop Serial")

        self.port_combo.setEnabled(False)
        self.baud_combo.setEnabled(False)

        self.status_label.setText(f"Starting {port} @ {baud}...")
        self.status_label.setProperty("status", "warning")
        self.status_label.style().polish(self.status_label)
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
        self.toggle_button.setText("Start Serial")

        self.port_combo.setEnabled(True)
        self.baud_combo.setEnabled(True)

        self.status_label.setText("Stopped")
        self.status_label.setProperty("status", "disabled")
        self.status_label.style().polish(self.status_label)
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
        """Update connection status from serial worker."""
        if not self._is_running:
            return

        self._connection_status = status
        port = self.port_combo.currentText()
        baud = self.baud_combo.currentText()

        if status == "connected":
            self.status_label.setText("Waiting for data...")
            self.status_label.setProperty("status", "warning")  # Orange until fusion is active
            self.status_label.style().polish(self.status_label)
        elif status == "error":
            # Re-enable port and baud selection on error so user can try different settings
            self.port_combo.setEnabled(True)
            self.baud_combo.setEnabled(True)

            self.status_label.setText(f"Error on {port} @ {baud}")
            self.status_label.setProperty("status", "error")
            self.status_label.style().polish(self.status_label)
            self._data_activity_timer.stop()

    def update_data_activity(self):
        """Called when data is received - indicates active connection."""
        if not self._is_running or self._connection_status != "connected":
            return

        import time
        self._last_data_time = time.time()

        # Only show green when both connected AND fusion is processing
        if self._fusion_processing:
            port = self.port_combo.currentText()
            baud = self.baud_combo.currentText()
            self.status_label.setText(f"Running on {port} @ {baud}")
            self.status_label.setProperty("status", "enabled")
            self.status_label.style().polish(self.status_label)

        # Reset timeout timer (5 seconds without data = back to waiting)
        self._data_activity_timer.start(5000)

    def _on_data_timeout(self):
        """Called when no data received for timeout period."""
        if not self._is_running or self._connection_status != "connected":
            return

        self.status_label.setText("Waiting for data...")
        self.status_label.setProperty("status", "warning")
        self.status_label.style().polish(self.status_label)

    def update_fusion_status(self, is_active):
        """Update fusion processing status from statusQueue."""
        self._fusion_processing = is_active

        if self._is_running and self._connection_status == "connected":
            if is_active:
                port = self.port_combo.currentText()
                baud = self.baud_combo.currentText()
                self.status_label.setText(f"Running on {port} @ {baud}")
                self.status_label.setProperty("status", "enabled")
            else:
                self.status_label.setText("Waiting for data...")
                self.status_label.setProperty("status", "warning")
            self.status_label.style().polish(self.status_label)

    # ------------------------------------------------------------------
    # UDP handling (identical logic to former NetworkPanelQt)
    # ------------------------------------------------------------------

    def _on_ip_changed(self, text):
        """Handle IP address entry change."""
        self._udp_ip = text

    def _on_udp_port_changed(self, text):
        """Handle UDP port entry change."""
        self._udp_port = text

    def toggle_udp(self):
        """Toggle UDP sending on/off."""
        self.udp_enabled = not self.udp_enabled

        if self.udp_enabled:
            self._enable_udp()
        else:
            self._disable_udp()

    def _enable_udp(self):
        """Enable UDP transmission."""
        self._udp_btn_text = "Stop UDP"
        self.udp_toggle_btn.setText(self._udp_btn_text)

        self.udp_ip_entry.setEnabled(False)
        self.udp_port_entry.setEnabled(False)

        try:
            ip = str(self.udp_ip_entry.text())
            port = int(self.udp_port_entry.text())
        except ValueError:
            self.log_message("Invalid port number")
            return

        if not safe_queue_put(
            self.udp_control_queue,
            ('set_udp', ip, port),
            timeout=QUEUE_PUT_TIMEOUT
        ):
            self.log_message("Failed to send UDP configuration")
            return

        if not safe_queue_put(
            self.udp_control_queue,
            ('udp_enable', True),
            timeout=QUEUE_PUT_TIMEOUT
        ):
            self.log_message("Failed to enable UDP")
            return

        self._udp_status_text = f"UDP Enabled -> {ip}:{port}"
        self.udp_status_label.setText(self._udp_status_text)
        self.udp_status_label.setProperty("status", "enabled")
        self.udp_status_label.style().polish(self.udp_status_label)

        self.log_message(f"UDP enabled -> {ip}:{port}")

    def _disable_udp(self):
        """Disable UDP transmission."""
        self._udp_btn_text = "Start UDP"
        self.udp_toggle_btn.setText(self._udp_btn_text)

        self.udp_ip_entry.setEnabled(True)
        self.udp_port_entry.setEnabled(True)

        if not safe_queue_put(
            self.udp_control_queue,
            ('udp_enable', False),
            timeout=QUEUE_PUT_TIMEOUT
        ):
            self.log_message("Failed to disable UDP")
            return

        self._udp_status_text = "UDP Disabled"
        self.udp_status_label.setText(self._udp_status_text)
        self.udp_status_label.setProperty("status", "disabled")
        self.udp_status_label.style().polish(self.udp_status_label)

        self.log_message("UDP disabled")

    def set_udp_config(self, ip, port):
        """Set UDP configuration programmatically."""
        try:
            self._udp_ip = str(ip)
            self._udp_port = str(port)
            self.udp_ip_entry.setText(self._udp_ip)
            self.udp_port_entry.setText(self._udp_port)

            if self.udp_enabled:
                safe_queue_put(
                    self.udp_control_queue,
                    ('set_udp', str(ip), int(port)),
                    timeout=QUEUE_PUT_TIMEOUT
                )
        except Exception:
            pass

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
        return self.udp_enabled

    def enable_udp(self):
        """Enable UDP if currently disabled."""
        if not self.udp_enabled:
            self.toggle_udp()

    def disable_udp(self):
        """Disable UDP if currently enabled."""
        if self.udp_enabled:
            self.toggle_udp()

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
                'udp_port': self.udp_port_entry.text()
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

            # Send initial configuration to UDP worker if enabled
            if self.udp_enabled and self.udp_control_queue:
                try:
                    ip = str(self.udp_ip_entry.text())
                    port = int(self.udp_port_entry.text())
                    safe_queue_put(
                        self.udp_control_queue,
                        ('set_udp', ip, port),
                        timeout=QUEUE_PUT_TIMEOUT
                    )
                    safe_queue_put(
                        self.udp_control_queue,
                        ('udp_enable', True),
                        timeout=QUEUE_PUT_TIMEOUT
                    )
                except Exception:
                    pass
