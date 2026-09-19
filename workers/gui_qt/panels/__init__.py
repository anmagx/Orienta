"""
PyQt panels package for orienta.

Contains PyQt implementations of GUI panels with same interfaces as tkinter versions.
"""

# Panel imports for the GUI
from .about_panel import AboutPanel
# Calibration panel removed; orientation_panel consolidates calibration UI and logic
from .connection_panel import ConnectionPanelQt
from .diagnostics_panel import DiagnosticsPanelQt
from .hold_panel import HoldPanelQt
from .message_panel import MessagePanelQt
from .orientation_panel import OrientationPanelQt
from .preferences_panel import PreferencesPanel
__all__ = [
    'AboutPanel',
    'ConnectionPanelQt',
    'DiagnosticsPanelQt',
    'HoldPanelQt',
    'MessagePanelQt',
    'OrientationPanelQt',
    'PreferencesPanel'
]