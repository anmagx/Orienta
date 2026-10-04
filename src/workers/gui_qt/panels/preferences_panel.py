"""
Preferences panel for orienta GUI.

Provides user interface for application settings including send-rate limits.
"""
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QComboBox, QPushButton, QSpacerItem, QSizePolicy, QSlider,
    QSpinBox, QFrame, QScrollArea
)

from src.managers.preferences_manager import PreferencesManager
from src.config.config import DEFAULT_THEME, THEMES_ENABLED, ALPHA_YAW, ALPHA_ROLL, ALPHA_PITCH, THRESH_DEBOUNCE_MS, STATIONARY_GYRO_THRESHOLD, STATIONARY_DEBOUNCE_S, DRIFT_SMOOTHING_TIME, DRIFT_TRANSITION_CURVE, GYRO_BIAS_CAL_SAMPLES, QUEUE_PUT_TIMEOUT
from src.workers.gui_qt.panels.base_panel import ui_log as _ui_log, DEFAULT_SPACING, LINE_THICKNESS, DIALOG_CONTENT_MARGIN

from src.util.error_utils import (
    safe_queue_put
)


class PreferencesPanel(QWidget):
    """Panel for application preferences and settings."""
    
    # Signal emitted when theme changes
    theme_changed = pyqtSignal(str)  # theme_name
    preferences_changed = pyqtSignal()  # General signal for any preference change
    
    def __init__(self, parent=None, preferences_manager=None,
                 input_command_queue=None, input_response_queue=None,
                 control_queue=None, udp_control_queue=None):
        """
        Initialize preferences panel.
        
        Args:
            parent: Parent widget
            preferences_manager: PreferencesManager instance
            input_command_queue: Queue for sending commands to input worker
            input_response_queue: Queue for receiving responses from input worker
            control_queue: Queue for sending fusion setting commands
            udp_control_queue: Queue for sending UDP worker commands
        """
        super().__init__(parent)
        self.prefs_manager = preferences_manager or PreferencesManager()
        # Shortcuts are owned by OrientationPanelQt; this panel only mirrors the
        # toggle mode for its checkbox and never persists shortcut state itself.
        self.disengage_toggle_mode = False  # False = hold to disengage, True = toggle on/off
        self.orientation_panel = None  # Will be set by parent
        self.control_queue = control_queue
        self.udp_control_queue = udp_control_queue
        self.send_rate_hz = 0
        
        # Store input worker queues
        self.input_command_queue = input_command_queue
        self.input_response_queue = input_response_queue
        
        # Drift correction alpha values
        self.alpha_pitch = ALPHA_PITCH
        self.alpha_roll = ALPHA_ROLL
        
        # Flag to prevent preference saves during initial load
        self._loading = True
        
        # Stationary detection parameters
        self.stationary_gyro_threshold = STATIONARY_GYRO_THRESHOLD
        self.stationary_debounce_s = STATIONARY_DEBOUNCE_S
        
        # Drift correction parameters
        self.drift_smoothing_time = DRIFT_SMOOTHING_TIME
        self.drift_transition_curve = DRIFT_TRANSITION_CURVE
        self.drift_correction_strength = 0.3  # Default max correction strength per frame (0.1 to 1.0)
        
        # Gyro calibration parameters
        self.gyro_bias_cal_samples = GYRO_BIAS_CAL_SAMPLES
        
        # Axis inversion settings for sensor configuration
        self.invert_yaw = False
        self.invert_pitch = False
        self.invert_roll = False
        
        # Debounce timers for alpha updates
        self._alpha_pitch_timer = QTimer()
        self._alpha_pitch_timer.setSingleShot(True)
        self._alpha_pitch_timer.timeout.connect(self._apply_alpha_pitch)
        self._pending_alpha_pitch = None
        
        self._alpha_roll_timer = QTimer()
        self._alpha_roll_timer.setSingleShot(True)
        self._alpha_roll_timer.timeout.connect(self._apply_alpha_roll)
        self._pending_alpha_roll = None
        
        self._stationary_gyro_timer = QTimer()
        self._stationary_gyro_timer.setSingleShot(True)
        self._stationary_gyro_timer.timeout.connect(self._apply_stationary_gyro)
        self._pending_stationary_gyro = None
        
        self._stationary_debounce_timer = QTimer()
        self._stationary_debounce_timer.setSingleShot(True)
        self._stationary_debounce_timer.timeout.connect(self._apply_stationary_debounce)
        self._pending_stationary_debounce = None
        
        self._drift_smoothing_timer = QTimer()
        self._drift_smoothing_timer.setSingleShot(True)
        self._drift_smoothing_timer.timeout.connect(self._apply_drift_smoothing)
        self._pending_drift_smoothing = None
        
        self._drift_strength_timer = QTimer()
        self._drift_strength_timer.setSingleShot(True)
        self._drift_strength_timer.timeout.connect(self._apply_drift_strength)
        self._pending_drift_strength = None
        
        # Debounce timer for preference saving
        self._prefs_save_timer = QTimer()
        self._prefs_save_timer.setSingleShot(True)
        self._prefs_save_timer.timeout.connect(self._emit_preferences_changed)
        
        # Set size policy to expand and fill available space
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        self.setup_ui()
    
    def setup_ui(self):
        """Set up the user interface."""
        # Main layout for this widget
        main_layout = QVBoxLayout()
        main_layout.setSpacing(DEFAULT_SPACING)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Buttons will be placed in a bottom fixed row (added after scroll area)

        # Create scroll area to contain all content (below top buttons)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setMinimumHeight(0)
        scroll_area.setMaximumHeight(16777215)
        # Keep a reference so we can resize it to fill available height
        self._scroll_area = scroll_area

        # Container widget for scroll area content
        container = QWidget()
        container.setObjectName("preferencesContainer")
        container.setAutoFillBackground(True)  # Use theme background
        layout = QVBoxLayout(container)
        
        # Theme selection group
        if THEMES_ENABLED:
            theme_group = QGroupBox("Appearance")
            theme_layout = QHBoxLayout()
            
            theme_label = QLabel("Theme:")
            self.theme_combo = QComboBox()
            self.theme_combo.addItems(["light", "dark"])
            self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
            
            theme_layout.addWidget(theme_label)
            theme_layout.addWidget(self.theme_combo)
            theme_layout.addStretch()
            
            theme_group.setLayout(theme_layout)
            layout.addWidget(theme_group)

        send_rate_group = QGroupBox("Limit Send Rate")
        send_rate_layout = QHBoxLayout()
        send_rate_label = QLabel("Maximum UDP send rate (Hz):")
        self.send_rate_combo = QComboBox()
        self.send_rate_combo.addItem("0 (Unlimited)", 0)
        self.send_rate_combo.addItem("60", 60)
        self.send_rate_combo.addItem("120", 120)
        self.send_rate_combo.addItem("240", 240)
        self.send_rate_combo.currentIndexChanged.connect(self._on_send_rate_changed)
        send_rate_layout.addWidget(send_rate_label)
        send_rate_layout.addWidget(self.send_rate_combo)
        send_rate_layout.addStretch()
        send_rate_group.setLayout(send_rate_layout)
        layout.addWidget(send_rate_group)
        
        # Keyboard shortcuts group
        # Note: The actual "Set Shortcut..." controls for Reset Orientation and
        # Disengage Drift Correction live next to their respective buttons in
        # the Orientation panel, which also owns their state and persistence.
        # Only the disengage hold/toggle mode behavior is configurable here.
        shortcuts_group = QGroupBox("Disengage Behavior")
        shortcuts_layout = QVBoxLayout()
        
        # Toggle mode checkbox for disengage
        from PyQt5.QtWidgets import QCheckBox
        self.disengage_toggle_checkbox = QCheckBox("Toggle mode (press once to disengage, press again to re-engage)")
        self.disengage_toggle_checkbox.setChecked(self.disengage_toggle_mode)
        self.disengage_toggle_checkbox.stateChanged.connect(self._on_disengage_toggle_changed)
        shortcuts_layout.addWidget(self.disengage_toggle_checkbox)
        
        shortcuts_group.setLayout(shortcuts_layout)
        layout.addWidget(shortcuts_group)
        
        # Sensor configuration group
        sensor_group = QGroupBox("Sensor Configuration")
        sensor_layout = QVBoxLayout()
        sensor_layout.setSpacing(DEFAULT_SPACING)
        
        # Axis inversion checkboxes
        inversion_header = QLabel("Axis Inversions (adjust for sensor mounting)")
        inversion_header.setStyleSheet("font-weight: 600; margin-bottom: 6px;")
        sensor_layout.addWidget(inversion_header)
        
        from PyQt5.QtWidgets import QCheckBox
        
        self.invert_yaw_checkbox = QCheckBox("Invert Yaw")
        self.invert_yaw_checkbox.setChecked(self.invert_yaw)
        self.invert_yaw_checkbox.stateChanged.connect(self._on_invert_yaw_changed)
        sensor_layout.addWidget(self.invert_yaw_checkbox)
        
        self.invert_pitch_checkbox = QCheckBox("Invert Pitch")
        self.invert_pitch_checkbox.setChecked(self.invert_pitch)
        self.invert_pitch_checkbox.stateChanged.connect(self._on_invert_pitch_changed)
        sensor_layout.addWidget(self.invert_pitch_checkbox)
        
        self.invert_roll_checkbox = QCheckBox("Invert Roll")
        self.invert_roll_checkbox.setChecked(self.invert_roll)
        self.invert_roll_checkbox.stateChanged.connect(self._on_invert_roll_changed)
        sensor_layout.addWidget(self.invert_roll_checkbox)
        
        # Add info label for sensor configuration
        sensor_info_label = QLabel("Enable inversions if your sensor orientation differs from the expected arrangement")
        sensor_info_label.setStyleSheet("color: #666666; font-size: 10px;")
        sensor_info_label.setWordWrap(True)
        sensor_layout.addWidget(sensor_info_label)
        
        sensor_group.setLayout(sensor_layout)
        layout.addWidget(sensor_group)
        
        # Drift correction group
        drift_group = QGroupBox("Drift Correction")
        drift_layout = QVBoxLayout()
        drift_layout.setSpacing(DEFAULT_SPACING)
        
        # Pitch alpha slider
        # Header for pitch/roll stability controls
        pitch_header = QLabel("Pitch & Roll stability")
        pitch_header.setStyleSheet("font-weight: 600; margin-bottom: 6px;")
        drift_layout.addWidget(pitch_header)

        pitch_layout = QHBoxLayout()
        pitch_label = QLabel("Pitch Alpha:")
        pitch_label.setMinimumWidth(80)
        self.alpha_pitch_slider = QSlider(Qt.Horizontal)
        self.alpha_pitch_slider.setMinimum(950)  # 0.95
        self.alpha_pitch_slider.setMaximum(999)  # 0.999
        self.alpha_pitch_slider.setValue(int(self.alpha_pitch * 1000))
        self.alpha_pitch_slider.valueChanged.connect(self._on_alpha_pitch_changed)
        self.alpha_pitch_value = QLabel(f"{self.alpha_pitch:.3f}")
        self.alpha_pitch_value.setMinimumWidth(50)
        
        pitch_layout.addWidget(pitch_label)
        pitch_layout.addWidget(self.alpha_pitch_slider)
        pitch_layout.addWidget(self.alpha_pitch_value)
        drift_layout.addLayout(pitch_layout)
        
        # Roll alpha slider
        roll_layout = QHBoxLayout()
        roll_label = QLabel("Roll Alpha:")
        roll_label.setMinimumWidth(80)
        self.alpha_roll_slider = QSlider(Qt.Horizontal)
        self.alpha_roll_slider.setMinimum(950)  # 0.95
        self.alpha_roll_slider.setMaximum(999)  # 0.999
        self.alpha_roll_slider.setValue(int(self.alpha_roll * 1000))
        self.alpha_roll_slider.valueChanged.connect(self._on_alpha_roll_changed)
        self.alpha_roll_value = QLabel(f"{self.alpha_roll:.3f}")
        self.alpha_roll_value.setMinimumWidth(50)
        
        roll_layout.addWidget(roll_label)
        roll_layout.addWidget(self.alpha_roll_slider)
        roll_layout.addWidget(self.alpha_roll_value)
        drift_layout.addLayout(roll_layout)

        # Divider rafter before the alpha tooltip
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setObjectName("sectionDivider")
        divider.setFixedHeight(LINE_THICKNESS)
        drift_layout.addWidget(divider)

        # Short tooltip specific to alpha sliders (placed under Roll Alpha)
        alpha_info_label = QLabel("Higher alpha = more gyro dominance.")
        alpha_info_label.setStyleSheet("color: #666666; font-size: 10px;")
        alpha_info_label.setWordWrap(True)
        alpha_info_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        drift_layout.addWidget(alpha_info_label)

        # Header for drift correction controls
        drift_curve_header = QLabel("Drift Correction (when near center)")
        drift_curve_header.setStyleSheet("font-weight: 600; margin-top: 8px; margin-bottom: 6px;")
        drift_layout.addWidget(drift_curve_header)

        # Drift transition curve dropdown (moved above smoothing time)
        curve_layout = QHBoxLayout()
        curve_label = QLabel("Transition Curve:")
        curve_label.setMinimumWidth(80)
        self.drift_curve_combo = QComboBox()
        self.drift_curve_combo.addItems(["exponential", "cosine", "linear", "quadratic"])
        self.drift_curve_combo.setCurrentText(self.drift_transition_curve)
        self.drift_curve_combo.currentTextChanged.connect(self._on_drift_curve_changed)

        curve_layout.addWidget(curve_label)
        curve_layout.addWidget(self.drift_curve_combo)
        curve_layout.addStretch()
        drift_layout.addLayout(curve_layout)

        # Drift smoothing time slider
        smoothing_layout = QHBoxLayout()
        smoothing_label = QLabel("Smoothing Time:")
        smoothing_label.setMinimumWidth(80)
        self.drift_smoothing_slider = QSlider(Qt.Horizontal)
        self.drift_smoothing_slider.setMinimum(0)    # 0.0 seconds
        self.drift_smoothing_slider.setMaximum(50)   # 5.0 seconds
        self.drift_smoothing_slider.setValue(int(self.drift_smoothing_time * 10))
        self.drift_smoothing_slider.valueChanged.connect(self._on_drift_smoothing_changed)
        self.drift_smoothing_value = QLabel(f"{self.drift_smoothing_time:.1f} s")
        self.drift_smoothing_value.setMinimumWidth(50)

        smoothing_layout.addWidget(smoothing_label)
        smoothing_layout.addWidget(self.drift_smoothing_slider)
        smoothing_layout.addWidget(self.drift_smoothing_value)
        drift_layout.addLayout(smoothing_layout)

        # Drift correction strength slider
        strength_layout = QHBoxLayout()
        strength_label = QLabel("Correction Strength:")
        strength_label.setMinimumWidth(80)
        self.drift_strength_slider = QSlider(Qt.Horizontal)
        self.drift_strength_slider.setMinimum(10)    # 0.1 (10%)
        self.drift_strength_slider.setMaximum(100)   # 1.0 (100%)
        self.drift_strength_slider.setValue(int(self.drift_correction_strength * 100))
        self.drift_strength_slider.valueChanged.connect(self._on_drift_strength_changed)
        self.drift_strength_value = QLabel(f"{int(self.drift_correction_strength * 100)}%")
        self.drift_strength_value.setMinimumWidth(50)

        strength_layout.addWidget(strength_label)
        strength_layout.addWidget(self.drift_strength_slider)
        strength_layout.addWidget(self.drift_strength_value)
        drift_layout.addLayout(strength_layout)
        
        # Add info label describing smoothing, strength and curve options (bottom of calibration frame)
        drift_info_label = QLabel("Smoothing time controls drift correction speed. Correction strength caps the maximum correction per frame (higher = stronger correction, may fight user input). Transition curves: exponential (original), cosine (smooth), linear, quadratic (sharp).")
        drift_info_label.setStyleSheet("color: #666666; font-size: 10px;")
        drift_info_label.setWordWrap(True)
        drift_info_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        drift_layout.addWidget(drift_info_label)
        
        drift_group.setLayout(drift_layout)
        layout.addWidget(drift_group)
        
        # Stationary detection group
        stationary_group = QGroupBox("Stationary Detection")
        stationary_layout = QVBoxLayout()
        stationary_layout.setSpacing(DEFAULT_SPACING)
        
        # Gyro threshold slider (1.0 to 20.0 deg/s)
        gyro_layout = QHBoxLayout()
        gyro_label = QLabel("Gyro Threshold:")
        gyro_label.setMinimumWidth(100)
        self.stationary_gyro_slider = QSlider(Qt.Horizontal)
        self.stationary_gyro_slider.setMinimum(10)  # 1.0 deg/s
        self.stationary_gyro_slider.setMaximum(200)  # 20.0 deg/s
        self.stationary_gyro_slider.setValue(int(self.stationary_gyro_threshold * 10))
        self.stationary_gyro_slider.valueChanged.connect(self._on_stationary_gyro_changed)
        self.stationary_gyro_value = QLabel(f"{self.stationary_gyro_threshold:.1f} °/s")
        self.stationary_gyro_value.setMinimumWidth(60)
        
        gyro_layout.addWidget(gyro_label)
        gyro_layout.addWidget(self.stationary_gyro_slider)
        gyro_layout.addWidget(self.stationary_gyro_value)
        stationary_layout.addLayout(gyro_layout)
        
        # Debounce time slider (0.05 to 1.0 seconds)
        debounce_layout = QHBoxLayout()
        debounce_label = QLabel("Debounce Time:")
        debounce_label.setMinimumWidth(100)
        self.stationary_debounce_slider = QSlider(Qt.Horizontal)
        self.stationary_debounce_slider.setMinimum(5)  # 0.05 seconds
        self.stationary_debounce_slider.setMaximum(100)  # 1.0 seconds
        self.stationary_debounce_slider.setValue(int(self.stationary_debounce_s * 100))
        self.stationary_debounce_slider.valueChanged.connect(self._on_stationary_debounce_changed)
        self.stationary_debounce_value = QLabel(f"{self.stationary_debounce_s:.2f} s")
        self.stationary_debounce_value.setMinimumWidth(60)
        
        debounce_layout.addWidget(debounce_label)
        debounce_layout.addWidget(self.stationary_debounce_slider)
        debounce_layout.addWidget(self.stationary_debounce_value)
        stationary_layout.addLayout(debounce_layout)
        
        # Add info label for stationary detection
        stationary_info_label = QLabel("Controls when the device is considered stationary for drift correction")
        stationary_info_label.setStyleSheet("color: #666666; font-size: 10px;")
        stationary_layout.addWidget(stationary_info_label)
        
        stationary_group.setLayout(stationary_layout)
        layout.addWidget(stationary_group)
        
        # Gyro calibration group
        gyro_group = QGroupBox("Gyro Calibration")
        gyro_layout = QVBoxLayout()
        gyro_layout.setSpacing(DEFAULT_SPACING)
        
        # Calibration samples slider (500 to 5000)
        samples_layout = QHBoxLayout()
        samples_label = QLabel("Calibration Samples:")
        samples_label.setMinimumWidth(100)
        self.gyro_samples_slider = QSlider(Qt.Horizontal)
        self.gyro_samples_slider.setMinimum(500)  # 500 samples
        self.gyro_samples_slider.setMaximum(5000)  # 5000 samples
        # Use 250-sample increments
        self.gyro_samples_slider.setSingleStep(250)
        self.gyro_samples_slider.setPageStep(250)
        # Set tick interval and position (use module-level QSlider)
        try:
            self.gyro_samples_slider.setTickInterval(250)
            self.gyro_samples_slider.setTickPosition(QSlider.TicksBelow)
        except Exception:
            pass
        # Round initial value to nearest 250 and clamp
        try:
            init_val = int(round(float(self.gyro_bias_cal_samples) / 250.0) * 250)
        except Exception:
            init_val = 500
        init_val = max(500, min(5000, init_val))
        self.gyro_samples_slider.setValue(init_val)
        self.gyro_samples_slider.valueChanged.connect(self._on_gyro_samples_changed)
        self.gyro_samples_value = QLabel(str(init_val))
        self.gyro_samples_value.setMinimumWidth(60)
        
        samples_layout.addWidget(samples_label)
        samples_layout.addWidget(self.gyro_samples_slider)
        samples_layout.addWidget(self.gyro_samples_value)
        gyro_layout.addLayout(samples_layout)
        
        # Add info label for gyro calibration
        gyro_info_label = QLabel("Number of samples collected when recalibrating gyro bias. More samples = better accuracy but slower calibration.")
        gyro_info_label.setStyleSheet("color: #666666; font-size: 10px;")
        gyro_layout.addWidget(gyro_info_label)
        
        gyro_group.setLayout(gyro_layout)
        layout.addWidget(gyro_group)
        
        # Set container as scroll area widget and add it to the main layout
        scroll_area.setWidget(container)
        main_layout.addWidget(scroll_area, 1)
        # Bottom fixed row with Reset and Close buttons
        bottom_btn_layout = QHBoxLayout()
        bottom_btn_layout.addStretch()
        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.clicked.connect(self._reset_to_defaults)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self._on_close_clicked)
        bottom_btn_layout.addWidget(self.reset_btn)
        bottom_btn_layout.addWidget(self.close_btn)
        main_layout.addLayout(bottom_btn_layout)
        self.setLayout(main_layout)

    # Let Qt layouts manage sizing; remove forced resize adjustments
    
    
    def load_preferences(self):
        """Load all preferences from config file via PreferencesManager, update UI, and sync with fusion worker."""
        self._loading = True
        # Load all preferences using PreferencesManager
        prefs = self.prefs_manager.load()
        
        # Load theme preferences
        if THEMES_ENABLED and hasattr(self, 'theme_combo'):
            current_theme = self.prefs_manager.get_theme()
            index = self.theme_combo.findText(current_theme)
            if index >= 0:
                self.theme_combo.setCurrentIndex(index)

        network_prefs = prefs.get('network', {})
        if isinstance(network_prefs, dict) and network_prefs.get('output_rate_hz'):
            try:
                saved_rate = int(network_prefs['output_rate_hz'])
            except (TypeError, ValueError):
                _ui_log(self, "[Preferences] Ignoring invalid saved UDP send rate")
            else:
                index = self.send_rate_combo.findData(saved_rate)
                if index < 0:
                    _ui_log(
                        self,
                        f"[Preferences] Unsupported saved UDP send rate {saved_rate}; using unlimited"
                    )
                    index = self.send_rate_combo.findData(0)
                self.send_rate_combo.setCurrentIndex(index)
        self.send_rate_hz = int(self.send_rate_combo.currentData())
        
        # These settings are persisted into the 'orientation' section by the GUI
        # worker; 'calibration' is only read to migrate older config files.
        cal_prefs = dict(prefs.get('calibration', {}))
        cal_prefs.update(prefs.get('orientation', {}))
        
        # Load and apply all calibration settings
        self._load_alpha_settings(cal_prefs)
        self._load_stationary_settings(cal_prefs)
        self._load_drift_settings(cal_prefs)
        self._load_gyro_settings(cal_prefs)
        self._load_shortcut_settings(cal_prefs)
        self._load_sensor_settings(cal_prefs)
        
        # Send settings to fusion worker
        self._apply_settings_to_fusion_worker(cal_prefs)

        self._apply_send_rate_to_worker()
        
        # Clear loading flag after initial load is complete
        self._loading = False

    def _on_send_rate_changed(self, index):
        """Apply a send-rate selection and persist it with the other preferences."""
        rate_hz = self.send_rate_combo.itemData(index)
        if rate_hz is None:
            return
        self.send_rate_hz = int(rate_hz)
        if not self._loading:
            self._apply_send_rate_to_worker()
            self._trigger_preference_save()

    def _apply_send_rate_to_worker(self):
        """Send the selected UDP rate limit to the UDP worker."""
        if self.udp_control_queue is not None and not safe_queue_put(
            self.udp_control_queue,
            ('set_rate', self.send_rate_hz),
            timeout=QUEUE_PUT_TIMEOUT
        ):
            _ui_log(self, "[Preferences] Failed to send UDP send rate")

    def get_send_rate_preferences(self):
        """Get the UDP send-rate preference for the existing network config section."""
        return {'output_rate_hz': str(self.send_rate_hz)}

    def _on_close_clicked(self):
        """Handle Close button - close containing dialog or top-level window."""
        try:
            win = self.window()
            if win:
                # Prefer dialog accept for QDialog to ensure proper dialog lifecycle
                try:
                    if hasattr(win, 'accept'):
                        win.accept()
                    else:
                        win.close()
                except Exception:
                    try:
                        win.close()
                    except Exception:
                        pass
        except Exception:
            pass

    def _on_theme_changed(self, theme_name):
        """Handle theme selection change.

        The theme is persisted by the GUI worker's aggregated save (it reads the
        active theme from ThemeManager), so this only applies the theme and
        requests a save rather than writing the config file itself.
        """
        if THEMES_ENABLED:
            self.theme_changed.emit(theme_name)
            # Only emit preferences changed if not loading to prevent duplicate saves
            if not getattr(self, '_loading', False):
                self.preferences_changed.emit()  # Notify parent to save all preferences
    
    
    def _on_alpha_pitch_changed(self, value):
        """Handle pitch alpha slider change with debouncing."""
        self.alpha_pitch = value / 1000.0
        self.alpha_pitch_value.setText(f"{self.alpha_pitch:.3f}")
        
        # Store pending value for debounced sending
        self._pending_alpha_pitch = self.alpha_pitch
        
        # Restart debounce timer
        self._alpha_pitch_timer.stop()
        self._alpha_pitch_timer.start(THRESH_DEBOUNCE_MS)
        
        # Debounced preference save
        self._trigger_preference_save()
    
    def _on_alpha_roll_changed(self, value):
        """Handle roll alpha slider change with debouncing."""
        self.alpha_roll = value / 1000.0
        self.alpha_roll_value.setText(f"{self.alpha_roll:.3f}")
        
        # Store pending value for debounced sending
        self._pending_alpha_roll = self.alpha_roll
        
        # Restart debounce timer
        self._alpha_roll_timer.stop()
        self._alpha_roll_timer.start(THRESH_DEBOUNCE_MS)
        
        # Debounced preference save
        self._trigger_preference_save()
    
    def _apply_alpha_pitch(self):
        """Apply pitch alpha value to fusion worker (debounced)."""
        if self._pending_alpha_pitch is not None:
            try:
                self._send_control_command(('set_alpha_pitch', self._pending_alpha_pitch))
                self._pending_alpha_pitch = None
            except Exception:
                pass
    
    def _apply_alpha_roll(self):
        """Apply roll alpha value to fusion worker (debounced)."""
        if self._pending_alpha_roll is not None:
            try:
                self._send_control_command(('set_alpha_roll', self._pending_alpha_roll))
                self._pending_alpha_roll = None
            except Exception:
                pass
    
    def _on_stationary_gyro_changed(self, value):
        """Handle stationary gyro threshold slider change with debouncing."""
        self.stationary_gyro_threshold = value / 10.0
        self.stationary_gyro_value.setText(f"{self.stationary_gyro_threshold:.1f} °/s")
        
        # Store pending value for debounced sending
        self._pending_stationary_gyro = self.stationary_gyro_threshold
        
        # Restart debounce timer
        self._stationary_gyro_timer.stop()
        self._stationary_gyro_timer.start(THRESH_DEBOUNCE_MS)
        
        # Debounced preference save
        self._trigger_preference_save()
    
    def _on_stationary_debounce_changed(self, value):
        """Handle stationary debounce time slider change with debouncing."""
        self.stationary_debounce_s = value / 100.0
        self.stationary_debounce_value.setText(f"{self.stationary_debounce_s:.2f} s")
        
        # Store pending value for debounced sending
        self._pending_stationary_debounce = self.stationary_debounce_s
        
        # Restart debounce timer
        self._stationary_debounce_timer.stop()
        self._stationary_debounce_timer.start(THRESH_DEBOUNCE_MS)
        
        # Debounced preference save
        self._trigger_preference_save()
    
    def _apply_stationary_gyro(self):
        """Apply stationary gyro threshold to fusion worker (debounced)."""
        if self._pending_stationary_gyro is not None:
            try:
                self._send_control_command(
                    ('set_stationary_gyro_threshold', float(self._pending_stationary_gyro)),
                    failure_message="[Preferences] Unable to send stationary gyro threshold: queue unavailable or full",
                    success_message=f"[Preferences] Sent stationary gyro threshold: {self._pending_stationary_gyro}"
                )
                self._pending_stationary_gyro = None
            except Exception as e:
                _ui_log(self, f"[Preferences] Failed to apply stationary gyro threshold: {e}")
    
    def _apply_stationary_debounce(self):
        """Apply stationary debounce time to fusion worker (debounced)."""
        if self._pending_stationary_debounce is not None:
            try:
                self._send_control_command(
                    ('set_stationary_debounce', float(self._pending_stationary_debounce)),
                    failure_message="[Preferences] Unable to send stationary debounce: queue unavailable or full",
                    success_message=f"[Preferences] Sent stationary debounce: {self._pending_stationary_debounce}"
                )
                self._pending_stationary_debounce = None
            except Exception as e:
                _ui_log(self, f"[Preferences] Failed to apply stationary debounce: {e}")
    
    def _on_drift_smoothing_changed(self, value):
        """Handle drift smoothing time slider change with debouncing."""
        self.drift_smoothing_time = value / 10.0
        self.drift_smoothing_value.setText(f"{self.drift_smoothing_time:.1f} s")
        
        # Store pending value for debounced sending
        self._pending_drift_smoothing = self.drift_smoothing_time
        
        # Restart debounce timer
        self._drift_smoothing_timer.stop()
        self._drift_smoothing_timer.start(THRESH_DEBOUNCE_MS)
        
        # Debounced preference save
        self._trigger_preference_save()
    
    def _on_drift_strength_changed(self, value):
        """Handle drift correction strength slider change with debouncing."""
        self.drift_correction_strength = value / 100.0
        self.drift_strength_value.setText(f"{int(self.drift_correction_strength * 100)}%")
        
        # Store pending value for debounced sending
        self._pending_drift_strength = self.drift_correction_strength
        
        # Restart debounce timer
        self._drift_strength_timer.stop()
        self._drift_strength_timer.start(THRESH_DEBOUNCE_MS)
        
        # Debounced preference save
        self._trigger_preference_save()
    
    def _on_drift_curve_changed(self, curve_type):
        """Handle drift transition curve selection change."""
        self.drift_transition_curve = curve_type
        
        # Send command to fusion worker for live update
        try:
            self._send_control_command(('set_drift_curve_type', curve_type))
        except Exception as e:
            _ui_log(self, f"[Preferences] Failed to send drift curve command: {e}")
        
        self._trigger_preference_save()
    
    def _on_gyro_samples_changed(self, value):
        """Handle gyro calibration samples slider change."""
        # Snap value to 250-sample increments and clamp to valid range
        try:
            snapped = int(round(float(value) / 250.0) * 250)
        except Exception:
            snapped = 500
        snapped = max(500, min(5000, snapped))

        # If slider produced a non-snapped value, update slider to snapped value
        if snapped != value:
            try:
                self.gyro_samples_slider.setValue(snapped)
            except Exception:
                pass

        # Update stored value and label
        self.gyro_bias_cal_samples = snapped
        self.gyro_samples_value.setText(str(snapped))
        # Note: This affects next recalibration, not current session
        self._trigger_preference_save()
    
    def _on_invert_yaw_changed(self, state):
        """Handle yaw inversion checkbox change."""
        self.invert_yaw = (state == 2)  # Qt.Checked == 2

        # Send command to fusion worker for live update if available
        try:
            cal = getattr(self, 'orientation_panel', None)
            self._send_control_command(('set_invert_yaw', self.invert_yaw))
        except Exception as e:
            _ui_log(self, f"[Preferences] Failed to send yaw inversion command: {e}")

        # Also update visualization immediately if supported
        try:
            if cal and hasattr(cal, 'set_invert_yaw'):
                cal.set_invert_yaw(self.invert_yaw)
        except Exception as e:
            _ui_log(self, f"[Preferences] Failed to update calibration visualization for yaw inversion: {e}")

        self._trigger_preference_save()
    
    def _on_invert_pitch_changed(self, state):
        """Handle pitch inversion checkbox change."""
        self.invert_pitch = (state == 2)  # Qt.Checked == 2

        # Send command to fusion worker for live update if available
        try:
            cal = getattr(self, 'orientation_panel', None)
            self._send_control_command(('set_invert_pitch', self.invert_pitch))
        except Exception as e:
            _ui_log(self, f"[Preferences] Failed to send pitch inversion command: {e}")

        # Also update visualization immediately if supported
        try:
            if cal and hasattr(cal, 'set_invert_pitch'):
                cal.set_invert_pitch(self.invert_pitch)
        except Exception as e:
            _ui_log(self, f"[Preferences] Failed to update calibration visualization for pitch inversion: {e}")

        self._trigger_preference_save()
    
    def _on_invert_roll_changed(self, state):
        """Handle roll inversion checkbox change."""
        self.invert_roll = (state == 2)  # Qt.Checked == 2

        # Send command to fusion worker for live update if available
        try:
            cal = getattr(self, 'orientation_panel', None)
            self._send_control_command(('set_invert_roll', self.invert_roll))
        except Exception as e:
            _ui_log(self, f"[Preferences] Failed to send roll inversion command: {e}")

        # Also update visualization immediately if supported
        try:
            if cal and hasattr(cal, 'set_invert_roll'):
                cal.set_invert_roll(self.invert_roll)
        except Exception as e:
            _ui_log(self, f"[Preferences] Failed to update calibration visualization for roll inversion: {e}")

        self._trigger_preference_save()
    
    def _apply_drift_smoothing(self):
        """Apply drift smoothing time to fusion worker (debounced)."""
        if self._pending_drift_smoothing is not None:
            try:
                self._send_control_command(('set_drift_smoothing_time', self._pending_drift_smoothing))
                self._pending_drift_smoothing = None
            except Exception:
                pass
    
    def _apply_drift_strength(self):
        """Apply drift correction strength to fusion worker (debounced)."""
        if self._pending_drift_strength is not None:
            try:
                self._send_control_command(('set_drift_correction_strength', self._pending_drift_strength))
                self._pending_drift_strength = None
            except Exception:
                pass
    

    def _emit_preferences_changed(self):
        """Emit preferences changed signal (debounced)."""
        # Only emit if not loading to prevent duplicate saves during startup
        if not getattr(self, '_loading', False):
            self.preferences_changed.emit()
    
    def _trigger_preference_save(self):
        """Trigger a debounced preference save."""
        self._prefs_save_timer.stop()
        self._prefs_save_timer.start(500)  # 500ms delay for preference saving
    
    def _reset_to_defaults(self):
        """Reset preferences to default values."""
        if THEMES_ENABLED and hasattr(self, 'theme_combo'):
            # Reset theme to default
            index = self.theme_combo.findText(DEFAULT_THEME)
            if index >= 0:
                self.theme_combo.setCurrentIndex(index)
            
            # Apply the default theme; persistence happens via the save below
            self.theme_changed.emit(DEFAULT_THEME)
        
        # Reset alpha values to defaults
        self.alpha_pitch = ALPHA_PITCH
        self.alpha_roll = ALPHA_ROLL
        
        # Reset stationary detection parameters to defaults
        self.stationary_gyro_threshold = STATIONARY_GYRO_THRESHOLD
        self.stationary_debounce_s = STATIONARY_DEBOUNCE_S
        
        # Reset drift correction parameters to defaults
        self.drift_smoothing_time = DRIFT_SMOOTHING_TIME
        self.drift_transition_curve = DRIFT_TRANSITION_CURVE
        self.drift_correction_strength = 0.3  # Default
        
        # Reset gyro calibration parameters to defaults
        self.gyro_bias_cal_samples = GYRO_BIAS_CAL_SAMPLES

        # Reset the UDP send-rate limit to unlimited
        unlimited_index = self.send_rate_combo.findData(0)
        rate_changed = self.send_rate_combo.currentIndex() != unlimited_index
        self.send_rate_combo.setCurrentIndex(unlimited_index)
        self.send_rate_hz = 0
        if not rate_changed:
            self._apply_send_rate_to_worker()
        
        # Reset axis inversions to defaults
        self.invert_yaw = False
        self.invert_pitch = False
        self.invert_roll = False
        
        # Update checkboxes
        self.invert_yaw_checkbox.setChecked(self.invert_yaw)
        self.invert_pitch_checkbox.setChecked(self.invert_pitch)
        self.invert_roll_checkbox.setChecked(self.invert_roll)
        
        # Apply to calibration panel
        if self.orientation_panel:
            self.orientation_panel.set_invert_yaw(self.invert_yaw)
            self.orientation_panel.set_invert_pitch(self.invert_pitch)
            self.orientation_panel.set_invert_roll(self.invert_roll)
        
        # Update sliders and labels
        self.alpha_pitch_slider.setValue(int(self.alpha_pitch * 1000))
        self.alpha_pitch_value.setText(f"{self.alpha_pitch:.3f}")
        self.alpha_roll_slider.setValue(int(self.alpha_roll * 1000))
        self.alpha_roll_value.setText(f"{self.alpha_roll:.3f}")
        
        # Update stationary detection sliders
        self.stationary_gyro_slider.setValue(int(self.stationary_gyro_threshold * 10))
        self.stationary_gyro_value.setText(f"{self.stationary_gyro_threshold:.1f} °/s")
        self.stationary_debounce_slider.setValue(int(self.stationary_debounce_s * 100))
        self.stationary_debounce_value.setText(f"{self.stationary_debounce_s:.2f} s")
        
        # Update drift correction sliders
        self.drift_smoothing_slider.setValue(int(self.drift_smoothing_time * 10))
        self.drift_smoothing_value.setText(f"{self.drift_smoothing_time:.1f} s")
        self.drift_strength_slider.setValue(int(self.drift_correction_strength * 100))
        self.drift_strength_value.setText(f"{int(self.drift_correction_strength * 100)}%")
        
        # Update drift curve dropdown
        index = self.drift_curve_combo.findText(self.drift_transition_curve)
        if index >= 0:
            self.drift_curve_combo.setCurrentIndex(index)
        
        # Update gyro calibration sliders (round defaults to nearest 250)
        try:
            def_val = int(round(float(self.gyro_bias_cal_samples) / 250.0) * 250)
        except Exception:
            def_val = 500
        def_val = max(500, min(5000, def_val))
        self.gyro_bias_cal_samples = def_val
        self.gyro_samples_slider.setValue(def_val)
        self.gyro_samples_value.setText(str(def_val))
        
        # Update fusion worker with debounced values
        self._pending_alpha_pitch = self.alpha_pitch
        self._pending_alpha_roll = self.alpha_roll
        self._pending_stationary_gyro = self.stationary_gyro_threshold
        self._pending_stationary_debounce = self.stationary_debounce_s
        self._pending_drift_smoothing = self.drift_smoothing_time
        self._pending_drift_strength = self.drift_correction_strength
        
        # Apply immediately (no debounce needed for reset)
        self._apply_alpha_pitch()
        self._apply_alpha_roll()
        self._apply_stationary_gyro()
        self._apply_stationary_debounce()
        self._apply_drift_smoothing()
        self._apply_drift_strength()
        
        # Persist every reset value through the aggregated save
        self._trigger_preference_save()
        
        # Visual feedback
        self.reset_btn.setText("Reset!")
        self.reset_btn.setEnabled(False)
        
        # Reset button text after delay
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(1500, lambda: (
            self.reset_btn.setText("Reset to Defaults"),
            self.reset_btn.setEnabled(True)
        ))
    
    def _on_disengage_toggle_changed(self, state):
        """Handle disengage toggle mode checkbox change."""
        self.disengage_toggle_mode = (state == 2)  # Qt.Checked == 2
        
        # OrientationPanelQt owns this setting and persists it
        if self.orientation_panel:
            self.orientation_panel.set_disengage_toggle_mode(self.disengage_toggle_mode)
        
        # Save preference
        if not getattr(self, '_loading', False):
            self.preferences_changed.emit()
    
    def _load_alpha_settings(self, cal_prefs):
        """Load alpha filter settings."""
        if 'alpha_pitch' in cal_prefs:
            self.alpha_pitch = float(cal_prefs['alpha_pitch'])
            self.alpha_pitch_slider.setValue(int(self.alpha_pitch * 1000))
            self.alpha_pitch_value.setText(f"{self.alpha_pitch:.3f}")
        
        if 'alpha_roll' in cal_prefs:
            self.alpha_roll = float(cal_prefs['alpha_roll'])
            self.alpha_roll_slider.setValue(int(self.alpha_roll * 1000))
            self.alpha_roll_value.setText(f"{self.alpha_roll:.3f}")
    
    def _load_stationary_settings(self, cal_prefs):
        """Load stationary detection settings."""
        if 'stationary_gyro_threshold' in cal_prefs:
            self.stationary_gyro_threshold = float(cal_prefs['stationary_gyro_threshold'])
            self.stationary_gyro_slider.setValue(int(self.stationary_gyro_threshold * 10))
            self.stationary_gyro_value.setText(f"{self.stationary_gyro_threshold:.1f} °/s")
        
        if 'stationary_debounce_s' in cal_prefs:
            self.stationary_debounce_s = float(cal_prefs['stationary_debounce_s'])
            self.stationary_debounce_slider.setValue(int(self.stationary_debounce_s * 100))
            self.stationary_debounce_value.setText(f"{self.stationary_debounce_s:.2f} s")
    
    def _load_drift_settings(self, cal_prefs):
        """Load drift correction settings."""
        if 'drift_smoothing_time' in cal_prefs:
            self.drift_smoothing_time = float(cal_prefs['drift_smoothing_time'])
            self.drift_smoothing_slider.setValue(int(self.drift_smoothing_time * 10))
            self.drift_smoothing_value.setText(f"{self.drift_smoothing_time:.1f} s")
        
        if 'drift_correction_strength' in cal_prefs:
            self.drift_correction_strength = float(cal_prefs['drift_correction_strength'])
            self.drift_strength_slider.setValue(int(self.drift_correction_strength * 100))
            self.drift_strength_value.setText(f"{int(self.drift_correction_strength * 100)}%")
        
        if 'drift_transition_curve' in cal_prefs:
            self.drift_transition_curve = cal_prefs['drift_transition_curve']
            index = self.drift_curve_combo.findText(self.drift_transition_curve)
            if index >= 0:
                self.drift_curve_combo.setCurrentIndex(index)
    
    def _load_gyro_settings(self, cal_prefs):
        """Load gyro calibration settings."""
        if 'gyro_bias_cal_samples' in cal_prefs:
            try:
                val = int(cal_prefs['gyro_bias_cal_samples'])
            except Exception:
                val = int(self.gyro_bias_cal_samples)
            # Round to nearest 250 and clamp
            val = int(round(float(val) / 250.0) * 250)
            val = max(500, min(5000, val))
            self.gyro_bias_cal_samples = val
            self.gyro_samples_slider.setValue(val)
            self.gyro_samples_value.setText(str(val))
    
    def _load_shortcut_settings(self, cal_prefs):
        """Sync the disengage toggle checkbox from the orientation panel.

        Shortcut keys themselves are owned, loaded and persisted by
        OrientationPanelQt; this panel only reflects the toggle mode.
        """
        toggle_mode = False
        if self.orientation_panel:
            toggle_mode = bool(getattr(self.orientation_panel, 'disengage_toggle_mode', False))
        else:
            raw = cal_prefs.get('disengage_toggle_mode', False)
            if isinstance(raw, str):
                toggle_mode = raw.lower() in ('true', '1', 'yes')
            else:
                toggle_mode = bool(raw)

        self.disengage_toggle_mode = toggle_mode
        self.disengage_toggle_checkbox.setChecked(toggle_mode)

    def _load_sensor_settings(self, cal_prefs):
        """Load sensor configuration settings from preferences."""
        # Convert string boolean values to actual booleans
        def to_bool(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ('true', '1', 'yes')
            return bool(value)
        
        self.invert_yaw = to_bool(cal_prefs.get('invert_yaw', False))
        self.invert_pitch = to_bool(cal_prefs.get('invert_pitch', False))
        self.invert_roll = to_bool(cal_prefs.get('invert_roll', False))
        
        # Update UI
        self.invert_yaw_checkbox.setChecked(self.invert_yaw)
        self.invert_pitch_checkbox.setChecked(self.invert_pitch)
        self.invert_roll_checkbox.setChecked(self.invert_roll)
    
    def _apply_settings_to_fusion_worker(self, cal_prefs):
        """Send all calibration settings to fusion worker at startup."""
        if not self.control_queue:
            return
        
        # Prevent duplicate application during startup
        if getattr(self, '_settings_applied', False):
            return
        self._settings_applied = True
        
        try:
            # Apply drift curve setting to fusion worker
            drift_curve = cal_prefs.get('drift_transition_curve', DRIFT_TRANSITION_CURVE)
            self._send_control_command(('set_drift_curve_type', drift_curve))
            
            # Apply alpha values to fusion worker
            if 'alpha_pitch' in cal_prefs:
                alpha_pitch = float(cal_prefs['alpha_pitch'])
                self._send_control_command(('set_alpha_pitch', alpha_pitch))
            
            if 'alpha_roll' in cal_prefs:
                alpha_roll = float(cal_prefs['alpha_roll'])
                self._send_control_command(('set_alpha_roll', alpha_roll))
            
            # Apply drift correction strength
            if 'drift_correction_strength' in cal_prefs:
                strength = float(cal_prefs['drift_correction_strength'])
                self._send_control_command(('set_drift_correction_strength', strength))
            
            _ui_log(self, "[Preferences] Startup settings applied")
                    
        except Exception as e:
            _ui_log(self, f"[Preferences] Error applying startup settings: {e}")
        
        # Apply axis inversions to calibration panel visualization
        if self.orientation_panel:
            self.orientation_panel.set_invert_yaw(self.invert_yaw)
            self.orientation_panel.set_invert_pitch(self.invert_pitch)
            self.orientation_panel.set_invert_roll(self.invert_roll)
        
        # Send axis inversions to fusion worker
        try:
            self._send_control_command(('set_invert_yaw', self.invert_yaw))
            self._send_control_command(('set_invert_pitch', self.invert_pitch))
            self._send_control_command(('set_invert_roll', self.invert_roll))
            # Apply stationary detection settings if present
            if 'stationary_gyro_threshold' in cal_prefs:
                try:
                    val = float(cal_prefs['stationary_gyro_threshold'])
                    self._send_control_command(('set_stationary_gyro_threshold', val))
                except Exception as e:
                    _ui_log(self, f"[Preferences] Failed to send startup stationary_gyro_threshold: {e}")
            if 'stationary_debounce_s' in cal_prefs:
                try:
                    val = float(cal_prefs['stationary_debounce_s'])
                    self._send_control_command(('set_stationary_debounce', val))
                except Exception as e:
                    _ui_log(self, f"[Preferences] Failed to send startup stationary_debounce_s: {e}")
        except Exception as e:
            _ui_log(self, f"[Preferences] Error applying axis inversions to fusion worker: {e}")
    
    def get_tuning_preferences(self):
        """Get the fusion-tuning preferences owned by this panel.

        Shortcut keys and the disengage toggle mode are intentionally excluded:
        OrientationPanelQt owns those and reports them via its own get_prefs().
        """
        return {
            'alpha_pitch': f"{self.alpha_pitch:.3f}",
            'alpha_roll': f"{self.alpha_roll:.3f}",
            'stationary_gyro_threshold': f"{self.stationary_gyro_threshold:.1f}",
            'stationary_debounce_s': f"{self.stationary_debounce_s:.3f}",
            'drift_smoothing_time': f"{self.drift_smoothing_time:.1f}",
            'drift_correction_strength': f"{self.drift_correction_strength:.2f}",
            'drift_transition_curve': self.drift_transition_curve,
            'gyro_bias_cal_samples': str(self.gyro_bias_cal_samples),
            'invert_yaw': self.invert_yaw,
            'invert_pitch': self.invert_pitch,
            'invert_roll': self.invert_roll
        }
    

    
    def connect_orientation_panel(self, orientation_panel):
        """Connect to the orientation panel for live fusion settings."""
        self.orientation_panel = orientation_panel

    def connect_control_queue(self, control_queue):
        """Connect the fusion-control queue owned by the GUI worker."""
        self.control_queue = control_queue

    def _send_control_command(self, command, failure_message=None, success_message=None):
        """Send a fusion control command without reaching through another panel."""
        if not self.control_queue:
            if failure_message:
                _ui_log(self, failure_message)
            return False
        if not safe_queue_put(self.control_queue, command, timeout=QUEUE_PUT_TIMEOUT):
            if failure_message:
                _ui_log(self, failure_message)
            return False
        if success_message:
            _ui_log(self, success_message)
        return True
    
    def get_panel_name(self) -> str:
        """Return panel display name."""
        return "Preferences"