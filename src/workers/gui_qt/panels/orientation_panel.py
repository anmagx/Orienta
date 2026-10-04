"""Orientation display and controls for the Orienta GUI."""
from PyQt5.QtWidgets import (QGroupBox, QVBoxLayout, QHBoxLayout, QGridLayout, 
                             QLabel, QSizePolicy, QWidget, QFrame, QPushButton, QDialog, QSlider, QApplication,
                             QStackedWidget, QDialogButtonBox)
from PyQt5.QtCore import Qt, QTimer, QRect, QEvent

from config.config import QUEUE_PUT_TIMEOUT
from src.util.error_utils import safe_queue_put
from src.workers.gui_qt.panels.about_panel import AboutPanel
from .base_panel import ui_log as _ui_log, DEFAULT_SPACING, LINE_THICKNESS, CONTENT_MARGINS, BUTTON_MIN_HEIGHT, BUTTON_EXTRA_HEIGHT, DIALOG_CONTENT_MARGIN
from .preferences_panel import PreferencesPanel
from .message_panel import MessagePanelQt
from .visualization_widget import OrientationVisualizationWidget, SquareContainer
from .hold_panel import HoldPanelQt as ExtractedHoldPanelQt
from .two_line_button import TwoLineButton as ExtractedTwoLineButton
from .shortcut_dialog import KeyCaptureDialog as ExtractedKeyCaptureDialog
from .visualization_popup import VisualizationPopupController

# Public widget implementations live in dedicated modules. These aliases keep
# imports from this historical module working during the package migration.
HoldPanelQt = ExtractedHoldPanelQt
TwoLineButton = ExtractedTwoLineButton
KeyCaptureDialog = ExtractedKeyCaptureDialog


class OrientationPanelQt(QGroupBox):
    """PyQt5 panel for orientation display."""
    
    def __init__(self, parent=None, control_queue=None, message_callback=None, padding=6):
        """
        Initialize the Orientation Panel.
        
        Args:
            parent: Parent PyQt5 widget
            control_queue: Queue to send control commands (fusion worker)
            message_callback: Callback for app messages/logs
            padding: Padding for the frame (default: 6)
        """
        super().__init__("Orientation", parent)
        
        # Store control channel and messaging callback for drift angle updates
        self.control_queue = control_queue
        self.message_callback = message_callback

        # Track whether fusion worker is actively processing data; start False
        self._processing_active = False

        # Track whether gyro calibration is currently running
        self._calibrating = False

        # Track serial connection state reported by GUI worker ('connected', 'stopped', 'error', etc.)
        self._serial_state = None

        # Euler angle display labels
        self.yaw_value_label = None
        self.pitch_value_label = None
        self.roll_value_label = None

        # Drift angle values (defaults)
        from config.config import DEFAULT_CENTER_THRESHOLD, THRESH_DEBOUNCE_MS
        self.drift_angle_yaw_value = DEFAULT_CENTER_THRESHOLD
        self.drift_angle_pitch_value = DEFAULT_CENTER_THRESHOLD
        self.drift_angle_roll_value = DEFAULT_CENTER_THRESHOLD

        # Debounce timers and pending values
        self._drift_yaw_send_timer = QTimer(self)
        self._drift_yaw_send_timer.setSingleShot(True)
        self._drift_yaw_send_timer.timeout.connect(self._apply_drift_angle_yaw)
        self._pending_drift_yaw_value = None

        self._drift_pitch_send_timer = QTimer(self)
        self._drift_pitch_send_timer.setSingleShot(True)
        self._drift_pitch_send_timer.timeout.connect(self._apply_drift_angle_pitch)
        self._pending_drift_pitch_value = None

        self._drift_roll_send_timer = QTimer(self)
        self._drift_roll_send_timer.setSingleShot(True)
        self._drift_roll_send_timer.timeout.connect(self._apply_drift_angle_roll)
        self._pending_drift_roll_value = None

        # Recalibrate button placeholder
        self.recal_button = None

        # Pop-out visualization state
        self._viz_popped_out = False
        self._popup_window = None
        self._popup_square = None
        self._placeholder_square = None
        self._viz_layout = None
        self._viz_index = None
        self._viz_prev_size = None
        self._popup_geom = None
        # Popup opacity (1.0 == fully opaque)
        self._popup_opacity = 1.0
        self._popup_controller = VisualizationPopupController(self)
        self._support_dialogs = {}

        self._build_ui()

        # Ensure interactive controls reflect 'no data' state at startup
        try:
            self.update_processing_status(False)
        except Exception:
            pass

        # Send initial drift angle values shortly after UI is constructed
        self._initial_drift_sent = False
        try:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._send_initial_drift_angle)
            t.start(100)
        except Exception:
            QTimer.singleShot(100, self._send_initial_drift_angle)

    
    def _build_ui(self):
        """Build the orientation panel UI."""
        try:
            _ui_log(self, "[OrientationPanel] _build_ui start")
        except Exception:
            pass
        # Main layout - single column for data displays only
        main_layout = QVBoxLayout()
        # Add modest vertical padding inside the panel to match other panels
        main_layout.setSpacing(DEFAULT_SPACING)
        main_layout.setContentsMargins(*CONTENT_MARGINS)
        self.setLayout(main_layout)
        
        # Allow panel to expand vertically to fill available space when appropriate
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        # Build display components now arranged as a vertical split: visualization left, values right
        split_layout = QHBoxLayout()
        split_layout.setSpacing(DEFAULT_SPACING * 2)
        split_layout.setContentsMargins(0, 0, 0, 0)

        # Left: visualization frame
        viz_frame = QFrame()
        # Use Ignored horizontal policy so the layout distributes width purely
        # according to the stretch factors below (true 50/50 split) instead of
        # being skewed by the frame's content size hints.
        viz_frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        viz_layout = QVBoxLayout(viz_frame)
        viz_layout.setSpacing(DEFAULT_SPACING)
        viz_layout.setContentsMargins(*CONTENT_MARGINS)

        # Visualization title removed (handled by panel header)
        # Instantiate visualization widget (preserve original behavior and size)
        try:
            self.visualization_widget = OrientationVisualizationWidget(self)
        except Exception:
            # Fallback to a minimal placeholder widget if class import fails
            self.visualization_widget = QWidget(self)
            self.visualization_widget.setMinimumSize(200, 200)
            self.visualization_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Ensure the visualization will expand to fill the available space
        try:
            self.visualization_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        except Exception:
            pass

        # Wrap the visualization in a square container so it always
        # remains square and centered, regardless of the panel's shape.
        self.visualization_square = SquareContainer(self.visualization_widget)
        viz_layout.addWidget(self.visualization_square, stretch=1)

        # Pop-out/In control below visualization
        try:
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 0, 0, 0)
            btn_row.setSpacing(DEFAULT_SPACING)
            self.pop_viz_button = QPushButton("Pop Out")
            self.pop_viz_button.setToolTip("Pop the visualization out into a floating window")
            self.pop_viz_button.clicked.connect(self._toggle_viz_popup)
            # Make Pop Out expand horizontally and match the settings button height
            try:
                desired_h = max(self.pop_viz_button.sizeHint().height(), BUTTON_MIN_HEIGHT - 16)
                self.pop_viz_button.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
            except Exception:
                try:
                    self.pop_viz_button.setMinimumHeight(BUTTON_MIN_HEIGHT - 16)
                except Exception:
                    pass
            self.pop_viz_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn_row.addWidget(self.pop_viz_button, 1)

            # Settings (wrench) button aligned to the right
            self.viz_settings_button = QPushButton("⚙")
            self.viz_settings_button.setToolTip("Visualization settings")
            try:
                desired_h = max(self.viz_settings_button.sizeHint().height(), BUTTON_MIN_HEIGHT - 16)
                # Keep a narrow fixed width but match the height of the other toolbar buttons
                self.viz_settings_button.setFixedSize(36, desired_h + BUTTON_EXTRA_HEIGHT)
            except Exception:
                try:
                    self.viz_settings_button.setMinimumHeight(BUTTON_MIN_HEIGHT - 16)
                except Exception:
                    pass
            self.viz_settings_button.setStyleSheet("font-size:14px; padding:0px;")
            self.viz_settings_button.clicked.connect(self._open_viz_settings)
            btn_row.addWidget(self.viz_settings_button)

            viz_layout.addLayout(btn_row)
        except Exception:
            self.pop_viz_button = None

        split_layout.addWidget(viz_frame, stretch=1)

        # Vertical divider styled like calibration panel
        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setObjectName("sectionDivider")
        divider.setFixedWidth(LINE_THICKNESS)
        # Style to match ConnectionPanel vertical divider
        divider.setStyleSheet("background-color: rgba(120,120,120,0.25);")
        split_layout.addWidget(divider)

        # Right: values list (Yaw / Pitch / Roll)
        values_frame = QFrame()
        # Use Ignored horizontal policy so both halves split evenly (matches
        # viz_frame policy above), keeping the divider centered.
        values_frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        values_layout = QVBoxLayout(values_frame)
        # Reduce spacing and margins to remove superfluous padding
        values_layout.setSpacing(DEFAULT_SPACING)
        values_layout.setContentsMargins(*CONTENT_MARGINS)

        # Device status, gyro calibration and drift correction indicators at the top of the values column
        try:
            # Device status
            self.device_status_label = QLabel("Device status: Unknown")
            self.device_status_label.setAlignment(Qt.AlignLeft)
            self.device_status_label.setProperty('status', 'unknown')
            values_layout.addWidget(self.device_status_label)

            # Gyro calibration status indicator (will be wired by calibration panel)
            self.calib_status_label = QLabel("Gyro: Not calibrated")
            self.calib_status_label.setAlignment(Qt.AlignLeft)
            self.calib_status_label.setProperty('status', 'error')
            values_layout.addWidget(self.calib_status_label)

            # Drift correction status
            self.drift_status_label = QLabel("Drift Correction Inactive")
            self.drift_status_label.setProperty("status", "error")
            self.drift_status_label.setAlignment(Qt.AlignLeft)
            values_layout.addWidget(self.drift_status_label)

            # Horizontal divider below the indicators
            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setFrameShadow(QFrame.Sunken)
            divider.setObjectName("statusDivider")
            divider.setFixedHeight(LINE_THICKNESS)
            divider.setStyleSheet("background-color: rgba(120,120,120,0.25);")
            values_layout.addWidget(divider)
        except Exception:
            # Fallback: add labels without divider (same order)
            self.device_status_label = QLabel("Device status: Unknown")
            self.device_status_label.setAlignment(Qt.AlignLeft)
            self.device_status_label.setProperty('status', 'unknown')
            values_layout.addWidget(self.device_status_label)

            self.calib_status_label = QLabel("Gyro: Not calibrated")
            self.calib_status_label.setAlignment(Qt.AlignLeft)
            self.calib_status_label.setProperty('status', 'error')
            values_layout.addWidget(self.calib_status_label)

            self.drift_status_label = QLabel("Drift Correction Inactive")
            self.drift_status_label.setProperty("status", "error")
            self.drift_status_label.setAlignment(Qt.AlignLeft)
            values_layout.addWidget(self.drift_status_label)

        # Replace fixed spacing with flexible spacers so the euler block
        # will be vertically centered between the top and bottom dividers.
        try:
            from PyQt5.QtWidgets import QSpacerItem, QSizePolicy as QSP
            # Use smaller spacers to reduce vertical gap between dividers
            top_spacer = QSpacerItem(20, 8, QSP.Minimum, QSP.Expanding)
            bottom_spacer = QSpacerItem(20, 8, QSP.Minimum, QSP.Expanding)
        except Exception:
            top_spacer = None
            bottom_spacer = None

        # Insert a flexible spacer above the euler grid
        if top_spacer:
            values_layout.addItem(top_spacer)

        # Euler angles: headers on top row, values on second row (three columns)
        euler_grid = QGridLayout()
        euler_grid.setContentsMargins(0, 0, 0, 0)
        # Narrow horizontal gaps between angle columns
        euler_grid.setHorizontalSpacing(DEFAULT_SPACING)
        # Reduce vertical space between header row and value row
        try:
            euler_grid.setVerticalSpacing(DEFAULT_SPACING)
            euler_grid.setRowStretch(0, 0)
            euler_grid.setRowStretch(1, 0)
        except Exception:
            pass

        yaw_label = QLabel("Yaw")
        yaw_label.setAlignment(Qt.AlignCenter)
        yaw_label.setStyleSheet("font-weight: bold;")
        euler_grid.addWidget(yaw_label, 0, 0)

        pitch_label = QLabel("Pitch")
        pitch_label.setAlignment(Qt.AlignCenter)
        pitch_label.setStyleSheet("font-weight: bold;")
        euler_grid.addWidget(pitch_label, 0, 1)

        roll_label = QLabel("Roll")
        roll_label.setAlignment(Qt.AlignCenter)
        roll_label.setStyleSheet("font-weight: bold;")
        euler_grid.addWidget(roll_label, 0, 2)

        # Values row beneath headers
        self.yaw_value_label = QLabel("0.0°")
        self.yaw_value_label.setAlignment(Qt.AlignCenter)
        # Slightly smaller font to reduce perceived size of the Euler block
        self.yaw_value_label.setStyleSheet("font-size: 14px;")
        euler_grid.addWidget(self.yaw_value_label, 1, 0)

        self.pitch_value_label = QLabel("0.0°")
        self.pitch_value_label.setAlignment(Qt.AlignCenter)
        self.pitch_value_label.setStyleSheet("font-size: 14px;")
        euler_grid.addWidget(self.pitch_value_label, 1, 1)

        self.roll_value_label = QLabel("0.0°")
        self.roll_value_label.setAlignment(Qt.AlignCenter)
        self.roll_value_label.setStyleSheet("font-size: 14px;")
        euler_grid.addWidget(self.roll_value_label, 1, 2)

        # Center the Euler grid horizontally within the values column
        try:
            euler_grid.setColumnStretch(0, 1)
            euler_grid.setColumnStretch(1, 1)
            euler_grid.setColumnStretch(2, 1)
        except Exception:
            pass
        # The euler grid shares its space with a "hold still" indicator that is
        # shown whenever no live orientation data is available (fusion inactive
        # or gyro calibration running).
        self._euler_page = QWidget()
        euler_page_layout = QVBoxLayout(self._euler_page)
        euler_page_layout.setContentsMargins(0, 0, 0, 0)
        euler_page_layout.setSpacing(0)
        euler_page_layout.addLayout(euler_grid)

        self.hold_indicator = HoldPanelQt(text="- HOLD STILL & UPRIGHT -", height=40)
        self._hold_page = QWidget()
        hold_page_layout = QVBoxLayout(self._hold_page)
        hold_page_layout.setContentsMargins(0, 0, 0, 0)
        hold_page_layout.setSpacing(0)
        hold_page_layout.addStretch()
        hold_page_layout.addWidget(self.hold_indicator)
        hold_page_layout.addStretch()

        self.euler_stack = QStackedWidget()
        self.euler_stack.addWidget(self._euler_page)
        self.euler_stack.addWidget(self._hold_page)
        values_layout.addWidget(self.euler_stack, 4)

        # Insert a flexible spacer below the euler grid so the grid block is
        # vertically centered between the top and bottom dividers
        if bottom_spacer:
            values_layout.addItem(bottom_spacer)

        # Horizontal divider separating angle displays from buttons
        try:
            angles_buttons_divider = QFrame()
            angles_buttons_divider.setFrameShape(QFrame.HLine)
            angles_buttons_divider.setFrameShadow(QFrame.Sunken)
            angles_buttons_divider.setObjectName("anglesButtonsDivider")
            angles_buttons_divider.setFixedHeight(LINE_THICKNESS)
            angles_buttons_divider.setStyleSheet("background-color: rgba(120,120,120,0.25);")
            values_layout.addWidget(angles_buttons_divider)
        except Exception:
            pass

        # Disengage drift correction button (moved from Calibration panel)
        from PyQt5.QtGui import QFontMetrics, QFont
        self.disengage_btn = TwoLineButton("Disengage Drift Correction", "")
        self.disengage_btn.setCheckable(True)
        self.disengage_btn.setToolTip("Hold to temporarily disable drift correction")

        # Compute size based on bold font like before
        bold_font = QFont(self.disengage_btn.font())
        bold_font.setBold(True)
        fm = QFontMetrics(bold_font)
        text_width = fm.horizontalAdvance("🔴 Drift Correction DISENGAGED")
        text_height = fm.height()
        try:
            # Match ConnectionPanel's prominent button sizing: ensure at least 40px base
            desired_h = max(self.disengage_btn.sizeHint().height(), BUTTON_MIN_HEIGHT)
            # Use fixed height similar to ConnectionPanel (desired_h + BUTTON_EXTRA_HEIGHT)
            self.disengage_btn.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
        except Exception:
            try:
                self.disengage_btn.setMinimumHeight(BUTTON_MIN_HEIGHT)
            except Exception:
                pass
        # Make button span full width of right column; allow vertical resizing preference
        self.disengage_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Default to inactive until fusion worker reports processing active
        try:
            self.disengage_btn.setEnabled(False)
            self.disengage_btn.setProperty('status', 'disabled')
            try:
                self.disengage_btn.style().polish(self.disengage_btn)
            except Exception:
                pass
        except Exception:
            pass

        # The orientation panel owns the disengage action.
        try:
            self.disengage_btn.pressed.connect(self._on_disengage_pressed)
            self.disengage_btn.released.connect(self._on_disengage_released)
        except Exception:
            pass

        # Create a small shortcut-set button to the right of the main disengage button
        disengage_row = QHBoxLayout()
        disengage_row.setSpacing(6)
        disengage_row.setContentsMargins(0, 0, 0, 0)
        # Main button expands; place directly in the row (shortcut will be shown on second line)
        try:
            self.disengage_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            disengage_row.addWidget(self.disengage_btn)
        except Exception:
            self.disengage_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            disengage_row.addWidget(self.disengage_btn)

        # Ensure drift sliders reflect stored values when toggling disengage
        try:
            self.stored_drift_yaw = getattr(self, 'drift_angle_yaw_value', 5.0)
            self.stored_drift_pitch = getattr(self, 'drift_angle_pitch_value', 5.0)
            self.stored_drift_roll = getattr(self, 'drift_angle_roll_value', 5.0)
        except Exception:
            pass

        # Shortcut button uses the same widget and styling path as every other
        # button in the panel; only its width is constrained by the row layout.
        try:
            self.disengage_shortcut_btn = QPushButton("⚙")
            self.disengage_shortcut_btn.setToolTip("Set shortcut for Disengage Drift Correction")
            try:
                desired_h = max(self.disengage_btn.sizeHint().height(), BUTTON_MIN_HEIGHT)
                self.disengage_shortcut_btn.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
            except Exception:
                try:
                    self.disengage_shortcut_btn.setMinimumHeight(BUTTON_MIN_HEIGHT)
                except Exception:
                    pass
            # Shortcut button keeps fixed width but match vertical height
            self.disengage_shortcut_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            disengage_row.addWidget(self.disengage_shortcut_btn)

            # Connect handler to open capture dialog and set shortcut via calibration panel
            def _on_set_disengage_shortcut():
                try:
                    # KeyCaptureDialog is defined above in this module
                    current = getattr(self, 'disengage_shortcut', None)
                    input_cmd_q = getattr(self, 'input_command_queue', None)
                    input_resp_q = getattr(self, 'input_response_queue', None)
                    # Use this panel as owner so pause/resume applies here
                    dlg = KeyCaptureDialog(self, current_key=current, input_command_queue=input_cmd_q, input_response_queue=input_resp_q, owner_panel=self)
                    if dlg.exec_() == QDialog.Accepted and getattr(dlg, 'captured_key', None):
                        key = dlg.captured_key
                        display_name = dlg.display_name or dlg.captured_key

                        # Stop capture mode first so input worker can switch to monitoring
                        try:
                            if input_cmd_q:
                                input_cmd_q.put(('stop_capture',), timeout=0.1)
                        except Exception:
                            pass

                        # Send set_shortcut to input worker (match PreferencesPanel behavior)
                        try:
                            if input_cmd_q:
                                # action identifier used by input worker
                                input_cmd_q.put(('set_shortcut', key, display_name, 'disengage_drift'), timeout=0.1)
                        except Exception:
                            pass

                        # Update this panel's internal state and UI
                        try:
                            self._set_disengage_shortcut(key, display_name)
                        except Exception:
                            pass

                        # Persist via the aggregated debounced save
                        try:
                            self._request_pref_save()
                        except Exception:
                            pass
                except Exception as e:
                    _ui_log(self, f"[OrientationPanel] Error setting disengage shortcut: {e}")

            self.disengage_shortcut_btn.clicked.connect(_on_set_disengage_shortcut)
        except Exception:
            self.disengage_shortcut_btn = None

        # Add the composed row to the layout
        values_layout.addLayout(disengage_row)

        # Connect disengage button toggling to restore stored drift values
        try:
            if hasattr(self, 'disengage_btn') and self.disengage_btn:
                def _on_disengage_toggled():
                    try:
                        if self.disengage_btn.isChecked():
                            # Temporarily disable drift correction: remember current values
                            self.stored_drift_yaw = getattr(self, 'drift_angle_yaw_value', self.stored_drift_yaw)
                            self.stored_drift_pitch = getattr(self, 'drift_angle_pitch_value', self.stored_drift_pitch)
                            self.stored_drift_roll = getattr(self, 'drift_angle_roll_value', self.stored_drift_roll)

                            # Do not change the slider UI. Instead, tell the fusion/control worker to use zero thresholds.
                            try:
                                if self.control_queue:
                                    safe_queue_put(self.control_queue, ('set_threshold', 0.0, 0.0, 0.0), timeout=QUEUE_PUT_TIMEOUT)
                            except Exception:
                                pass
                        else:
                            # Restore stored values in the fusion/control worker but preserve the slider UI values
                            try:
                                if self.control_queue:
                                    safe_queue_put(self.control_queue, ('set_threshold', float(self.stored_drift_yaw), float(self.stored_drift_pitch), float(self.stored_drift_roll)), timeout=QUEUE_PUT_TIMEOUT)
                            except Exception:
                                pass
                    except Exception:
                        pass
                self.disengage_btn.toggled.connect(_on_disengage_toggled)
        except Exception:
            pass

        # Reset Orientation button (moved from Calibration panel)
        # Create a row with main reset button and small shortcut button
        reset_row = QHBoxLayout()
        reset_row.setSpacing(DEFAULT_SPACING)
        reset_row.setContentsMargins(0, 0, 0, 0)

        self.reset_button = TwoLineButton("Reset Orientation", "")
        # Size to match width of column
        self.reset_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Fixed height similar to disengage button
        try:
            # Match ConnectionPanel button sizing: ensure at least BUTTON_MIN_HEIGHT base
            desired_h = max(self.reset_button.sizeHint().height(), BUTTON_MIN_HEIGHT)
            # Ensure reset button is tall enough for two-line rendering
            self.reset_button.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
        except Exception:
            try:
                self.reset_button.setMinimumHeight(BUTTON_MIN_HEIGHT)
            except Exception:
                pass

        # Default to inactive until fusion worker reports processing active
        try:
            self.reset_button.setEnabled(False)
            self.reset_button.setProperty('status', 'disabled')
            try:
                self.reset_button.style().polish(self.reset_button)
            except Exception:
                pass
        except Exception:
            pass

        # Add the reset button directly; shortcut name will be shown on a second line in the button text when set
        try:
            self.reset_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            reset_row.addWidget(self.reset_button)
        except Exception:
            reset_row.addWidget(self.reset_button)

        # Shortcut button uses the same construction and height as reset_button.
        try:
            self.reset_shortcut_btn = QPushButton("⚙")
            self.reset_shortcut_btn.setToolTip("Set shortcut for Reset Orientation")
            try:
                desired_h = max(self.reset_button.sizeHint().height(), BUTTON_MIN_HEIGHT)
                self.reset_shortcut_btn.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
            except Exception:
                try:
                    self.reset_shortcut_btn.setMinimumHeight(BUTTON_MIN_HEIGHT)
                except Exception:
                    pass
            self.reset_shortcut_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            # Shortcut button remains enabled at all times; user can set shortcuts even if
            # fusion processing is inactive. Keep default enabled state and standard styling.
            try:
                reset_row.addWidget(self.reset_shortcut_btn)
            except Exception:
                pass

            def _on_set_reset_shortcut():
                try:
                    # KeyCaptureDialog is defined above in this module
                    current = getattr(self, 'reset_shortcut', None)
                    input_cmd_q = getattr(self, 'input_command_queue', None)
                    input_resp_q = getattr(self, 'input_response_queue', None)
                    # Use this panel as owner so pause/resume applies here
                    dlg = KeyCaptureDialog(self, current_key=current, input_command_queue=input_cmd_q, input_response_queue=input_resp_q, owner_panel=self)
                    if dlg.exec_() == QDialog.Accepted and getattr(dlg, 'captured_key', None):
                        key = dlg.captured_key
                        display_name = dlg.display_name or dlg.captured_key

                        # Stop capture mode before activating the new shortcut
                        try:
                            if input_cmd_q:
                                input_cmd_q.put(('stop_capture',), timeout=0.1)
                        except Exception:
                            pass

                        # Send set_shortcut to input worker (match PreferencesPanel behavior)
                        try:
                            if input_cmd_q:
                                input_cmd_q.put(('set_shortcut', key, display_name, 'reset_orientation'), timeout=0.1)
                        except Exception:
                            pass

                        # Update this panel's internal state and UI
                        try:
                            self._set_reset_shortcut(key, display_name)
                        except Exception:
                            pass

                        # Persist via the aggregated debounced save
                        try:
                            self._request_pref_save()
                        except Exception:
                            pass
                except Exception as e:
                    _ui_log(self, f"[OrientationPanel] Error setting reset shortcut: {e}")

            self.reset_shortcut_btn.clicked.connect(_on_set_reset_shortcut)
        except Exception:
            self.reset_shortcut_btn = None

        # The orientation panel owns the reset action.
        try:
            self.reset_button.clicked.connect(self._on_reset_orientation)
        except Exception:
            pass

        values_layout.addLayout(reset_row)

        # Recalibrate yaw drift correction button (moved from Calibration panel)
        try:
            self.recal_button = QPushButton("Recalibrate Yaw Drift Correction")
            # Match sizing of other buttons but keep this one slightly smaller
            try:
                desired_h = max(self.recal_button.sizeHint().height(), BUTTON_MIN_HEIGHT - 16)
                self.recal_button.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
            except Exception:
                try:
                    # Fallback: ensure minimum height
                    self.recal_button.setMinimumHeight(BUTTON_MIN_HEIGHT - 16)
                except Exception:
                    pass
            self.recal_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            # Default to inactive until fusion worker reports processing active
            try:
                self.recal_button.setEnabled(False)
                self.recal_button.setProperty('status', 'disabled')
                try:
                    self.recal_button.style().polish(self.recal_button)
                except Exception:
                    pass
            except Exception:
                pass
            # The orientation panel owns recalibration.
            try:
                self.recal_button.clicked.connect(self._on_recalibrate)
            except Exception:
                pass

            values_layout.addWidget(self.recal_button)
        except Exception:
            self.recal_button = None

            self.recal_button = None

        # Tools row: Preferences and Monitor share available space, About (?) takes fixed shortcut width
        try:
            tools_row = QHBoxLayout()
            tools_row.setSpacing(DEFAULT_SPACING)
            tools_row.setContentsMargins(0, 0, 0, 0)

            # Preferences button (always active)
            try:
                self.preferences_button = QPushButton("Preferences...")
                try:
                    # Use a slightly smaller minimum so Preferences button doesn't become overly tall
                    desired_h = max(self.preferences_button.sizeHint().height(), BUTTON_MIN_HEIGHT - 16)
                    self.preferences_button.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
                except Exception:
                    try:
                        self.preferences_button.setMinimumHeight(BUTTON_MIN_HEIGHT - 16)
                    except Exception:
                        pass
                self.preferences_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                try:
                    self.preferences_button.setEnabled(True)
                    self.preferences_button.setProperty('status', '')
                except Exception:
                    pass
                self.preferences_button.clicked.connect(self._open_preferences_window)
                # Give a large stretch so this and monitor share remaining space
                tools_row.addWidget(self.preferences_button, 100)
            except Exception:
                self.preferences_button = None

            # Monitor / Logs button
            try:
                self.monitor_button = QPushButton("Monitor / Logs")
                try:
                    # Keep Monitor a bit smaller than the prominent action buttons
                    desired_h = max(self.monitor_button.sizeHint().height(), BUTTON_MIN_HEIGHT - 16)
                    self.monitor_button.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
                except Exception:
                    try:
                        self.monitor_button.setMinimumHeight(BUTTON_MIN_HEIGHT - 16)
                    except Exception:
                        pass
                self.monitor_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                try:
                    self.monitor_button.setEnabled(True)
                    self.monitor_button.setProperty('status', '')
                except Exception:
                    pass
                self.monitor_button.clicked.connect(self._open_monitor_window)
                tools_row.addWidget(self.monitor_button, 100)
            except Exception:
                self.monitor_button = None

            # About/help button (small, align with shortcut buttons)
            try:
                self.about_button = QPushButton("?")
                self.about_button.setToolTip("About this application")
                try:
                    # Prefer actual painted width of an existing shortcut button when available,
                    # otherwise fall back to sizeHint width or a small default.
                    shortcut_w = None
                    for nm in ('reset_shortcut_btn', 'disengage_shortcut_btn', 'disengage_shortcut_btn'):
                        btn = getattr(self, nm, None)
                        if btn is not None:
                            try:
                                w = btn.width()
                                if w and w > 8:
                                    shortcut_w = w
                                    break
                            except Exception:
                                pass
                            try:
                                w = btn.sizeHint().width()
                                if w and w > 8:
                                    shortcut_w = w
                                    break
                            except Exception:
                                pass
                    if shortcut_w is None:
                        # Fall back to sizeHint if painted width not available
                        try:
                            shortcut_w = self.reset_shortcut_btn.sizeHint().width() if hasattr(self, 'reset_shortcut_btn') and self.reset_shortcut_btn else 36
                        except Exception:
                            shortcut_w = 36
                    # Clamp the width to a reasonable range so it doesn't overflow
                    try:
                        shortcut_w = int(shortcut_w)
                        # clamp between 24 and 48
                        if shortcut_w < 24:
                            shortcut_w = 24
                        elif shortcut_w > 48:
                            shortcut_w = 40
                    except Exception:
                        shortcut_w = 36
                    # Use same vertical sizing as tools
                    desired_h = max(self.about_button.sizeHint().height(), BUTTON_MIN_HEIGHT - 16)
                    self.about_button.setFixedHeight(desired_h + BUTTON_EXTRA_HEIGHT)
                    # Ensure a small fixed width matching shortcut buttons
                    self.about_button.setFixedWidth(int(shortcut_w))
                except Exception:
                    pass
                self.about_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                self.about_button.clicked.connect(self._open_about_window)
                # Add without stretch so it remains fixed while others expand
                tools_row.addWidget(self.about_button, 0)
            except Exception:
                self.about_button = None

            values_layout.addLayout(tools_row)
        except Exception:
            # Fallback: add buttons individually
            try:
                if not hasattr(self, 'preferences_button') or self.preferences_button is None:
                    self.preferences_button = QPushButton("Preferences...")
                    self.preferences_button.clicked.connect(self._open_preferences_window)
                    values_layout.addWidget(self.preferences_button)
            except Exception:
                pass
            try:
                if not hasattr(self, 'monitor_button') or self.monitor_button is None:
                    self.monitor_button = QPushButton("Monitor / Logs")
                    self.monitor_button.clicked.connect(self._open_monitor_window)
                    values_layout.addWidget(self.monitor_button)
            except Exception:
                pass

        split_layout.addWidget(values_frame, stretch=1)

        # Wrap the top area (visualization + controls) in its own frame so
        # the sliders below are a separate, non-overlapping section.
        try:
            top_frame = QFrame()
            top_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            top_layout = QVBoxLayout(top_frame)
            top_layout.setContentsMargins(0, 0, 0, 0)
            top_layout.setSpacing(0)
            top_layout.addLayout(split_layout)
            # Give the top area the primary stretch so it doesn't starve
            # the sliders area of vertical space. Sliders will be added
            # below with a smaller stretch so they remain visible.
            main_layout.addWidget(top_frame, 1)
            try:
                _ui_log(self, "[OrientationPanel] added top_frame to main_layout")
            except Exception:
                pass
        except Exception:
            # Fallback to adding the layout directly if widget wrapping fails
            main_layout.addLayout(split_layout)

        # NOTE: slider widgets are created later in this method; connections
        # are established immediately after each slider is instantiated.

        # Horizontal divider separating main panel from sliders
        # (Divider now inserted as part of the sliders block below; removed duplicate here)

        # --- Input response monitoring ---
        try:
            # Create a timer that polls the input_response_queue (if assigned) and
            # dispatches shortcut events to local handlers. This mirrors the old
            # CalibrationPanel behavior so KeyCaptureDialog can pause/resume it.
            self.input_response_timer = QTimer()
            self.input_response_timer.timeout.connect(self._process_input_responses)
            # Start the timer; handler will no-op if queue is not provided yet.
            self.input_response_timer.start(50)
        except Exception:
            self.input_response_timer = None

        # --- Drift angle sliders spanning full width ---
        try:
            _ui_log(self, "[OrientationPanel] entering sliders construction block")

            sliders_frame = QFrame()
            sliders_layout = QVBoxLayout(sliders_frame)
            sliders_layout.setContentsMargins(*CONTENT_MARGINS)
            sliders_layout.setSpacing(DEFAULT_SPACING)

            # Header
            header = QLabel("Drift Correction Angles")
            header.setAlignment(Qt.AlignCenter)
            header.setStyleSheet("font-weight: bold; margin-bottom: 4px;")
            sliders_layout.addWidget(header)

            # Yaw
            yaw_row = QHBoxLayout()
            yaw_label = QLabel("Yaw:")
            yaw_label.setMinimumWidth(40)
            yaw_row.addWidget(yaw_label)
            self.drift_yaw_slider = QSlider(Qt.Horizontal)
            self.drift_yaw_slider.setMinimum(0)
            self.drift_yaw_slider.setMaximum(250)
            self.drift_yaw_slider.setValue(int(self.drift_angle_yaw_value * 10))
            try:
                _ui_log(self, f"[OrientationPanel] created drift_yaw_slider: {self.drift_yaw_slider}")
            except Exception:
                pass
            # Connect slider to handler so changes update viz/state
            try:
                self.drift_yaw_slider.valueChanged.connect(self._on_drift_yaw_angle_change)
            except Exception:
                pass
            yaw_row.addWidget(self.drift_yaw_slider, 1)
            self.drift_angle_yaw_label = QLabel(f"{self.drift_angle_yaw_value:.1f}°")
            self.drift_angle_yaw_label.setMinimumWidth(40)
            self.drift_angle_yaw_label.setAlignment(Qt.AlignCenter)
            yaw_row.addWidget(self.drift_angle_yaw_label)
            sliders_layout.addLayout(yaw_row)

            # Pitch
            pitch_row = QHBoxLayout()
            pitch_label = QLabel("Pitch:")
            pitch_label.setMinimumWidth(40)
            pitch_row.addWidget(pitch_label)
            self.drift_pitch_slider = QSlider(Qt.Horizontal)
            self.drift_pitch_slider.setMinimum(0)
            self.drift_pitch_slider.setMaximum(250)
            self.drift_pitch_slider.setValue(int(self.drift_angle_pitch_value * 10))
            try:
                _ui_log(self, f"[OrientationPanel] created drift_pitch_slider: {self.drift_pitch_slider}")
            except Exception:
                pass
            try:
                self.drift_pitch_slider.valueChanged.connect(self._on_drift_pitch_angle_change)
            except Exception:
                pass
            pitch_row.addWidget(self.drift_pitch_slider, 1)
            self.drift_angle_pitch_label = QLabel(f"{self.drift_angle_pitch_value:.1f}°")
            self.drift_angle_pitch_label.setMinimumWidth(40)
            self.drift_angle_pitch_label.setAlignment(Qt.AlignCenter)
            pitch_row.addWidget(self.drift_angle_pitch_label)
            sliders_layout.addLayout(pitch_row)

            # Roll
            roll_row = QHBoxLayout()
            roll_label = QLabel("Roll:")
            roll_label.setMinimumWidth(40)
            roll_row.addWidget(roll_label)
            self.drift_roll_slider = QSlider(Qt.Horizontal)
            self.drift_roll_slider.setMinimum(0)
            self.drift_roll_slider.setMaximum(250)
            self.drift_roll_slider.setValue(int(self.drift_angle_roll_value * 10))
            try:
                _ui_log(self, f"[OrientationPanel] created drift_roll_slider: {self.drift_roll_slider}")
            except Exception:
                pass
            try:
                self.drift_roll_slider.valueChanged.connect(self._on_drift_roll_angle_change)
            except Exception:
                pass
            roll_row.addWidget(self.drift_roll_slider, 1)
            self.drift_angle_roll_label = QLabel(f"{self.drift_angle_roll_value:.1f}°")
            self.drift_angle_roll_label.setMinimumWidth(40)
            self.drift_angle_roll_label.setAlignment(Qt.AlignCenter)
            roll_row.addWidget(self.drift_angle_roll_label)
            sliders_layout.addLayout(roll_row)

            # Insert a horizontal divider immediately above the sliders so
            # they visually separate from the top visualization/controls area.
            try:
                sliders_div = QFrame()
                sliders_div.setFrameShape(QFrame.HLine)
                sliders_div.setFrameShadow(QFrame.Sunken)
                sliders_div.setFixedHeight(LINE_THICKNESS)
                sliders_div.setStyleSheet("background-color: rgba(120,120,120,0.25);")
                main_layout.addWidget(sliders_div)
            except Exception:
                pass

            # Ensure sliders area cannot be collapsed by surrounding layouts.
            # Use Preferred vertical policy so the layout can allocate space,
            # and set a sensible minimum height so sliders remain visible.
            try:
                sliders_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                # Prefer a minimum height so the sliders area can shrink when
                # space is constrained while still remaining visible.
                sliders_frame.setMinimumHeight(120)
            except Exception:
                pass

            # Add sliders area with no stretch so it keeps a modest fixed
            # allocation below the top_frame rather than being expanded.
            try:
                main_layout.addWidget(sliders_frame, 0)
            except Exception:
                main_layout.addWidget(sliders_frame)

            # Debug: print existence and size hints immediately and again
            # after a short delay so we can observe allocation post-layout.
            try:
                def _debug_print_slider_geometries():
                    try:
                        sf_geo = sliders_frame.geometry() if hasattr(sliders_frame, 'geometry') else None
                        sf_hint = sliders_frame.sizeHint() if hasattr(sliders_frame, 'sizeHint') else None
                        yaw_exists = hasattr(self, 'drift_yaw_slider') and self.drift_yaw_slider is not None
                        pitch_exists = hasattr(self, 'drift_pitch_slider') and self.drift_pitch_slider is not None
                        roll_exists = hasattr(self, 'drift_roll_slider') and self.drift_roll_slider is not None
                        yaw_hint = self.drift_yaw_slider.sizeHint() if yaw_exists else None
                        pitch_hint = self.drift_pitch_slider.sizeHint() if pitch_exists else None
                        roll_hint = self.drift_roll_slider.sizeHint() if roll_exists else None
                        min_h = sliders_frame.minimumHeight() if hasattr(sliders_frame, 'minimumHeight') else None
                        _ui_log(self, f"[OrientationPanel] sliders_frame.geo={sf_geo}, hint={sf_hint}, minH={min_h}, yaw_exists={yaw_exists}, yaw_hint={yaw_hint}, pitch_exists={pitch_exists}, pitch_hint={pitch_hint}, roll_exists={roll_exists}, roll_hint={roll_hint}")
                    except Exception as e:
                        _ui_log(self, f"[OrientationPanel] debug geometry error: {e}")

                # Immediate (post-construction) data
                _debug_print_slider_geometries()

                # Delayed check after layout pass
                QTimer.singleShot(250, _debug_print_slider_geometries)
            except Exception:
                pass
        except Exception as e:
            try:
                _ui_log(self, f"[OrientationPanel] exception constructing sliders: {e}")
            except Exception:
                pass

    def _open_preferences_window(self):
        """
        Open a dialog containing the PreferencesPanel. If a preferences panel
        instance was previously created by the main GUI (in the Preferences tab),
        reuse that instance by reparenting it into the dialog and removing its
        tab from the main TabbedGUIWorker. Otherwise create a new PreferencesPanel
        instance and show it in the dialog.
        """
        try:
            existing = self._support_dialogs.get('preferences')
            if existing is not None:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            parent_window = None
            try:
                parent_window = self.window()
            except Exception:
                parent_window = None

            dialog = QDialog(parent_window if parent_window is not None else self)
            self._support_dialogs['preferences'] = dialog
            dialog.setAttribute(Qt.WA_DeleteOnClose, False)
            dialog.setWindowTitle("Preferences")
            dialog.setModal(False)
            dlg_layout = QVBoxLayout(dialog)
            dlg_layout.setContentsMargins(DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN)

            prefs_widget = None
            # If a preferences_panel exists (created by GUI worker), prefer to reuse it
            prefs = getattr(self, 'preferences_panel', None)
            if prefs is not None:
                try:
                    # Remove from any existing parent/layout by reparenting
                    prefs.setParent(dialog)
                    prefs_widget = prefs
                except Exception:
                    prefs_widget = None

            if prefs_widget is None:
                try:
                    # Create a new PreferencesPanel using available queues/managers
                    prefs_widget = PreferencesPanel(dialog)
                except Exception:
                    prefs_widget = None

            if prefs_widget is not None:
                dlg_layout.addWidget(prefs_widget)
                # Connect to calibration panel if available
                try:
                    if hasattr(prefs_widget, 'connect_orientation_panel'):
                        prefs_widget.connect_orientation_panel(self)
                except Exception:
                    pass

                # If parent_window can save/apply theme, wire signals back
                try:
                    if parent_window is not None:
                        if hasattr(parent_window, '_apply_theme') and hasattr(prefs_widget, 'theme_changed'):
                            prefs_widget.theme_changed.connect(parent_window._apply_theme)
                        if hasattr(parent_window, 'save_preferences') and hasattr(prefs_widget, 'preferences_changed'):
                            prefs_widget.preferences_changed.connect(parent_window.save_preferences)
                except Exception:
                    pass


            # Apply parent's palette/style so the dialog matches the app theme
            try:
                if parent_window is not None:
                    try:
                        dialog.setStyleSheet(parent_window.styleSheet())
                    except Exception:
                        pass
                    try:
                        dialog.setPalette(parent_window.palette())
                        dialog.setAutoFillBackground(True)
                    except Exception:
                        pass

                # Resize to fit the full preferences page or use a sensible minimum
                try:
                    hint = prefs_widget.sizeHint() if prefs_widget is not None else None
                    w = max(900, hint.width() + 40 if hint is not None else 900)
                    h = max(700, hint.height() + 80 if hint is not None else 700)
                    dialog.resize(int(w), int(h))
                except Exception:
                    try:
                        dialog.resize(900, 700)
                    except Exception:
                        pass

                # Show dialog non-modally
                dialog.show()
            except Exception:
                try:
                    dialog.exec_()
                except Exception:
                    pass
        except Exception as e:
            try:
                _ui_log(self, f"[OrientationPanel] Failed to open preferences window: {e}")
            except Exception:
                pass

        # Connect some leftover slider signals (best-effort)
        try:
            self.drift_pitch_slider.valueChanged.connect(self._on_drift_pitch_angle_change)
        except Exception:
            pass

        # --- Monitor / Logs dialog opener ---
    def _open_monitor_window(self):
        """
        Open a dialog containing the MessagePanel. Reuse an existing MessagePanel
        instance created by the main GUI when possible (reparenting it into the
        dialog) so logs and serial output remain continuous. Otherwise create a
        new MessagePanelQt instance attached to the dialog.
        """
        try:
            existing = self._support_dialogs.get('monitor')
            if existing is not None:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            parent_window = None
            try:
                parent_window = self.window()
            except Exception:
                parent_window = None

            dialog = QDialog(parent_window if parent_window is not None else self)
            self._support_dialogs['monitor'] = dialog
            dialog.setAttribute(Qt.WA_DeleteOnClose, False)
            dialog.setWindowTitle("Monitor / Logs")
            dialog.setModal(False)
            dlg_layout = QVBoxLayout(dialog)
            dlg_layout.setContentsMargins(DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN)

            msg_widget = None
            # Prefer the shared message panel owned by the main window.
            cand = getattr(self, 'message_panel', None)
            if cand is None and parent_window is not None:
                cand = getattr(parent_window, 'message_panel', None)

            if cand is not None:
                try:
                    cand.setParent(dialog)
                    msg_widget = cand
                except Exception:
                    msg_widget = None

            if msg_widget is None:
                try:
                    # Create a local MessagePanel with generous history sizes
                    msg_widget = MessagePanelQt(dialog, serial_height=12, message_height=12, max_serial_lines=500, max_message_lines=200, padding=6)
                except Exception:
                    msg_widget = None

            if msg_widget is not None:
                dlg_layout.addWidget(msg_widget)

            # Apply parent's palette/style so the dialog matches the app theme
            try:
                if parent_window is not None:
                    try:
                        dialog.setStyleSheet(parent_window.styleSheet())
                    except Exception:
                        pass
                    try:
                        dialog.setPalette(parent_window.palette())
                        dialog.setAutoFillBackground(True)
                    except Exception:
                        pass

                # Resize to a sensible minimum for logs
                try:
                    hint = msg_widget.sizeHint() if msg_widget is not None else None
                    w = max(800, hint.width() + 40 if hint is not None else 800)
                    h = max(600, hint.height() + 80 if hint is not None else 600)
                    dialog.resize(int(w), int(h))
                except Exception:
                    try:
                        dialog.resize(800, 600)
                    except Exception:
                        pass

                dialog.show()
            except Exception:
                try:
                    dialog.exec_()
                except Exception:
                    pass
        except Exception as e:
            try:
                _ui_log(self, f"[OrientationPanel] Failed to open monitor window: {e}")
            except Exception:
                pass
        try:
            self.drift_roll_slider.valueChanged.connect(self._on_drift_roll_angle_change)
        except Exception:
            pass

        # --- About dialog opener ---
    def _open_about_window(self):
        """
        Open a dialog containing the AboutPanel. Reuse an existing AboutPanel
        instance from the main window when possible (reparenting it into the
        dialog) or create a fresh one if not available.
        """
        try:
            existing = self._support_dialogs.get('about')
            if existing is not None:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            parent_window = None
            try:
                parent_window = self.window()
            except Exception:
                parent_window = None

            dialog = QDialog(parent_window if parent_window is not None else self)
            self._support_dialogs['about'] = dialog
            dialog.setAttribute(Qt.WA_DeleteOnClose, False)
            dialog.setWindowTitle("About")
            dialog.setModal(False)
            dlg_layout = QVBoxLayout(dialog)
            dlg_layout.setContentsMargins(DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN)

            about_widget = None
            # Prefer the shared about panel owned by the main window.
            cand = getattr(self, 'about_panel', None)
            if cand is None and parent_window is not None:
                cand = getattr(parent_window, 'about_panel', None)

            if cand is not None:
                try:
                    cand.setParent(dialog)
                    about_widget = cand
                except Exception:
                    about_widget = None

            if about_widget is None:
                try:
                    about_widget = AboutPanel(dialog)
                except Exception:
                    about_widget = None

            if about_widget is not None:
                dlg_layout.addWidget(about_widget)

            # Apply parent's palette/style so the dialog matches the app theme
            try:
                if parent_window is not None:
                    try:
                        dialog.setStyleSheet(parent_window.styleSheet())
                    except Exception:
                        pass
                    try:
                        dialog.setPalette(parent_window.palette())
                        dialog.setAutoFillBackground(True)
                    except Exception:
                        pass

                # Resize to a sensible minimum for about content
                try:
                    hint = about_widget.sizeHint() if about_widget is not None else None
                    w = max(500, hint.width() + 40 if hint is not None else 500)
                    h = max(400, hint.height() + 80 if hint is not None else 400)
                    dialog.resize(int(w), int(h))
                except Exception:
                    try:
                        dialog.resize(500, 400)
                    except Exception:
                        pass

                dialog.show()
            except Exception:
                try:
                    dialog.exec_()
                except Exception:
                    pass
        except Exception as e:
            try:
                _ui_log(self, f"[OrientationPanel] Failed to open about window: {e}")
            except Exception:
                pass

        # Sliders are constructed during _build_ui; removed duplicate block here.

    def _build_euler_displays(self, parent_layout):
        """Build Euler angle (Yaw, Pitch, Roll) display row."""
        # Create grid layout for euler angles
        euler_grid = QGridLayout()
        euler_grid.setContentsMargins(*CONTENT_MARGINS)  # Add horizontal + vertical padding
        
        # Row 0: Yaw, Pitch, Roll
        euler_grid.addWidget(QLabel("Yaw:"), 0, 0)
        self.yaw_value_label = QLabel("0.0")
        self.yaw_value_label.setMinimumWidth(50)
        self.yaw_value_label.setAlignment(Qt.AlignCenter)  # Center the value
        euler_grid.addWidget(self.yaw_value_label, 0, 1)
        
        euler_grid.addWidget(QLabel("Pitch:"), 0, 2)
        self.pitch_value_label = QLabel("0.0")
        self.pitch_value_label.setMinimumWidth(50)
        self.pitch_value_label.setAlignment(Qt.AlignCenter)  # Center the value
        euler_grid.addWidget(self.pitch_value_label, 0, 3)
        
        euler_grid.addWidget(QLabel("Roll:"), 0, 4)
        self.roll_value_label = QLabel("0.0")
        self.roll_value_label.setMinimumWidth(50)
        self.roll_value_label.setAlignment(Qt.AlignCenter)  # Center the value
        euler_grid.addWidget(self.roll_value_label, 0, 5)
        
        # Add grid to parent layout
        parent_layout.addLayout(euler_grid)
    
    def update_euler(self, yaw, pitch, roll):
        """
        Update Euler angle displays and visualization.
        
        Args:
            yaw: Yaw angle in degrees
            pitch: Pitch angle in degrees
            roll: Roll angle in degrees
        """
        try:
            # Update numeric displays
            try:
                self.yaw_value_label.setText(f"{float(yaw):.1f}")
                self.pitch_value_label.setText(f"{float(pitch):.1f}")
                self.roll_value_label.setText(f"{float(roll):.1f}")
            except Exception:
                pass

            # Update the visualization widget directly (preferred)
            try:
                if hasattr(self, 'visualization_widget') and self.visualization_widget:
                    # OrientationVisualizationWidget.update_orientation(pitch, yaw, roll)
                    # The visualization implementation expects pitch first, then yaw, then roll.
                    try:
                        self.visualization_widget.update_orientation(pitch, yaw, roll)
                    except Exception:
                        # Fall back to yaw,pitch,roll ordering if a legacy implementation is present
                        try:
                            self.visualization_widget.update_orientation(yaw, pitch, roll)
                        except Exception:
                            pass
            except Exception:
                pass

        except Exception:
            pass

    def _create_popup_window(self):
        """Create a frameless always-on-top window to host the visualization.
        The popup uses a translucent background and an inner rounded frame so
        the visible window corners are rounded while preserving the current
        application palette (theme) for the inner background color.
        """
        try:
            from PyQt5.QtWidgets import QWidget, QFrame
            from PyQt5.QtGui import QPalette
            from PyQt5.QtCore import Qt as _Qt

            # Frameless translucent top-level widget so we can draw rounded corners
            popup = QWidget(None, Qt.Window | Qt.FramelessWindowHint | Qt.Tool)
            popup.setObjectName('visualizationPopup')
            popup.setAttribute(Qt.WA_StyledBackground, True)
            popup.setWindowFlags(popup.windowFlags() | Qt.WindowStaysOnTopHint)
            # Allow per-pixel transparency so rounded corners are truly transparent
            popup.setAttribute(Qt.WA_TranslucentBackground, True)
            popup.setAttribute(Qt.WA_ShowWithoutActivating, True)

            # Inner frame that will receive the rounded background and border.
            # Give it the same object name used by the theme so global QSS
            # selectors in the app stylesheet apply to this inner widget.
            container = QFrame(popup)
            container.setObjectName('visualizationPopup')
            container.setAttribute(Qt.WA_StyledBackground, True)

            # Copy the application's stylesheet (or parent window's) onto the
            # popup so QSS rules are honored for top-level/tool windows.
            try:
                parent_window = None
                try:
                    parent_window = self.window()
                except Exception:
                    parent_window = None
                app = QApplication.instance()
                global_sheet = None
                if parent_window is not None:
                    try:
                        global_sheet = parent_window.styleSheet()
                    except Exception:
                        global_sheet = None
                if not global_sheet and app is not None:
                    try:
                        global_sheet = app.styleSheet()
                    except Exception:
                        global_sheet = None
                if global_sheet:
                    try:
                        popup.setStyleSheet(global_sheet)
                    except Exception:
                        pass
                # Also copy palette so non-QSS widgets still follow theme
                try:
                    if parent_window is not None:
                        popup.setPalette(parent_window.palette())
                        popup.setAutoFillBackground(True)
                    elif app is not None:
                        popup.setPalette(app.palette())
                        popup.setAutoFillBackground(True)
                except Exception:
                    pass
            except Exception:
                pass

            # Minimal inline overrides: adjust corner radius and border thickness
            # while leaving colors to the global stylesheet so the popup follows
            # the current theme (dark/light).
            try:
                radius = 10
                container.setStyleSheet(f"border-radius: {radius}px; border: 1px solid palette(mid);")
            except Exception:
                container.setStyleSheet("border-radius: 10px; border: 1px solid #555555; background-color: #3c3c3c; color: #ffffff; ")

            # Layout: place the rounded container inside popup with zero margins
            outer_layout = QVBoxLayout(popup)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            outer_layout.addWidget(container)

            # Add the square visualization inside the rounded container with a small inner margin
            inner_layout = QVBoxLayout(container)
            inner_layout.setContentsMargins(DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN)
            square = SquareContainer(self.visualization_widget, parent=container)
            inner_layout.addWidget(square)

            # Position the popup using stored geometry or a sensible default
            screen = QApplication.primaryScreen()
            geom = screen.availableGeometry()
            try:
                if isinstance(self._popup_geom, QRect):
                    w = max(64, int(self._popup_geom.width()))
                    h = max(64, int(self._popup_geom.height()))
                    x = int(self._popup_geom.x())
                    y = int(self._popup_geom.y())
                else:
                    w = 320
                    h = 320
                    x = geom.right() - w - 24
                    y = geom.bottom() - h - 24
            except Exception:
                w = 320
                h = 320
                x = geom.right() - w - 24
                y = geom.bottom() - h - 24

            popup.setGeometry(x, y, w, h)

            # Apply stored opacity (applies to the whole window)
            try:
                popup.setWindowOpacity(max(0.0, min(1.0, float(self._popup_opacity))))
            except Exception:
                pass

            popup.show()
            try:
                self._popup_geom = popup.geometry()
            except Exception:
                self._popup_geom = None
            try:
                popup.installEventFilter(self)
            except Exception:
                pass
            return popup, square
        except Exception:
            return None, None

    def _toggle_viz_popup(self):
        """Toggle visualization between embedded and popped-out states."""
        self._popup_controller.toggle()
        return
        try:
            if not self._viz_popped_out:
                # Record previous visualization container size so we can restore it on pop-in
                try:
                    self._viz_prev_size = self.visualization_square.size()
                except Exception:
                    self._viz_prev_size = None
                # Record original layout and index so we can restore later
                try:
                    parent = self.visualization_square.parent()
                    self._viz_layout = parent.layout() if parent is not None else None
                    self._viz_index = None
                    if self._viz_layout is not None:
                        for idx in range(self._viz_layout.count()):
                            it = self._viz_layout.itemAt(idx)
                            try:
                                w = it.widget()
                            except Exception:
                                w = None
                            if w is self.visualization_square:
                                self._viz_index = idx
                                break
                except Exception:
                    self._viz_layout = None
                    self._viz_index = None

                # Create placeholder to keep layout spacing
                placeholder = QWidget()
                placeholder.setMinimumSize(160, 160)
                placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

                # Remove the visualization_square from its layout and insert placeholder
                try:
                    if self._viz_layout is not None and self._viz_index is not None:
                        item = self._viz_layout.takeAt(self._viz_index)
                        try:
                            if item and item.widget():
                                item.widget().setParent(None)
                        except Exception:
                            pass
                        self._viz_layout.insertWidget(self._viz_index, placeholder, stretch=1)
                    else:
                        try:
                            self.visualization_square.setParent(None)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Create popup which will reparent the visualization widget into the popup's square
                popup, square = self._create_popup_window()
                if popup is None:
                    # Failed to create popup: try to restore original placement
                    try:
                        if self._viz_layout is not None and self._viz_index is not None:
                            # remove placeholder
                            for i in range(self._viz_layout.count()):
                                it = self._viz_layout.itemAt(i)
                                if it and it.widget() is placeholder:
                                    self._viz_layout.takeAt(i)
                                    break
                            self._viz_layout.insertWidget(self._viz_index, self.visualization_square, stretch=1)
                    except Exception:
                        pass
                    return

                # Save popup and placeholder references
                self._popup_window = popup
                self._popup_square = square
                self._placeholder_square = placeholder

                self.pop_viz_button.setText("Pop In")
                self._viz_popped_out = True
            else:
                # Pop in: move visualization back into its original container
                try:
                    if self._popup_window:
                        # Detach visualization from popup
                        try:
                            self.visualization_widget.setParent(None)
                        except Exception:
                            pass

                        # Reparent visualization into the original square container
                        try:
                            self.visualization_widget.setParent(self.visualization_square)
                            self.visualization_square._child = self.visualization_widget
                        except Exception:
                            pass

                        # Replace placeholder with the original square in the recorded layout
                        try:
                            if self._viz_layout is not None and self._viz_index is not None:
                                replaced = False
                                for i in range(self._viz_layout.count()):
                                    it = self._viz_layout.itemAt(i)
                                    if it and it.widget() is self._placeholder_square:
                                        self._viz_layout.takeAt(i)
                                        self._viz_layout.insertWidget(i, self.visualization_square, stretch=1)
                                        replaced = True
                                        break
                                if not replaced:
                                    # fallback: insert at stored index
                                    self._viz_layout.insertWidget(self._viz_index, self.visualization_square, stretch=1)
                            else:
                                # fallback: try to add back to a reasonable parent
                                try:
                                    parent = self.visualization_square.parent()
                                    if parent is not None:
                                        parent.layout().addWidget(self.visualization_square, stretch=1)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        try:
                            self._popup_window.close()
                        except Exception:
                            pass
                        # Force a geometry refresh on the restored square so the
                        # child visualization is laid out at the correct size.
                        try:
                            if self._viz_prev_size is not None:
                                try:
                                    # Apply fixed size to the square to restore previous scale
                                    self.visualization_square.setFixedSize(self._viz_prev_size.width(), self._viz_prev_size.height())
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        try:
                            if hasattr(self.visualization_square, 'refresh_child_geometry'):
                                QTimer.singleShot(0, self.visualization_square.refresh_child_geometry)
                        except Exception:
                            pass
                        try:
                            self.visualization_widget.update()
                        except Exception:
                            pass
                        # Remove fixed size after a short delay to let layouts resume control
                        try:
                            def _clear_fixed():
                                try:
                                    self.visualization_square.setMinimumSize(0, 0)
                                    self.visualization_square.setMaximumSize(16777215, 16777215)
                                    self.visualization_square.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                                    try:
                                        self.visualization_square.update()
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                            QTimer.singleShot(150, _clear_fixed)
                        except Exception:
                            pass
                    # Clear stored popup/placeholder/layout info
                    self._popup_window = None
                    self._popup_square = None
                    self._placeholder_square = None
                    self._viz_layout = None
                    self._viz_index = None
                except Exception:
                    pass
                self.pop_viz_button.setText("Pop Out")
                self._viz_popped_out = False
        except Exception:
            pass

    def _open_viz_settings(self):
        """Open a small dialog allowing the user to set popout size and position."""
        try:
            dlg = QDialog(self)
            dlg.setObjectName('visualizationSettingsDialog')
            dlg.setAttribute(Qt.WA_StyledBackground, True)
            dlg.setWindowTitle("Visualization Settings")
            layout = QVBoxLayout(dlg)
            # Reduce vertical spacing and margins so the dialog is compact
            try:
                layout.setSpacing(6)
                layout.setContentsMargins(DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN)
            except Exception:
                pass

            # Remember the original geometry so Cancel can restore it
            orig_geom = None
            try:
                if self._popup_window and self._popup_geom is not None:
                    orig_geom = QRect(self._popup_geom)
                elif self._popup_window:
                    orig_geom = QRect(self._popup_window.geometry())
                elif self._popup_geom is not None:
                    orig_geom = QRect(self._popup_geom)
            except Exception:
                orig_geom = None

            # Create a labeled group so the dialog contents match other panels
            try:
                group = QGroupBox("Popout Settings")
                group.setObjectName('visualizationSettingsGroup')
                group_layout = QVBoxLayout(group)
                group_layout.setSpacing(6)
                group_layout.setContentsMargins(DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN)
            except Exception:
                group = None
                group_layout = None

            # Size setting
            size_row = QHBoxLayout()
            try:
                size_row.setContentsMargins(0, 2, 0, 2)
                size_row.setSpacing(6)
            except Exception:
                pass
            size_row.addWidget(QLabel("Popout size:"))
            self._size_slider = QSlider(Qt.Horizontal)
            self._size_slider.setMinimum(200)
            self._size_slider.setMaximum(1200)
            current_size = 320
            try:
                if self._popup_window:
                    current_size = max(64, int(self._popup_window.width()))
                elif self._popup_geom is not None:
                    current_size = max(64, int(self._popup_geom.width()))
            except Exception:
                current_size = 320
            self._size_slider.setValue(current_size)
            # Show current pixel value next to the slider so users see absolute size
            try:
                self._size_value_label = QLabel(f"{int(current_size)} px")
                self._size_value_label.setMinimumWidth(64)
                self._size_value_label.setAlignment(Qt.AlignCenter)
            except Exception:
                self._size_value_label = QLabel(f"{current_size} px")
            size_row.addWidget(self._size_slider, 1)
            size_row.addWidget(self._size_value_label)
            if group_layout is not None:
                group_layout.addLayout(size_row)
            else:
                layout.addLayout(size_row)

            # Opacity setting (0-100 mapped to 0.0-1.0)
            opacity_row = QHBoxLayout()
            try:
                opacity_row.setContentsMargins(0, 2, 0, 2)
                opacity_row.setSpacing(6)
            except Exception:
                pass
            opacity_row.addWidget(QLabel("Popout opacity:"))
            from PyQt5.QtWidgets import QSpinBox
            self._opacity_slider = QSlider(Qt.Horizontal)
            self._opacity_slider.setMinimum(10)
            self._opacity_slider.setMaximum(100)
            try:
                cur_op = int(max(10, min(100, int(self._popup_opacity * 100))))
            except Exception:
                cur_op = 100
            self._opacity_slider.setValue(cur_op)
            opacity_row.addWidget(self._opacity_slider, 1)
            self._opacity_label = QLabel(f"{cur_op}%")
            self._opacity_label.setMinimumWidth(48)
            self._opacity_label.setAlignment(Qt.AlignCenter)
            opacity_row.addWidget(self._opacity_label)
            if group_layout is not None:
                group_layout.addLayout(opacity_row)
            else:
                layout.addLayout(opacity_row)

            # Position options
            pos_row = QHBoxLayout()
            try:
                pos_row.setContentsMargins(0, 2, 0, 2)
                pos_row.setSpacing(6)
            except Exception:
                pass
            pos_row.addWidget(QLabel("Position:"))
            from PyQt5.QtWidgets import QComboBox
            self._pos_combo = QComboBox()
            self._pos_combo.addItems(["Top Left", "Top Right", "Bottom Left", "Bottom Right"])
            # Try to select current popup position if available. Use the best
            # available geometry (live popup, stored popup geometry, or the
            # original geometry captured above) and pick the screen that
            # contains that geometry so multi-monitor setups work correctly.
            try:
                from PyQt5.QtCore import QRect, QPoint

                g = None
                # Prefer the live popup geometry when present
                try:
                    if getattr(self, '_popup_window', None) is not None:
                        try:
                            g = QRect(self._popup_window.geometry())
                        except Exception:
                            g = None
                except Exception:
                    g = None

                # Fallback to stored popup geometry
                if g is None and getattr(self, '_popup_geom', None) is not None:
                    try:
                        g = QRect(self._popup_geom)
                    except Exception:
                        g = None

                # Finally, use orig_geom captured earlier if still available
                if g is None and 'orig_geom' in locals() and orig_geom is not None:
                    try:
                        g = QRect(orig_geom)
                    except Exception:
                        g = None

                if g is not None:
                    # Use the screen that contains the center of g when possible
                    center_point = QPoint(int(g.x() + g.width() / 2), int(g.y() + g.height() / 2))
                    screen_geom = None
                    try:
                        app = QApplication.instance()
                        # Preferred API: screenAt (available on newer Qt versions)
                        if app is not None and hasattr(app, 'screenAt'):
                            try:
                                scr = app.screenAt(center_point)
                                if scr is not None:
                                    screen_geom = scr.availableGeometry()
                            except Exception:
                                screen_geom = None

                        # Fallback: check all screens
                        if screen_geom is None:
                            try:
                                for s in QApplication.screens():
                                    try:
                                        if s.geometry().contains(center_point):
                                            screen_geom = s.availableGeometry()
                                            break
                                    except Exception:
                                        continue
                            except Exception:
                                screen_geom = None

                        # Last resort: primary screen
                        if screen_geom is None:
                            screen_geom = QApplication.primaryScreen().availableGeometry()
                    except Exception:
                        try:
                            screen_geom = QApplication.primaryScreen().availableGeometry()
                        except Exception:
                            screen_geom = None

                    if screen_geom is not None:
                        if g.x() < screen_geom.center().x():
                            # left
                            if g.y() < screen_geom.center().y():
                                self._pos_combo.setCurrentIndex(0)
                            else:
                                self._pos_combo.setCurrentIndex(2)
                        else:
                            if g.y() < screen_geom.center().y():
                                self._pos_combo.setCurrentIndex(1)
                            else:
                                self._pos_combo.setCurrentIndex(3)
            except Exception:
                pass
            pos_row.addWidget(self._pos_combo, 1)
            if group_layout is not None:
                group_layout.addLayout(pos_row)
            else:
                layout.addLayout(pos_row)

            # Live apply: when sliders change, update popup immediately
            def _apply_live():
                try:
                    size = int(self._size_slider.value())
                    try:
                        # Update the pixel label live
                        self._size_value_label.setText(f"{size} px")
                    except Exception:
                        pass
                    pos_idx = int(self._pos_combo.currentIndex())
                    screen = QApplication.primaryScreen().availableGeometry()
                    w = size
                    h = size
                    margin = 24
                    if pos_idx == 0:  # TL
                        x = screen.left() + margin
                        y = screen.top() + margin
                    elif pos_idx == 1:  # TR
                        x = screen.right() - w - margin
                        y = screen.top() + margin
                    elif pos_idx == 2:  # BL
                        x = screen.left() + margin
                        y = screen.bottom() - h - margin
                    else:  # BR
                        x = screen.right() - w - margin
                        y = screen.bottom() - h - margin
                    self._popup_geom = QRect(x, y, w, h)
                    if self._popup_window:
                        try:
                            self._popup_window.setGeometry(self._popup_geom)
                        except Exception:
                            pass
                except Exception:
                    pass

            def _apply_opacity_live():
                try:
                    val = int(self._opacity_slider.value())
                    pct = max(10, min(100, val))
                    self._popup_opacity = pct / 100.0
                    try:
                        self._opacity_label.setText(f"{pct}%")
                    except Exception:
                        pass
                    if self._popup_window:
                        try:
                            self._popup_window.setWindowOpacity(self._popup_opacity)
                        except Exception:
                            pass
                except Exception:
                    pass

            self._size_slider.valueChanged.connect(lambda _: _apply_live())
            # Keep the pixel label in sync as the user drags the slider
            self._size_slider.valueChanged.connect(lambda val: self._size_value_label.setText(f"{int(val)} px"))
            self._pos_combo.currentIndexChanged.connect(lambda _: _apply_live())
            self._opacity_slider.valueChanged.connect(lambda _: _apply_opacity_live())

            # When live changes occur, emit preferences_changed to persist immediately
            try:
                prefs = getattr(self, 'preferences_panel', None)
                if prefs and hasattr(prefs, 'preferences_changed'):
                    self._size_slider.valueChanged.connect(lambda _: self._request_pref_save())
                    self._pos_combo.currentIndexChanged.connect(lambda _: self._request_pref_save())
                    self._opacity_slider.valueChanged.connect(lambda _: self._request_pref_save())
            except Exception:
                pass

            # Buttons: OK simply closes, Cancel restores original geometry
            # Insert the labeled group into the dialog before buttons
            try:
                if group is not None:
                    layout.addWidget(group)
            except Exception:
                pass

            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            layout.addWidget(bb)
            def _on_ok():
                try:
                    # Already applied live; just accept
                    dlg.accept()
                except Exception:
                    dlg.accept()

            def _on_cancel():
                try:
                    # Restore previous geometry if popup exists
                    if orig_geom is not None:
                        self._popup_geom = QRect(orig_geom)
                        if self._popup_window:
                            try:
                                self._popup_window.setGeometry(self._popup_geom)
                            except Exception:
                                pass
                except Exception:
                    pass
                dlg.reject()

            bb.accepted.connect(_on_ok)
            bb.rejected.connect(_on_cancel)
            # Make the settings dialog wider by default and enforce a smaller
            # fixed height to remove unused vertical space between controls.
            try:
                hint = dlg.sizeHint()
                w = max(700, hint.width())
                # Use a smaller fixed height; sliders and controls fit comfortably
                h = max(160, hint.height())
                dlg.resize(int(w), int(h))
                try:
                    dlg.setFixedHeight(int(h))
                except Exception:
                    pass
            except Exception:
                dlg.resize(700, 160)
                try:
                    dlg.setFixedHeight(160)
                except Exception:
                    pass

            dlg.exec_()
        except Exception:
            pass

    def eventFilter(self, obj, event):
        """Capture move/resize events from the popup to keep _popup_geom current."""
        if obj is getattr(self, '_popup_window', None):
            self._popup_controller.event_filter(obj, event)
            return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)

    def _update_euler_view(self):
        """Show Euler angles only while fusion is processing and no calibration runs."""
        try:
            stack = getattr(self, 'euler_stack', None)
            if stack is None:
                return

            show_angles = (bool(getattr(self, '_processing_active', False))
                           and not bool(getattr(self, '_calibrating', False)))
            hold = getattr(self, 'hold_indicator', None)

            if show_angles:
                if hold is not None:
                    try:
                        hold.stop_blinking()
                    except Exception:
                        pass
                stack.setCurrentWidget(self._euler_page)
            else:
                # Show hold page; only blink when calibration is running or when
                # serial is connected but the fusion loop isn't processing data.
                stack.setCurrentWidget(self._hold_page)
                try:
                    should_blink = bool(getattr(self, '_calibrating', False)) or (
                        (getattr(self, '_serial_state', None) == 'connected') and not bool(getattr(self, '_processing_active', False))
                    )
                except Exception:
                    should_blink = bool(getattr(self, '_calibrating', False))

                if hold is not None:
                    try:
                        if should_blink:
                            hold.start_blinking()
                        else:
                            hold.stop_blinking()
                    except Exception:
                        pass
        except Exception:
            pass

    def update_drift_status(self, active):
        """
        Update drift correction status and visualization.

        Args:
            active: Boolean indicating if drift correction is active
        """
        try:
            # Update visualization state
            try:
                if hasattr(self, 'visualization_widget') and self.visualization_widget:
                    self.visualization_widget.update_drift_correction(active)
            except Exception:
                pass

            # Update own drift status label
            if hasattr(self, 'drift_status_label') and self.drift_status_label:
                if active:
                    self.drift_status_label.setText("Drift Correction Active")
                    self.drift_status_label.setProperty("status", "enabled")
                else:
                    self.drift_status_label.setText("Drift Correction Inactive")
                    self.drift_status_label.setProperty("status", "error")
                try:
                    self.drift_status_label.style().polish(self.drift_status_label)
                except Exception:
                    pass

        except Exception:
            pass

    # --- Calibration status compatibility API ---
    def update_calibration_status(self, calibrated: bool):
        """
        Compatibility method called by GUI worker when fusion reports gyro calibration status.
        Updates the calibration indicator label.
        """
        try:
            if hasattr(self, 'calib_status_label') and self.calib_status_label:
                if calibrated:
                    self.calib_status_label.setText("Gyro: Calibrated")
                    self.calib_status_label.setProperty('status', 'enabled')
                else:
                    self.calib_status_label.setText("Gyro: Not calibrated")
                    self.calib_status_label.setProperty('status', 'error')
                try:
                    self.calib_status_label.style().polish(self.calib_status_label)
                except Exception:
                    pass
        except Exception:
            pass

    def update_calibrating_status(self, calibrating: bool):
        """
        Compatibility method called by GUI worker when fusion reports calibration in-progress.
        Updates the calibration indicator label to reflect active calibration.
        """
        try:
            if hasattr(self, 'calib_status_label') and self.calib_status_label:
                if calibrating:
                    self.calib_status_label.setText("Gyro: Calibrating...")
                    self.calib_status_label.setProperty('status', 'warning')
                else:
                    # When calibration stops, leave result unchanged; GUI _handle_status_update
                    # will subsequently call update_calibration_status with the final result.
                    pass
                try:
                    self.calib_status_label.style().polish(self.calib_status_label)
                except Exception:
                    pass
        except Exception:
            pass

        # Calibration blocks live angle output; reflect that in the Euler area
        self._calibrating = bool(calibrating)
        self._update_euler_view()

    def update_serial_connection_status(self, value):
        """
        Compatibility method called by GUI worker to inform about serial connection
        lifecycle changes. Expected values: 'connected', 'stopped', 'disconnected', 'error', etc.
        The hold indicator should blink when serial is connected but no fusion data
        is being processed (i.e. waiting for the device to begin sending samples).
        """
        try:
            # Normalize to string where possible
            state = None
            if isinstance(value, str):
                state = value.lower().strip()
            elif isinstance(value, bool):
                state = 'connected' if value else 'stopped'
            elif value is None:
                state = None
            else:
                try:
                    state = str(value).lower()
                except Exception:
                    state = None

            self._serial_state = state
        except Exception:
            # Defensive fallback
            try:
                self._serial_state = str(value)
            except Exception:
                self._serial_state = None
        finally:
            # Refresh the Euler/hold display based on the new serial state
            try:
                self._update_euler_view()
            except Exception:
                pass


    def update_processing_status(self, value):
        """
        Update UI elements based on whether the fusion worker is actively processing data.

        When processing is inactive the Disengage, Reset and Recalibrate buttons (and
        their shortcut-set buttons) are disabled and visually muted.
        This method is idempotent: repeated calls with the same boolean state are
        ignored to avoid duplicate work and noisy logging.
        """
        try:
            # Normalize incoming value to a boolean 'active'
            active = False
            if isinstance(value, bool):
                active = value
            elif isinstance(value, str):
                v = value.lower().strip()
                if v in ('active', 'true', '1', 'on'):
                    active = True
                elif v in ('inactive', 'false', '0', 'off'):
                    active = False
                else:
                    # Fallback: interpret non-empty strings as True
                    active = bool(v)
            else:
                active = bool(value)

            # If state hasn't changed, do nothing
            if getattr(self, '_processing_active', None) == active:
                self._update_euler_view()
                return

            # Remember state
            self._processing_active = active
            self._update_euler_view()

            # Log state change
            try:
                _ui_log(self, f"[OrientationPanel] update_processing_status called -> active={active}")
            except Exception:
                pass

            widget_names = ('disengage_btn', 'reset_button', 'recal_button')

            for name in widget_names:
                w = getattr(self, name, None)
                if w is None:
                    continue

                try:
                    if not active:
                        # Ensure disabled appearance and behavior
                        w.setEnabled(False)
                        w.setProperty('status', 'disabled')
                        prev_tip = w.toolTip() or ''
                        w.setToolTip((prev_tip + ' (inactive: waiting for data)').strip())
                        w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                        w.setFocusPolicy(Qt.NoFocus)

                        try:
                            from PyQt5.QtWidgets import QGraphicsOpacityEffect
                            eff = QGraphicsOpacityEffect()
                            eff.setOpacity(0.45)
                            w._inactive_opacity_effect = eff
                            w.setGraphicsEffect(eff)
                        except Exception:
                            # If style or widgets not available in tests, ignore
                            pass
                    else:
                        # Active -> restore normal appearance
                        w.setEnabled(True)
                        w.setProperty('status', '')
                        w.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                        w.setFocusPolicy(Qt.StrongFocus)
                        if hasattr(w, '_inactive_opacity_effect'):
                            try:
                                w.setGraphicsEffect(None)
                            except Exception:
                                pass
                            try:
                                del w._inactive_opacity_effect
                            except Exception:
                                pass

                    # Refresh widget style where possible
                    try:
                        w.style().polish(w)
                        w.update()
                    except Exception:
                        pass
                except Exception:
                    # Defensive: if any one widget fails, continue with others
                    pass
        except Exception:
            # Defensive catch-all to avoid crashing the UI
            pass

    def clear_calibration_state(self):
        """
        Clear calibration *status* indicators without mutating user-configured
        drift thresholds or slider state.

        The orientation panel persists drift thresholds via PreferencesManager and
        these represent user choices. Clearing calibration (e.g. because the
        fusion worker lost the device) should not overwrite those values; it
        should only update calibration-related indicators.
        """
        try:
            # Update calibration indicator only; do not change slider values or
            # stored drift thresholds (stored_drift_*). Those are preserved so
            # user tuning is not lost by a calibration/state reset.
            if getattr(self, 'calib_status_label', None):
                try:
                    self.calib_status_label.setText("Gyro: Not calibrated")
                    self.calib_status_label.setProperty('status', 'error')
                    try:
                        self.calib_status_label.style().polish(self.calib_status_label)
                    except Exception:
                        pass
                except Exception:
                    pass

            # Mark internal calibrating flag false so any blinking/animations stop
            try:
                self._calibrating = False
            except Exception:
                pass

            # Do NOT reset drift_angle_* values, sliders, or stored_drift_* here.
            # Those are owned by the user and persisted separately via PreferencesManager.
        except Exception:
            pass

    def update_device_status(self, stationary: bool):
        """
        Update device movement status shown in the panel.

        Args:
            stationary: True if device is stationary, False if moving
        """
        try:
            if not hasattr(self, 'device_status_label') or not self.device_status_label:
                return
            if stationary:
                text = "Device status: stationary"
                self.device_status_label.setProperty('status', 'enabled')
            else:
                text = "Device status: moving"
                self.device_status_label.setProperty('status', 'error')
            self.device_status_label.setText(text)
            try:
                # Ensure stylesheet updates are reflected
                self.device_status_label.style().polish(self.device_status_label)
            except Exception:
                pass
        except Exception:
            pass

    def reset_status(self):
        """Reset any transient textual status displayed in the panel."""
        try:
            if hasattr(self, 'device_status_label') and self.device_status_label:
                self.device_status_label.setText("Device status: Unknown")
        except Exception:
            pass
    
    # --- Drift angle application methods (debounced) ---
    def _apply_drift_angle_yaw(self):
        try:
            if self._pending_drift_yaw_value is not None and self.control_queue:
                if not safe_queue_put(self.control_queue, ('set_center_threshold_yaw', float(self._pending_drift_yaw_value)), timeout=QUEUE_PUT_TIMEOUT):
                    if self.message_callback:
                        self.message_callback("Failed to send yaw drift angle update")
                else:
                    if self.message_callback:
                        self.message_callback(f"Yaw drift angle updated to {self._pending_drift_yaw_value:.1f}°")
                self._pending_drift_yaw_value = None
        except Exception:
            pass

    def _apply_drift_angle_pitch(self):
        try:
            if self._pending_drift_pitch_value is not None and self.control_queue:
                if not safe_queue_put(self.control_queue, ('set_center_threshold_pitch', float(self._pending_drift_pitch_value)), timeout=QUEUE_PUT_TIMEOUT):
                    if self.message_callback:
                        self.message_callback("Failed to send pitch drift angle update")
                else:
                    if self.message_callback:
                        self.message_callback(f"Pitch drift angle updated to {self._pending_drift_pitch_value:.1f}°")
                self._pending_drift_pitch_value = None
        except Exception:
            pass

    def _apply_drift_angle_roll(self):
        try:
            if self._pending_drift_roll_value is not None and self.control_queue:
                if not safe_queue_put(self.control_queue, ('set_center_threshold_roll', float(self._pending_drift_roll_value)), timeout=QUEUE_PUT_TIMEOUT):
                    if self.message_callback:
                        self.message_callback("Failed to send roll drift angle update")
                else:
                    if self.message_callback:
                        self.message_callback(f"Roll drift angle updated to {self._pending_drift_roll_value:.1f}°")
                self._pending_drift_roll_value = None
        except Exception:
            pass

    def set_drift_angle_yaw(self, angle):
        try:
            angle = float(angle)
            angle = max(0.0, min(25.0, angle))
            angle = round(angle * 10.0) / 10.0
            self.drift_angle_yaw_value = angle
            try:
                self.drift_angle_yaw_label.setText(f"{angle:.1f}°")
                self.drift_yaw_slider.setValue(int(angle * 10))
            except Exception:
                pass
            if self.control_queue:
                safe_queue_put(self.control_queue, ('set_center_threshold_yaw', float(angle)), timeout=QUEUE_PUT_TIMEOUT)
        except Exception:
            pass

    def set_drift_angle_pitch(self, angle):
        try:
            angle = float(angle)
            angle = max(0.0, min(25.0, angle))
            angle = round(angle * 10.0) / 10.0
            self.drift_angle_pitch_value = angle
            try:
                self.drift_angle_pitch_label.setText(f"{angle:.1f}°")
                self.drift_pitch_slider.setValue(int(angle * 10))
            except Exception:
                pass
            if self.control_queue:
                safe_queue_put(self.control_queue, ('set_center_threshold_pitch', float(angle)), timeout=QUEUE_PUT_TIMEOUT)
        except Exception:
            pass

    def set_drift_angle_roll(self, angle):
        try:
            angle = float(angle)
            angle = max(0.0, min(25.0, angle))
            angle = round(angle * 10.0) / 10.0
            self.drift_angle_roll_value = angle
            try:
                self.drift_angle_roll_label.setText(f"{angle:.1f}°")
                self.drift_roll_slider.setValue(int(angle * 10))
            except Exception:
                pass
            if self.control_queue:
                safe_queue_put(self.control_queue, ('set_center_threshold_roll', float(angle)), timeout=QUEUE_PUT_TIMEOUT)
        except Exception:
            pass

    def _on_drift_yaw_angle_change(self, value):
        """Handle yaw drift angle slider changes with debouncing."""
        try:
            # Convert slider value (0-250) to float (0.0-25.0)
            v = float(value) / 10.0
        except Exception:
            v = 0.0

        # Quantize to 0.1 and update display immediately
        vq = round(v * 10.0) / 10.0
        self.drift_angle_yaw_value = vq
        try:
            self.drift_angle_yaw_label.setText(f"{vq:.1f}°")
        except Exception:
            pass
        
        # Update visualization widget immediately
        try:
            if hasattr(self, 'visualization_widget') and self.visualization_widget:
                self.visualization_widget.update_drift_angle_yaw(vq)
        except Exception:
            pass
        
        # Update stored value if not disengaged
        try:
            if not (hasattr(self, 'disengage_btn') and self.disengage_btn and self.disengage_btn.isChecked()):
                self.stored_drift_yaw = vq
        except Exception:
            pass

        # Store the value for debounced sending
        self._pending_drift_yaw_value = vq
        
        # Restart debounce timer
        try:
            self._drift_yaw_send_timer.stop()
            from config.config import THRESH_DEBOUNCE_MS
            self._drift_yaw_send_timer.start(THRESH_DEBOUNCE_MS)
        except Exception:
            pass

    def _on_drift_pitch_angle_change(self, value):
        """Handle pitch drift angle slider changes with debouncing."""
        try:
            # Convert slider value (0-250) to float (0.0-25.0)
            v = float(value) / 10.0
        except Exception:
            v = 0.0

        # Quantize to 0.1 and update display immediately
        vq = round(v * 10.0) / 10.0
        self.drift_angle_pitch_value = vq
        try:
            self.drift_angle_pitch_label.setText(f"{vq:.1f}°")
        except Exception:
            pass
        
        # Update visualization widget immediately
        try:
            if hasattr(self, 'visualization_widget') and self.visualization_widget:
                self.visualization_widget.update_drift_angle_pitch(vq)
        except Exception:
            pass
        
        # Update stored value if not disengaged
        try:
            if not (hasattr(self, 'disengage_btn') and self.disengage_btn and self.disengage_btn.isChecked()):
                self.stored_drift_pitch = vq
        except Exception:
            pass

        # Store the value for debounced sending
        self._pending_drift_pitch_value = vq
        
        # Restart debounce timer
        try:
            self._drift_pitch_send_timer.stop()
            from config.config import THRESH_DEBOUNCE_MS
            self._drift_pitch_send_timer.start(THRESH_DEBOUNCE_MS)
        except Exception:
            pass

    def _on_drift_roll_angle_change(self, value):
        """Handle roll drift angle slider changes with debouncing."""
        try:
            # Convert slider value (0-250) to float (0.0-25.0)
            v = float(value) / 10.0
        except Exception:
            v = 0.0

        # Quantize to 0.1 and update display immediately
        vq = round(v * 10.0) / 10.0
        self.drift_angle_roll_value = vq
        try:
            self.drift_angle_roll_label.setText(f"{vq:.1f}°")
        except Exception:
            pass
        
        # Update visualization widget immediately
        try:
            if hasattr(self, 'visualization_widget') and self.visualization_widget:
                self.visualization_widget.update_drift_angle_roll(vq)
        except Exception:
            pass
        
        # Update stored value if not disengaged
        try:
            if not (hasattr(self, 'disengage_btn') and self.disengage_btn and self.disengage_btn.isChecked()):
                self.stored_drift_roll = vq
        except Exception:
            pass

        # Store the value for debounced sending
        self._pending_drift_roll_value = vq
        
        # Restart debounce timer
        try:
            self._drift_roll_send_timer.stop()
            from config.config import THRESH_DEBOUNCE_MS
            self._drift_roll_send_timer.start(THRESH_DEBOUNCE_MS)
        except Exception:
            pass

    def _send_initial_drift_angle(self):
        """Send the initial drift angle values to the fusion worker."""
        try:
            # Prevent double sending of initial values
            if self._initial_drift_sent or not self.control_queue:
                return
            self._initial_drift_sent = True
            if self.control_queue:
                # Send yaw drift angle
                if not safe_queue_put(self.control_queue, ('set_center_threshold_yaw', float(self.drift_angle_yaw_value)), timeout=QUEUE_PUT_TIMEOUT):
                    if self.message_callback:
                        self.message_callback("Failed to send initial yaw drift angle")
                else:
                    if self.message_callback:
                        self.message_callback(f"Initial yaw drift angle set to {self.drift_angle_yaw_value:.1f}°")
                
                # Send pitch drift angle
                if not safe_queue_put(self.control_queue, ('set_center_threshold_pitch', float(self.drift_angle_pitch_value)), timeout=QUEUE_PUT_TIMEOUT):
                    if self.message_callback:
                        self.message_callback("Failed to send initial pitch drift angle")
                else:
                    if self.message_callback:
                        self.message_callback(f"Initial pitch drift angle set to {self.drift_angle_pitch_value:.1f}°")
                
                # Send roll drift angle
                if not safe_queue_put(self.control_queue, ('set_center_threshold_roll', float(self.drift_angle_roll_value)), timeout=QUEUE_PUT_TIMEOUT):
                    if self.message_callback:
                        self.message_callback("Failed to send initial roll drift angle")
                else:
                    if self.message_callback:
                        self.message_callback(f"Initial roll drift angle set to {self.drift_angle_roll_value:.1f}°")
        except Exception:
            pass
    
    def get_prefs(self):
        """
        Get current preferences for persistence.
        Returns a dict compatible with the old CalibrationPanel.get_prefs so
        PreferencesManager and other callers can save the calibration state.
        """
        try:
            prefs = {
                'drift_angle_yaw': getattr(self, 'drift_angle_yaw_value', None),
                'drift_angle_pitch': getattr(self, 'drift_angle_pitch_value', None),
                'drift_angle_roll': getattr(self, 'drift_angle_roll_value', None),
                'reset_shortcut': getattr(self, 'reset_shortcut', 'None'),
                'reset_shortcut_display_name': getattr(self, 'reset_shortcut_display_name', 'None'),
                'disengage_shortcut': getattr(self, 'disengage_shortcut', 'None'),
                'disengage_shortcut_display_name': getattr(self, 'disengage_shortcut_display_name', 'None'),
                'disengage_toggle_mode': getattr(self, 'disengage_toggle_mode', False)
            }

            # Persist popup geometry and opacity so popout restores between runs
            try:
                if isinstance(self._popup_geom, QRect):
                    prefs['popup_x'] = int(self._popup_geom.x())
                    prefs['popup_y'] = int(self._popup_geom.y())
                    prefs['popup_w'] = int(self._popup_geom.width())
                    prefs['popup_h'] = int(self._popup_geom.height())
                elif self._popup_window is not None:
                    try:
                        g = self._popup_window.geometry()
                        prefs['popup_x'] = int(g.x())
                        prefs['popup_y'] = int(g.y())
                        prefs['popup_w'] = int(g.width())
                        prefs['popup_h'] = int(g.height())
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                prefs['popup_opacity'] = float(getattr(self, '_popup_opacity', 1.0))
            except Exception:
                prefs['popup_opacity'] = 1.0
            return prefs
        except Exception:
            return {}

    def set_prefs(self, prefs):
        """
        Apply saved preferences from the preferences manager.
        Supports legacy and new keys used by the original CalibrationPanel.
        """
        if not prefs:
            return
        try:
            # Support new separate keys
            if 'drift_angle_yaw' in prefs and prefs['drift_angle_yaw'] is not None:
                try:
                    self.set_drift_angle_yaw(float(prefs['drift_angle_yaw']))
                except Exception:
                    pass
            if 'drift_angle_pitch' in prefs and prefs['drift_angle_pitch'] is not None:
                try:
                    self.set_drift_angle_pitch(float(prefs['drift_angle_pitch']))
                except Exception:
                    pass
            if 'drift_angle_roll' in prefs and prefs['drift_angle_roll'] is not None:
                try:
                    self.set_drift_angle_roll(float(prefs['drift_angle_roll']))
                except Exception:
                    pass

            # Backwards compatibility: old single key 'drift_angle'
            if ('drift_angle' in prefs and prefs.get('drift_angle') and
                    'drift_angle_yaw' not in prefs and 'drift_angle_pitch' not in prefs and 'drift_angle_roll' not in prefs):
                try:
                    angle = float(prefs.get('drift_angle'))
                    self.set_drift_angle_yaw(angle)
                    self.set_drift_angle_pitch(angle)
                    self.set_drift_angle_roll(angle)
                except Exception:
                    pass

            # Restore reset shortcut
            shortcut = prefs.get('reset_shortcut', 'None')
            if shortcut and shortcut != 'None':
                display_name = prefs.get('reset_shortcut_display_name', shortcut)
                try:
                    self._set_reset_shortcut(shortcut, display_name)
                except Exception:
                    pass

            # Restore disengage shortcut
            disengage_shortcut = prefs.get('disengage_shortcut', 'None')
            if disengage_shortcut and disengage_shortcut != 'None':
                display_name = prefs.get('disengage_shortcut_display_name', disengage_shortcut)
                try:
                    self._set_disengage_shortcut(disengage_shortcut, display_name)
                except Exception:
                    pass

            # Disengage toggle mode
            toggle_mode = prefs.get('disengage_toggle_mode', False)
            if isinstance(toggle_mode, str):
                toggle_mode = toggle_mode.lower() in ('true', '1', 'yes')
            try:
                self.set_disengage_toggle_mode(bool(toggle_mode))
            except Exception:
                pass

            # Restore popup geometry and opacity if present
            try:
                # Opacity might be stored as float or string
                if 'popup_opacity' in prefs and prefs.get('popup_opacity') is not None:
                    try:
                        op = prefs.get('popup_opacity')
                        if isinstance(op, str):
                            op = float(op)
                        else:
                            op = float(op)
                        # clamp between 0.1 and 1.0 to avoid invisible popups
                        op = max(0.1, min(1.0, op))
                        self._popup_opacity = op
                    except Exception:
                        pass

                # Geometry may be stored as four separate numeric keys
                if ('popup_x' in prefs and 'popup_y' in prefs and
                        'popup_w' in prefs and 'popup_h' in prefs):
                    try:
                        x = int(prefs.get('popup_x'))
                        y = int(prefs.get('popup_y'))
                        w = int(prefs.get('popup_w'))
                        h = int(prefs.get('popup_h'))
                        # Basic validation
                        if w <= 0 or h <= 0:
                            raise ValueError('invalid size')
                        self._popup_geom = QRect(x, y, w, h)
                        # If popup already exists, apply immediately
                        if getattr(self, '_popup_window', None):
                            try:
                                self._popup_window.setGeometry(self._popup_geom)
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass

    def _set_reset_shortcut(self, key, display_name):
        """Set the reset orientation shortcut and register with input worker."""
        try:
            self.reset_shortcut = key
            self.reset_shortcut_display_name = display_name if display_name else key
            # Update UI text if button exists
            try:
                # Show the shortcut display name on a second line within the button (smaller font)
                if key and key != 'None' and hasattr(self, 'reset_button') and self.reset_button:
                    try:
                        # Use TwoLineButton API to set main and secondary lines
                        self.reset_button.setParts("Reset Orientation", self.reset_shortcut_display_name)
                    except Exception:
                        try:
                            self.reset_button.setParts("Reset Orientation", str(self.reset_shortcut_display_name))
                        except Exception:
                            pass
                elif hasattr(self, 'reset_button') and self.reset_button:
                    try:
                        self.reset_button.setParts("Reset Orientation", "")
                    except Exception:
                        pass
            except Exception:
                pass

            # Register with input worker
            if getattr(self, 'input_command_queue', None) and key and key != 'None':
                try:
                    self.input_command_queue.put(('set_shortcut', key, self.reset_shortcut_display_name, 'reset_orientation'))
                except Exception:
                    pass
            elif getattr(self, 'input_command_queue', None):
                try:
                    self.input_command_queue.put(('clear_shortcut', 'reset_orientation'))
                except Exception:
                    pass
        except Exception:
            pass

    def _set_disengage_shortcut(self, key, display_name):
        """Set the disengage shortcut and register with input worker."""
        try:
            self.disengage_shortcut = key
            self.disengage_shortcut_display_name = display_name if display_name else key
            try:
                # Update the button to show the shortcut on a second line
                if key and key != 'None' and hasattr(self, 'disengage_btn') and self.disengage_btn:
                    try:
                        self.disengage_btn.setParts("Disengage Drift Correction", self.disengage_shortcut_display_name)
                    except Exception:
                        try:
                            self.disengage_btn.setParts("Disengage Drift Correction", str(self.disengage_shortcut_display_name))
                        except Exception:
                            pass
                elif hasattr(self, 'disengage_btn') and self.disengage_btn:
                    try:
                        self.disengage_btn.setParts("Disengage Drift Correction", "")
                    except Exception:
                        pass
            except Exception:
                pass

            if getattr(self, 'input_command_queue', None) and key and key != 'None':
                try:
                    self.input_command_queue.put(('set_shortcut', key, self.disengage_shortcut_display_name, 'disengage_drift'))
                except Exception:
                    pass
            elif getattr(self, 'input_command_queue', None):
                try:
                    self.input_command_queue.put(('clear_shortcut', 'disengage_drift'))
                except Exception:
                    pass
        except Exception:
            pass

    def set_disengage_toggle_mode(self, toggle_mode):
        """Set whether the disengage button is toggle or hold mode."""
        try:
            self.disengage_toggle_mode = bool(toggle_mode)
            # If switching to hold mode while currently toggled on, turn it off
            if not self.disengage_toggle_mode and getattr(self, 'disengage_toggled_on', False):
                try:
                    self._disengage_off()
                except Exception:
                    pass
        except Exception:
            pass

    def pause_input_response_monitoring(self):
        """Pause the panel's input_response_queue polling if any."""
        try:
            if getattr(self, 'input_response_timer', None):
                self.input_response_timer.stop()
        except Exception:
            pass

    def resume_input_response_monitoring(self):
        """Resume the panel's input_response_queue polling if any."""
        try:
            if getattr(self, 'input_response_timer', None):
                self.input_response_timer.start(50)
        except Exception:
            pass

    def _on_reset_orientation(self):
        """Send reset command to the control queue."""
        if self.control_queue and not safe_queue_put(
                self.control_queue, 'reset_orientation', timeout=QUEUE_PUT_TIMEOUT):
            if self.message_callback:
                self.message_callback("Failed to send reset orientation command")

    def _on_recalibrate(self):
        """Request gyro recalibration from control queue."""
        try:
            sample_count = None
            if hasattr(self, 'preferences_panel') and self.preferences_panel:
                try:
                    sample_count = self.preferences_panel.gyro_bias_cal_samples
                except Exception:
                    sample_count = None
            cmd = ('recalibrate_gyro_bias', sample_count) if sample_count is not None else ('recalibrate_gyro_bias',)
            if not self.control_queue:
                if self.message_callback:
                    self.message_callback("Cannot recalibrate: no fusion control queue")
                return
            if not safe_queue_put(self.control_queue, cmd, timeout=QUEUE_PUT_TIMEOUT):
                if self.message_callback:
                    self.message_callback("Failed to send gyro recalibration command")
        except Exception as e:
            _ui_log(self, f"[OrientationPanel] Error requesting gyro recalibration: {e}")

    def _on_disengage_pressed(self):
        """Handle disengage pressed (toggle or hold)."""
        try:
            if getattr(self, 'disengage_toggle_mode', False):
                if getattr(self, 'disengage_toggled_on', False):
                    self._disengage_off()
                else:
                    self._disengage_on()
            else:
                self._disengage_on()
        except Exception:
            pass

    def _disengage_on(self):
        """Disable drift correction at the worker and update visuals."""
        try:
            self.stored_drift_yaw = getattr(self, 'drift_angle_yaw_value', 0.0)
            self.stored_drift_pitch = getattr(self, 'drift_angle_pitch_value', 0.0)
            self.stored_drift_roll = getattr(self, 'drift_angle_roll_value', 0.0)
            if self.control_queue:
                safe_queue_put(self.control_queue, ('set_threshold', 0.0, 0.0, 0.0), timeout=QUEUE_PUT_TIMEOUT)
            try:
                if hasattr(self, 'disengage_btn') and self.disengage_btn:
                    self.disengage_btn.setParts("🔴 Drift Correction DISENGAGED", "")
                    # Use semantic property so QSS applies the correct styling (dark/light aware)
                    try:
                        self.disengage_btn.setProperty('status', 'error')
                        # Also make text bold via font rather than stylesheet
                        f = self.disengage_btn.font()
                        f.setBold(True)
                        self.disengage_btn.setFont(f)
                        # Refresh style
                        try:
                            self.disengage_btn.style().polish(self.disengage_btn)
                        except Exception:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass
            self.disengage_toggled_on = True
        except Exception:
            pass

    def _disengage_off(self):
        """Restore drift correction thresholds and visuals."""
        try:
            if self.control_queue:
                safe_queue_put(self.control_queue, ('set_threshold', getattr(self, 'stored_drift_yaw', 0.0), getattr(self, 'stored_drift_pitch', 0.0), getattr(self, 'stored_drift_roll', 0.0)), timeout=QUEUE_PUT_TIMEOUT)
            try:
                if hasattr(self, 'disengage_btn') and self.disengage_btn:
                    # Restore default button text: show shortcut on second line if available
                    try:
                        if getattr(self, 'disengage_shortcut_display_name', None):
                            try:
                                self.disengage_btn.setParts("Disengage Drift Correction", self.disengage_shortcut_display_name)
                            except Exception:
                                try:
                                    self.disengage_btn.setParts("Disengage Drift Correction", str(self.disengage_shortcut_display_name))
                                except Exception:
                                    pass
                        else:
                            try:
                                self.disengage_btn.setParts("Disengage Drift Correction", "")
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # Clear semantic status property so QSS reverts to default button styling
                    try:
                        self.disengage_btn.setProperty('status', '')
                        f = self.disengage_btn.font()
                        f.setBold(False)
                        self.disengage_btn.setFont(f)
                        try:
                            self.disengage_btn.style().polish(self.disengage_btn)
                        except Exception:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass
            self.disengage_toggled_on = False
        except Exception:
            pass

    def _on_disengage_released(self):
        """Handle disengage release (hold mode restores)."""
        try:
            if not getattr(self, 'disengage_toggle_mode', False):
                self._disengage_off()
        except Exception:
            pass

    def _process_input_responses(self):
        """Poll input_response_queue for shortcut events and dispatch handlers.

        Expected messages from InputWorker:
            ('input_captured', key, display_name)
            ('shortcut_pressed', key, action)
            ('shortcut_released', key, action)
        """
        try:
            if not getattr(self, 'input_response_queue', None):
                return

            # Drain available responses to avoid backlog
            while True:
                try:
                    resp = self.input_response_queue.get_nowait()
                except Exception:
                    break

                if not resp or not isinstance(resp, (list, tuple)) or len(resp) < 1:
                    continue

                tag = resp[0]
                try:
                    if tag == 'input_captured':
                        # Generally handled by KeyCaptureDialog; ignore here unless no dialog
                        key = resp[1] if len(resp) > 1 else None
                        disp = resp[2] if len(resp) > 2 else None
                        _ui_log(self, f"[OrientationPanel] input_captured: {key} ({disp})")
                    elif tag == 'shortcut_pressed':
                        key = resp[1] if len(resp) > 1 else None
                        action = resp[2] if len(resp) > 2 else None
                        _ui_log(self, f"[OrientationPanel] shortcut_pressed: {key} -> {action}")
                        if action == 'reset_orientation' and hasattr(self, '_on_reset_orientation'):
                            try:
                                self._on_reset_orientation()
                            except Exception:
                                pass
                        elif action == 'disengage_drift' and hasattr(self, '_on_disengage_pressed'):
                            try:
                                self._on_disengage_pressed()
                            except Exception:
                                pass
                    elif tag == 'shortcut_released':
                        key = resp[1] if len(resp) > 1 else None
                        action = resp[2] if len(resp) > 2 else None
                        _ui_log(self, f"[OrientationPanel] shortcut_released: {key} -> {action}")
                        if action == 'disengage_drift' and hasattr(self, '_on_disengage_released'):
                            try:
                                self._on_disengage_released()
                            except Exception:
                                pass
                except Exception as e:
                    _ui_log(self, f"[OrientationPanel] Error handling input response: {e}")
        except Exception:
            pass

    def connect_preferences_panel(self, preferences_panel):
        """
        Connect the PreferencesPanel so preferences can be synced and applied.
        This mirrors the old CalibrationPanel.connect_preferences_panel API so
        TabbedGUIWorker can call it without changes.
        """
        try:
            self.preferences_panel = preferences_panel
            try:
                preferences_panel.orientation_panel = self
            except Exception:
                pass

            # Restore shortcuts from preferences to this panel (if present)
            # Shortcuts are owned by this panel; only push the toggle mode out
            # to the PreferencesPanel checkbox so its UI reflects our state.
            try:
                if hasattr(preferences_panel, 'disengage_toggle_checkbox'):
                    preferences_panel.disengage_toggle_mode = bool(self.disengage_toggle_mode)
                    preferences_panel.disengage_toggle_checkbox.setChecked(
                        bool(self.disengage_toggle_mode))
            except Exception:
                pass

            # Sync axis inversion flags
            try:
                if hasattr(preferences_panel, 'invert_yaw'):
                    self.set_invert_yaw(preferences_panel.invert_yaw)
                if hasattr(preferences_panel, 'invert_pitch'):
                    self.set_invert_pitch(preferences_panel.invert_pitch)
                if hasattr(preferences_panel, 'invert_roll'):
                    self.set_invert_roll(preferences_panel.invert_roll)
            except Exception:
                pass

        except Exception:
            pass

        # End connect_preferences_panel

    def _request_pref_save(self):
        """Request a debounced preferences save via the connected PreferencesPanel.

        This will call `PreferencesPanel._trigger_preference_save()` when
        available (debounced), otherwise fall back to emitting
        `preferences_changed` immediately.
        """
        try:
            prefs = getattr(self, 'preferences_panel', None)
            if prefs:
                if hasattr(prefs, '_trigger_preference_save'):
                    try:
                        prefs._trigger_preference_save()
                        return
                    except Exception:
                        pass
                if hasattr(prefs, 'preferences_changed'):
                    try:
                        prefs.preferences_changed.emit()
                        return
                    except Exception:
                        pass
        except Exception:
            pass
