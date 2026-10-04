"""Base classes for PyQt panels to maintain consistency."""

from PyQt5.QtWidgets import QGroupBox, QFrame, QWidget
from PyQt5.QtCore import pyqtSignal, QObject

# Shared GUI layout/style constants used by panels for consistent visuals
# Horizontal padding / vertical padding order: (left, top, right, bottom)
CONTENT_MARGINS = (4, 6, 4, 6)
DEFAULT_SPACING = 6
DIALOG_CONTENT_MARGIN = 6
BUTTON_EXTRA_HEIGHT = 6
BUTTON_MIN_HEIGHT = 40
LINE_THICKNESS = 1
INACTIVE_OPACITY = 0.45

from abc import ABC, abstractmethod


def ui_log(owner, msg: str):
    """Log through a panel callback when available, otherwise use stdlib logging."""
    try:
        cb = getattr(owner, 'message_callback', None)
        if callable(cb):
            try:
                cb(msg)
                return
            except Exception:
                pass

        owner_panel = getattr(owner, 'owner_panel', None)
        panel_cb = getattr(owner_panel, 'message_callback', None) if owner_panel else None
        if callable(panel_cb):
            try:
                panel_cb(msg)
                return
            except Exception:
                pass

        import logging
        logging.info(msg)
    except Exception:
        try:
            import logging
            logging.debug('Failed to deliver UI log', exc_info=True)
        except Exception:
            pass


class BasePanelQt(QGroupBox):
    """Base class for all PyQt panels (equivalent to ttk.LabelFrame)."""
    
    # Common signals for all panels
    message_signal = pyqtSignal(str)  # For logging messages
    
    def __init__(self, parent, title="", **kwargs):
        super().__init__(title, parent)
        self.message_callback = kwargs.get('message_callback', None)
        self.setup_ui()
        
    def setup_ui(self):
        """Setup the panel UI (override in subclasses)."""
        pass
        
    def get_prefs(self):
        """Get panel preferences for saving (override in subclasses)."""
        return {}
        
    def set_prefs(self, prefs):
        """Apply saved preferences (override in subclasses)."""
        pass
        
    def log_message(self, msg):
        """Helper to log messages via callback or signal."""
        if self.message_callback:
            self.message_callback(msg)
        else:
            self.message_signal.emit(msg)