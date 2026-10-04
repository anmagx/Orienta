"""
PyQt5 GUI Worker with Tabbed Layout for orienta.

This is a slimmed-down tabbed interface that organizes panels into focused views:
- Orientation Tracking: Serial, Message, Calibration, Orientation, Network panels

The Message Panel can be collapsed/expanded to save space when not needed.
"""

import sys
import queue
import threading
import time
import os
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QPushButton, QFrame, QSplitter, QSizePolicy
)
from PyQt5.QtCore import QTimer, pyqtSignal, QObject, Qt
from PyQt5.QtGui import QIcon

from src.workers.gui_qt.panels.connection_panel import ConnectionPanelQt
from src.workers.gui_qt.panels.message_panel import MessagePanelQt
from src.workers.gui_qt.panels.orientation_panel import OrientationPanelQt
# CalibrationPanelQt removed; orientation_panel now hosts calibration UI/logic
from src.workers.gui_qt.panels.preferences_panel import PreferencesPanel
from src.workers.gui_qt.panels.about_panel import AboutPanel
from src.workers.gui_qt.panels.orientation_panel import HoldPanelQt

from src.managers.preferences_manager import PreferencesManager
from src.workers.gui_qt.helpers.icon_helper import set_window_icon
from src.managers.theme_manager import ThemeManager

from src.config.config import (
    GUI_UPDATE_INTERVAL_MS, WORKER_QUEUE_CHECK_INTERVAL_MS,
    APP_NAME, APP_VERSION
)
from src.util.log_utils import log_info, log_warning, log_error


class TabbedGUISignals(QObject):
    """Signals for the tabbed GUI worker."""
    status_update = pyqtSignal(str, str)  # section, message
    orientation_update = pyqtSignal(float, float, float)  # roll, pitch, yaw
    drift_status_update = pyqtSignal(str)  # status
    processing_changed = pyqtSignal(bool)  # fusion processing active/inactive


class TabbedGUIWorker(QMainWindow):
    """PyQt5 GUI worker with tabbed layout for orienta."""

    def __init__(self, serial_control_queue, fusion_control_queue,
                 udp_control_queue, status_queue, ui_status_queue, message_queue,
                 serial_display_queue=None, euler_display_queue=None,
                 log_queue=None, stop_event=None, on_stop_callback=None,
                 input_command_queue=None, input_response_queue=None):
        """
        Initialize the tabbed GUI worker.
        
        Args:
            serial_control_queue: Queue for serial worker commands
            fusion_control_queue: Queue for fusion worker commands  
            udp_control_queue: Queue for UDP worker commands
            status_queue: Queue for receiving status updates
            ui_status_queue: Queue for receiving UI-specific status updates
            message_queue: Queue for receiving messages
            serial_display_queue: Queue for raw serial data display
            euler_display_queue: Queue for orientation angles
            log_queue: Queue for log messages
            stop_event: Threading event for shutdown coordination
            on_stop_callback: Callback when GUI is closed
            input_command_queue: Queue for sending commands to input worker
            input_response_queue: Queue for receiving responses from input worker
        """
        super().__init__()
        
        # Store queues and state
        self.serial_control_queue = serial_control_queue
        self.fusion_control_queue = fusion_control_queue
        self.udp_control_queue = udp_control_queue
        self.status_queue = status_queue
        self.ui_status_queue = ui_status_queue
        self.message_queue = message_queue
        self.serial_display_queue = serial_display_queue
        self.euler_display_queue = euler_display_queue
        self.log_queue = log_queue
        self.stop_event = stop_event
        self.on_stop_callback = on_stop_callback
        
        # Input worker queues
        self.input_command_queue = input_command_queue
        self.input_response_queue = input_response_queue
        
        # Initialize managers
        self.preferences_manager = PreferencesManager()
        self.theme_manager = ThemeManager(QApplication.instance())
        
        # Initialize signals
        self.signals = TabbedGUISignals()
        self._connect_signals()
        
        # Setup UI
        self.setup_ui()
        self.setup_timers()
        
        # Auto-resize window to fit content perfectly (after UI is built)
        QTimer.singleShot(0, self._finalize_window_size)
        
        self.load_preferences()
    
    def setup_ui(self):
        """Setup the main UI with tabbed layout."""
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        
        # Set window icon
        try:
            set_window_icon(self)
        except Exception as e:
            log_warning(self.log_queue, 'GUI', f"Could not set window icon: {e}")
        
        # Central widget with tab layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(8, 8, 8, 8)
        
        # Use a single-pane layout (tabbed layout removed)
        # The orientation panel is the main content; other panels are exposed via dialogs
        main_layout.addStretch(0)

        # Create and add the orientation widget directly
        orientation_widget = self.create_orientation_tab()
        if orientation_widget is not None:
            main_layout.addWidget(orientation_widget)

        # Create shared panels (no tabs): messages, preferences, about
        self.create_messages_tab()
        self.create_preferences_tab()
        self.create_about_tab()
        
        # Status bar has been moved into the Connection panel at the bottom of the Orientation tab
        # (No global status bar needed here.)
    
    def create_orientation_tab(self):
        """Create the Orientation Tracking widget (replaces the old tab).
        Returns the created widget so the caller can add it to the main layout.
        """
        orientation_widget = QWidget()
        layout = QVBoxLayout(orientation_widget)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)
        
        # Orientation Panel (main focus)
        self.orientation_panel = OrientationPanelQt(
            orientation_widget,
            self.fusion_control_queue,
            self._log_message,
            padding=6
        )
        layout.addWidget(self.orientation_panel)

        # Assign input queues to orientation panel so it can manage shortcuts/capture
        try:
            self.orientation_panel.input_command_queue = self.input_command_queue
            self.orientation_panel.input_response_queue = self.input_response_queue
            # Connect processing_changed signal so panels react via Qt signals
            try:
                self.signals.processing_changed.connect(self.orientation_panel.update_processing_status)
            except Exception:
                pass
            # OrientationPanelQt is the single owner of orientation controls.
        except Exception:
            self.orientation_panel = None

        # Connection panel moved to the bottom of the widget for easier access
        self.connection_panel = ConnectionPanelQt(
            orientation_widget,
            self.serial_control_queue,
            self.udp_control_queue,
            self._log_message,
            padding=6,
            on_serial_stop=None
        )
        layout.addWidget(self.connection_panel)

        return orientation_widget
    
    def create_messages_tab(self):
        """Create the MessagePanel instance for logging and serial monitor.

        The Messages UI is no longer shown as a dedicated tab; instead a
        MessagePanel instance is created here for reuse by the Monitor / Logs
        dialog (and by any other panel that expects self.message_panel).
        """
        try:
            # Create a shared MessagePanel instance but do not add it as a tab.
            # Parent is None so it can be reparented into dialogs as needed.
            self.message_panel = MessagePanelQt(
                None,
                serial_height=12,
                message_height=12,
                max_serial_lines=500,
                max_message_lines=200,
                padding=6
            )
            log_info(self.log_queue, 'GUI', "MessagePanel created (not added as tab)")
        except Exception as e:
            log_error(self.log_queue, 'GUI', f"Failed to create MessagePanel: {e}")
            self.message_panel = None
    
    def create_preferences_tab(self):
        """Create the Preferences panel instance (no tab). The Preferences UI
        is exposed via the Orientation panel's Preferences button. A single
        shared PreferencesPanel instance is created here and wired to the
        calibration panel and theme/save handlers.
        """
        # Preferences Panel (created without adding to the tab widget)
        self.preferences_panel = PreferencesPanel(
            None,
            self.preferences_manager,
            self.input_command_queue,
            self.input_response_queue,
            self.fusion_control_queue,
            udp_control_queue=self.udp_control_queue
        )

        # Connect preferences panel to the orientation panel for shortcuts
        try:
            self.preferences_panel.connect_orientation_panel(self.orientation_panel)
        except Exception:
            pass

        # Connect calibration panel to preferences panel for sample counts
        try:
            self.orientation_panel.connect_preferences_panel(self.preferences_panel)
        except Exception:
            pass

        # Connect theme change signal
        try:
            self.preferences_panel.theme_changed.connect(self._apply_theme)
        except Exception:
            pass
        try:
            self.preferences_panel.preferences_changed.connect(self.save_preferences)
        except Exception:
            pass

        # Do not add a Preferences tab anymore; preferences are shown via dialog
    
    def _apply_theme(self, theme_name):
        """Apply the selected theme to the application."""
        try:
            # Prevent duplicate theme application during startup
            if hasattr(self, '_theme_applied') and self._theme_applied == theme_name:
                return
            self._theme_applied = theme_name
            
            self.theme_manager.load_theme(theme_name)
            log_info(self.log_queue, 'GUI', f"Applied theme: {theme_name}")
        except Exception as e:
            log_error(self.log_queue, 'GUI', f"Error applying theme {theme_name}: {e}")
    
    def create_about_tab(self):
        """Create the shared AboutPanel instance (no tab). The About UI is shown via dialogs."""
        try:
            self.about_panel = AboutPanel()
        except Exception:
            self.about_panel = None
    
    def _connect_signals(self):
        """Connect internal signals to update methods."""
        self.signals.status_update.connect(self._update_status_bar)
        self.signals.orientation_update.connect(self._update_orientation)
        self.signals.drift_status_update.connect(self._update_drift_status)
    
    def setup_timers(self):
        """Setup update timers."""
        # Main update timer
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.process_queues)
        self.update_timer.start(WORKER_QUEUE_CHECK_INTERVAL_MS)
        
        # Periodic GUI updates
        self.gui_timer = QTimer()
        self.gui_timer.timeout.connect(self.update_gui_elements)
        self.gui_timer.start(GUI_UPDATE_INTERVAL_MS)
    
    def process_queues(self):
        """Process incoming queue messages."""
        try:
            # Process euler display queue for real-time orientation updates
            if self.euler_display_queue:
                euler_count = 0
                latest_euler = None
                # Drain queue to get most recent data for real-time performance
                while euler_count < 100 and not self.euler_display_queue.empty():
                    try:
                        euler_data = self.euler_display_queue.get_nowait()
                        if isinstance(euler_data, (list, tuple)) and len(euler_data) >= 3:
                            latest_euler = euler_data
                        euler_count += 1
                    except:
                        break
                
                # Update display with most recent data only
                if latest_euler:
                    if len(latest_euler) >= 3:
                        # Data format from fusion worker: [yaw, pitch, roll]
                        yaw, pitch, roll = latest_euler[0], latest_euler[1], latest_euler[2]
                        
                        # Update orientation display immediately for real-time response
                        if hasattr(self.orientation_panel, 'update_euler'):
                            self.orientation_panel.update_euler(yaw, pitch, roll)
                        
            
            # Process status updates (check if queue exists and not None)
            if self.status_queue:
                status_count = 0
                while status_count < 20 and not self.status_queue.empty():
                    try:
                        item = self.status_queue.get_nowait()
                        if isinstance(item, tuple) and len(item) >= 2:
                            # Handle tuple format from fusion worker: ('status_type', value)
                            status_type, value = item[0], item[1]
                            self._handle_status_update(status_type, value)
                        elif isinstance(item, dict):
                            if 'section' in item and 'message' in item:
                                self.signals.status_update.emit(item['section'], item['message'])
                            elif 'orientation' in item:
                                # Orientation update from fusion worker
                                euler = item['orientation']
                                if len(euler) >= 3:
                                    # Data format: [yaw, pitch, roll] - emit in correct order
                                    yaw, pitch, roll = euler[0], euler[1], euler[2]
                                    self.signals.orientation_update.emit(roll, pitch, yaw)
                            elif 'drift_status' in item:
                                self.signals.drift_status_update.emit(item['drift_status'])
                        elif isinstance(item, str):
                            # Simple string status
                            self.signals.status_update.emit('general', item)
                        status_count += 1
                    except:
                        break
                        
            # Process UI status updates (dedicated queue for UI state changes)
            if self.ui_status_queue:
                ui_status_count = 0
                while ui_status_count < 20 and not self.ui_status_queue.empty():
                    try:
                        item = self.ui_status_queue.get_nowait()
                        if isinstance(item, tuple) and len(item) >= 2:
                            # Handle tuple format: ('status_type', value)
                            status_type, value = item[0], item[1]
                            self._handle_ui_status_update(status_type, value)
                        ui_status_count += 1
                    except:
                        break
        except Exception as e:
            # Silently handle queue processing errors to avoid spam
            pass
        
        # Process message queue
        if self.message_queue:
            msg_count = 0
            while msg_count < 30 and not self.message_queue.empty():
                try:
                    msg = self.message_queue.get_nowait()
                    if isinstance(msg, str) and hasattr(self.message_panel, 'append_message'):
                        self.message_panel.append_message(msg)
                    msg_count += 1
                except:
                    break
            
            # Update displays if we processed any messages
            if msg_count > 0 and hasattr(self.message_panel, 'update_displays'):
                self.message_panel.update_displays()
    
    def _update_status_bar(self, section: str, message: str):
        """Update the status bar with new information."""
        # For QStatusBar, we'll show the most recent message
        # You could extend this to show multiple sections if needed
        log_info(self.log_queue, 'GUI', f"[{section}] {message}")
    
    def _update_orientation(self, roll: float, pitch: float, yaw: float):
        """Update orientation display."""
        if hasattr(self.orientation_panel, 'update_euler'):
            self.orientation_panel.update_euler(yaw, pitch, roll)
        
    def _update_drift_status(self, status):
        """Update drift status display."""
        if hasattr(self.orientation_panel, 'update_drift_status'):
            # Convert string to boolean for drift correction status
            if isinstance(status, bool):
                active = status
            else:
                # Handle string status from older code paths
                active = 'active' in str(status).lower() or 'true' in str(status).lower()
            self.orientation_panel.update_drift_status(active)
    
    def _safe_panel_call(self, panel_name: str, method_name: str, *args):
        """Call a panel method when it exists, logging failures without duplicating dispatch code."""
        panel = getattr(self, panel_name, None)
        method = getattr(panel, method_name, None) if panel is not None else None
        if not callable(method):
            return
        try:
            method(*args)
        except Exception as e:
            try:
                log_error(self.log_queue, 'GUI', f"{panel_name}.{method_name} raised: {e}")
            except Exception:
                pass

    def _clear_orientation_indicators(self):
        """Clear stateful orientation indicators after processing or serial stops."""
        self._safe_panel_call('orientation_panel', 'update_drift_status', False)
        self._safe_panel_call('orientation_panel', 'update_device_status', False)

    def _route_status_update(self, status_type: str, value, *, ui_status: bool = False):
        """Route worker status updates to panels through one dispatch table."""
        if status_type == 'processing':
            is_active = (value == 'active')
            self._safe_panel_call('connection_panel', 'update_fusion_status', is_active)
            self._safe_panel_call('orientation_panel', 'update_processing_status', value)
            try:
                self.signals.processing_changed.emit(is_active)
            except Exception:
                pass
            if ui_status and not is_active:
                self._clear_orientation_indicators()
        elif status_type == 'serial_connection':
            self._safe_panel_call('connection_panel', 'update_connection_status', value)
            if ui_status:
                self._safe_panel_call('orientation_panel', 'update_serial_connection_status', value)
                # Only clear calibration on unexpected disconnect or error. Preserve
                # user-configured drift thresholds when the serial port is stopped
                # intentionally.
                if value in ['disconnected', 'error']:
                    self._safe_panel_call('orientation_panel', 'clear_calibration_state')
                # Regardless of stop reason, mark processing inactive and clear UI indicators
                self._safe_panel_call('orientation_panel', 'update_processing_status', 'inactive')
                self._clear_orientation_indicators()
                self._safe_panel_call('connection_panel', 'update_message_rate', 0.0)
        elif status_type == 'serial_data':
            if value:
                self._safe_panel_call('connection_panel', 'update_data_activity')
        elif status_type == 'gyro_calibrated':
            self._safe_panel_call('orientation_panel', 'update_calibration_status', bool(value))
        elif status_type == 'gyro_calibrating':
            self._safe_panel_call('orientation_panel', 'update_calibrating_status', bool(value))
        elif status_type == 'drift_correction':
            self._safe_panel_call('orientation_panel', 'update_drift_status', bool(value))
        elif status_type == 'msg_rate':
            self._safe_panel_call('connection_panel', 'update_message_rate', float(value))
        elif status_type == 'send_rate':
            self._safe_panel_call('connection_panel', 'update_send_rate', float(value))
        elif status_type == 'stationary':
            self._safe_panel_call('orientation_panel', 'update_device_status', bool(value))

    def _handle_status_update(self, status_type: str, value):
        """Handle worker status updates."""
        self._route_status_update(status_type, value, ui_status=False)

    def _handle_ui_status_update(self, status_type: str, value):
        """Handle UI-specific worker status updates."""
        self._route_status_update(status_type, value, ui_status=True)

    def update_gui_elements(self):
        """Periodic GUI updates for display queues only (non-real-time data)."""
        try:
            # Process serial display queue for message panel
            if self.serial_display_queue:
                serial_count = 0
                while serial_count < 30 and not self.serial_display_queue.empty():
                    try:
                        serial_data = self.serial_display_queue.get_nowait()
                        if hasattr(self.message_panel, 'append_serial'):
                            self.message_panel.append_serial(str(serial_data))
                        serial_count += 1
                    except:
                        break
                
                # Update displays if we processed any serial data
                if serial_count > 0 and hasattr(self.message_panel, 'update_displays'):
                    self.message_panel.update_displays()
            
            # Note: Euler/orientation updates moved to process_queues() for real-time performance
                        
        except Exception as e:
            # Silently handle display queue errors to avoid spam
            pass
    
    def _log_message(self, message: str):
        """Log a message to the message panel."""
        if hasattr(self.message_panel, 'append_message'):
            self.message_panel.append_message(message)
            # Update displays immediately for direct log calls
            if hasattr(self.message_panel, 'update_displays'):
                self.message_panel.update_displays()
    
    def _finalize_window_size(self):
        """Set initial window size to the central widget's sizeHint (small),
        then apply that as the minimum size so it opens compact and grows only as needed.
        """
        # Ensure layouts are up to date
        self.adjustSize()

        # Prefer the minimumSizeHint (smaller, required minimum for content)
        hint = self.centralWidget().minimumSizeHint()
        if hint.isEmpty():
            hint = self.centralWidget().sizeHint()

        # Small decoration padding to account for window chrome (kept minimal)
        target_w = max(700, hint.width() + 8)  # Increased minimum from 200 to 500
        target_h = max(240, hint.height() + 20)

        # Constrain initial width to avoid extremely wide default
        max_width = 600
        target_w = min(target_w, max_width)

        # Resize to the target and lock the window to this fixed size to prevent resizing
        self.resize(target_w, target_h)
        # Prevent user resizing: enforce fixed size equal to the chosen target
        try:
            self.setFixedSize(target_w, target_h)
        except Exception:
            # Fall back to setting min/max if setFixedSize fails in some environments
            try:
                self.setMinimumSize(target_w, target_h)
                self.setMaximumSize(target_w, target_h)
            except Exception:
                pass
    
    def _enable_resizing(self):
        """Remove maximum size constraint to allow user resizing while preserving minimum size."""
        # Remove maximum size constraint but keep minimum size
        self.setMaximumSize(16777215, 16777215)  # Qt's default maximum size
    
    def load_preferences(self):
        """Load saved preferences for all panels."""
        try:
            prefs = self.preferences_manager.load()
            log_info(self.log_queue, 'GUI', f"load_preferences: sections={list(prefs.keys())}")
            
            # Handle both dict and string formats for preferences
            if isinstance(prefs, str):
                log_warning(self.log_queue, 'GUI', "Preferences returned as string, skipping load")
                return
            
            if not isinstance(prefs, dict):
                log_warning(self.log_queue, 'GUI', f"Unexpected preferences format: {type(prefs)}")
                return
            
            # Apply preferences to each panel
            if hasattr(self.connection_panel, 'set_prefs') and ('serial' in prefs or 'network' in prefs):
                self.connection_panel.set_prefs(prefs)
            
            if hasattr(self.orientation_panel, 'set_prefs') and 'orientation' in prefs:
                log_info(self.log_queue, 'GUI', "Applying orientation prefs")
                self.orientation_panel.set_prefs(prefs['orientation'])
            
            # Load preferences for the preferences panel itself
            if hasattr(self.preferences_panel, 'load_preferences'):
                self.preferences_panel.load_preferences()
            
            # Apply theme preference
            theme_name = self.preferences_manager.get_theme()
            self._apply_theme(theme_name)
            
            
            log_info(self.log_queue, 'GUI', "Preferences loaded")
            
        except Exception as e:
            log_error(self.log_queue, 'GUI', f"Error loading preferences: {e}")
    
    def closeEvent(self, event):
        """Handle window close event."""
        log_info(self.log_queue, 'GUI', "Close event received")
        
        # Stop running timers to prevent callbacks into deleted widgets
        for tname in ('update_timer', 'gui_timer', 'process_timer'):
            try:
                timer = getattr(self, tname, None)
                if timer:
                    timer.stop()
            except Exception:
                pass

        # Cleanup calibration panel resources (threads) before saving preferences
        if hasattr(self.orientation_panel, 'cleanup'):
            try:
                self.orientation_panel.cleanup()
            except Exception:
                pass

        # Save preferences before closing (do this while panels still exist)
        try:
            self.save_preferences()
        except Exception:
            pass

        # Remove cross-panel references to avoid accessing deleted C++ wrappers
        try:
            if hasattr(self, 'preferences_panel') and hasattr(self.preferences_panel, 'orientation_panel'):
                try:
                    self.preferences_panel.orientation_panel = None
                except Exception:
                    pass
            # Finally drop our own reference
            try:
                self.orientation_panel = None
            except Exception:
                pass
        except Exception:
            pass
        
        # Give threads time to cleanup
        QApplication.processEvents()
        
        # Call stop callback if provided
        try:
            if callable(self.on_stop_callback):
                self.on_stop_callback()
        except Exception:
            pass
        
        # Set stop event
        if self.stop_event:
            self.stop_event.set()
        
        event.accept()
    
    def save_preferences(self):
        """Save current preferences from all panels."""
        try:
            # Collect preferences from all panels
            prefs = {}
            
            if hasattr(self.connection_panel, 'get_prefs'):
                prefs.update(self.connection_panel.get_prefs())
            
            if hasattr(self.orientation_panel, 'get_prefs'):
                prefs['orientation'] = self.orientation_panel.get_prefs()
            
            if hasattr(self.preferences_panel, 'get_tuning_preferences'):
                tuning_prefs = self.preferences_panel.get_tuning_preferences()
                prefs.setdefault('orientation', {}).update(tuning_prefs)

            if hasattr(self.preferences_panel, 'get_send_rate_preferences'):
                send_rate_prefs = self.preferences_panel.get_send_rate_preferences()
                prefs.setdefault('network', {}).update(send_rate_prefs)
            
            # Save GUI state
            prefs['gui'] = {
                'selected_tab': '0',  # No tabs: default to 0 for compatibility
                'theme': self.theme_manager.get_current_theme()
            }
            
            # Save to preferences
            if not self.preferences_manager.save(prefs):
                log_error(self.log_queue, 'GUI', "Failed to save preferences")
                return
            
            log_info(self.log_queue, 'GUI', "Preferences saved")
            
        except Exception as e:
            log_error(self.log_queue, 'GUI', f"Error saving preferences: {e}")


def start_gui_worker(serial_control_queue, fusion_control_queue,
                     udp_control_queue, status_queue, ui_status_queue, message_queue,
                     serial_display_queue=None, euler_display_queue=None,
                 log_queue=None, stop_event=None, on_stop_callback=None,
                 input_command_queue=None, input_response_queue=None):
    """
    Start the PyQt5 GUI worker with tabbed interface.
    
    Args:
        serial_control_queue: Queue for serial worker commands
        fusion_control_queue: Queue for fusion worker commands  
        udp_control_queue: Queue for UDP worker commands
        status_queue: Queue for receiving status updates
        ui_status_queue: Queue for receiving UI-specific status updates
        message_queue: Queue for receiving messages
        serial_display_queue: Queue for raw serial data display
        euler_display_queue: Queue for orientation angles
        log_queue: Queue for log messages
        stop_event: Threading event for shutdown coordination
        on_stop_callback: Callback when GUI is closed
        input_command_queue: Queue for sending commands to input worker
        input_response_queue: Queue for receiving responses from input worker
    """
    # Create or get QApplication instance
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    # Set application icon
    try:
        icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'img', 'icon.ico'))
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            if not app_icon.isNull():
                app.setWindowIcon(app_icon)
                log_info(log_queue, 'GUI', f"Application icon set: {icon_path}")
            else:
                log_warning(log_queue, 'GUI', f"Failed to load icon: {icon_path}")
        else:
            log_warning(log_queue, 'GUI', "No icon file found")
            
    except Exception as e:
        log_error(log_queue, 'GUI', f"Could not set application icon: {e}")
    
    # Windows-specific taskbar icon handling
    try:
        import platform
        if platform.system() == "Windows":
            import ctypes
            # Set the app ID for proper taskbar grouping
            app_id = f"orienta.{APP_NAME}.{APP_VERSION}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
            log_info(log_queue, 'GUI', f"Windows AppUserModelID set: {app_id}")
    except Exception as e:
        log_error(log_queue, 'GUI', f"Could not set Windows app ID: {e}")
    
    # Create main window
    main_window = TabbedGUIWorker(
        serial_control_queue=serial_control_queue,
        fusion_control_queue=fusion_control_queue,
        udp_control_queue=udp_control_queue,
        status_queue=status_queue,
        ui_status_queue=ui_status_queue,
        message_queue=message_queue,
        serial_display_queue=serial_display_queue,
        euler_display_queue=euler_display_queue,
        log_queue=log_queue,
        stop_event=stop_event,
        on_stop_callback=on_stop_callback,
        input_command_queue=input_command_queue,
        input_response_queue=input_response_queue
    )
    
    # Show window
    main_window.show()
    
    log_info(log_queue, 'GUI', "Started")
    
    # Run event loop
    app.exec_()
    
    log_info(log_queue, 'GUI', "PyQt5 tabbed GUI stopped")


def run_worker(messageQueue, serialDisplayQueue, statusQueue, stop_event, 
               eulerDisplayQueue, controlQueue, serialControlQueue, 
               udpControlQueue, logQueue, uiStatusQueue, inputCommandQueue, inputResponseQueue):
    """
    Compatibility wrapper for the process manager.
    
    This function maintains the same interface as the original launcher
    to ensure compatibility with the existing process manager.
    """
    start_gui_worker(
        serial_control_queue=serialControlQueue,
        fusion_control_queue=controlQueue,
        udp_control_queue=udpControlQueue,
        status_queue=statusQueue,
        ui_status_queue=uiStatusQueue,
        message_queue=messageQueue,
        serial_display_queue=serialDisplayQueue,
        euler_display_queue=eulerDisplayQueue,
        log_queue=logQueue,
        stop_event=stop_event,
        input_command_queue=inputCommandQueue,
        input_response_queue=inputResponseQueue,
        on_stop_callback=lambda: stop_event.set()
    )


if __name__ == "__main__":
    # Test the tabbed GUI independently
    log_info(None, 'GUI', "Testing PyQt5 Tabbed GUI...")
    
    # Create mock queues
    import queue
    import threading
    
    test_queues = {
        'serial': queue.Queue(),
        'fusion': queue.Queue(),
        'udp': queue.Queue(),
        'status': queue.Queue(),
        'message': queue.Queue()
    }
    
    stop_event = threading.Event()
    
    def test_stop():
        log_info(None, 'GUI', "Test stop callback called")
        stop_event.set()
    
    # Start GUI
    start_gui_worker(
        serial_control_queue=test_queues['serial'],
        fusion_control_queue=test_queues['fusion'],
        udp_control_queue=test_queues['udp'],
        status_queue=test_queues['status'],
        message_queue=test_queues['message'],
        stop_event=stop_event,
        on_stop_callback=test_stop
    )