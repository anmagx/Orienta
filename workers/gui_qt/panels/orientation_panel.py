"""
PyQt5 Orientation Panel for orienta GUI.

Display-only panel showing Euler angles (Yaw, Pitch, Roll).
No controls - purely for data visualization.
"""
from PyQt5.QtWidgets import (QGroupBox, QVBoxLayout, QHBoxLayout, QGridLayout, 
                             QLabel, QSizePolicy)
from PyQt5.QtCore import Qt


class OrientationPanelQt(QGroupBox):
    """PyQt5 panel for orientation display."""
    
    def __init__(self, parent=None, control_queue=None, message_callback=None, padding=6):
        """
        Initialize the Orientation Panel.
        
        Args:
            parent: Parent PyQt5 widget
            control_queue: Not used (display-only panel)
            message_callback: Not used (display-only panel) 
            padding: Padding for the frame (default: 6)
        """
        super().__init__("Orientation", parent)
        
        # Euler angle display labels
        self.yaw_value_label = None
        self.pitch_value_label = None
        self.roll_value_label = None
        
        self._build_ui()
    
    def _build_ui(self):
        """Build the orientation panel UI."""
        # Main layout - single column for data displays only
        main_layout = QVBoxLayout()
        # Add modest vertical padding inside the panel to match other panels
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(4, 6, 4, 6)
        self.setLayout(main_layout)
        
        # Allow panel to expand vertically to fill available space when appropriate
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        # Build display components
        self._build_euler_displays(main_layout)

    def _build_euler_displays(self, parent_layout):
        """Build Euler angle (Yaw, Pitch, Roll) display row."""
        # Create grid layout for euler angles
        euler_grid = QGridLayout()
        euler_grid.setContentsMargins(6, 4, 6, 4)  # Add horizontal + vertical padding
        
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
            self.yaw_value_label.setText(f"{float(yaw):.1f}")
            self.pitch_value_label.setText(f"{float(pitch):.1f}")
            self.roll_value_label.setText(f"{float(roll):.1f}")
            
            # Update calibration panel visualization if connected
            if hasattr(self, 'calibration_panel') and self.calibration_panel:
                self.calibration_panel.update_orientation(pitch, yaw, roll)
        except Exception:
            pass

    def update_drift_status(self, active):
        """
        Update drift correction status in calibration panel.
        
        Args:
            active: Boolean indicating if drift correction is active
        """
        try:
            # Update calibration panel visualization if connected
            if hasattr(self, 'calibration_panel') and self.calibration_panel:
                self.calibration_panel.update_drift_status(active)
        except Exception:
            pass
    

    
    def get_prefs(self):
        """
        Get current preferences for persistence.
        
        Returns:
            dict: Dictionary with empty preferences (orientation panel is display-only)
        """
        return {}
    
    def set_prefs(self, prefs):
        """
        Apply saved preferences.
        
        Args:
            prefs: Dictionary with optional preference keys (orientation panel is display-only)
        """
        # Orientation panel is display-only, no preferences to restore
        pass
    
    def connect_calibration_panel(self, calibration_panel):
        """
        Connect to the calibration panel for visualization updates.
        
        Args:
            calibration_panel: CalibrationPanelQt instance with visualization widget
        """
        # Store reference to calibration panel
        self.calibration_panel = calibration_panel