"""
PyQt5 Orientation Panel for orienta GUI.

Display-only panel showing Euler angles (Yaw, Pitch, Roll).
No controls - purely for data visualization.
"""
from PyQt5.QtWidgets import (QGroupBox, QVBoxLayout, QHBoxLayout, QGridLayout, 
                             QLabel, QSizePolicy, QWidget, QFrame, QPushButton, QDialog, QSlider, QApplication,
                             QStackedWidget)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QPen, QColor, QKeySequence


# HoldPanelQt: previously in hold_panel.py — moved here so the panel file
# can be removed. This class provides the same animated "hold still" banner
# used both as the top-of-window indicator and (via a smaller instance)
# inside the Orientation panel when live data isn't available.
class HoldPanelQt(QWidget):
    """Panel for displaying HOLD STILL indicator at the top of the application."""
    
    def __init__(self, parent=None, text=None, height=30):
        """
        Initialize the Hold Still Panel.
        
        Args:
            parent: Parent PyQt widget
            text: Optional custom text ('\n' is rendered as a line break)
            height: Fixed height of the panel in pixels
        """
        super().__init__(parent)
        
        # Animation state tracking (scrolling highlight)
        self._blink_timer = QTimer()
        self._blink_timer.timeout.connect(self._on_blink_timer)
        self._scroll_index = 0
        self._is_scrolling = False  # Track if animation is active
        self._direction = 1
        self._base_text = text if text else "- HOLD STILL & UPRIGHT -"
        self._highlight_width = 5
        self._fixed_height = int(height)
        
        self.setup_ui()
        
    def setup_ui(self):
        """Setup the hold still panel UI."""
        # Main layout - horizontal to center the text
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(8, 4, 8, 4)  # Small padding
        main_layout.setSpacing(0)
        
        # Add stretch before text to center it
        main_layout.addStretch()
        
        # HOLD STILL label (we'll render per-character HTML for animation)
        self.hold_still_label = QLabel(self._base_text)
        self.hold_still_label.setAlignment(Qt.AlignCenter)
        
        # Style the text
        font = self.hold_still_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)  # Larger text for top-level visibility
        self.hold_still_label.setFont(font)
        
        # Initially subtle (static gray text)
        self._set_normal_style()
        
        main_layout.addWidget(self.hold_still_label)
        
        # Add stretch after text to center it
        main_layout.addStretch()
        
        # Set fixed height for consistent layout
        self.setFixedHeight(self._fixed_height)
        
    def _set_normal_style(self):
        """Set normal (non-blinking) style."""
        # When not animating we can use a simple stylesheet
        self.hold_still_label.setStyleSheet("color: #666666;")  # Subtle gray
        
    def _set_yellow_style(self):
        """Set yellow (blinking) style."""
        self.hold_still_label.setStyleSheet("color: #FFD700; font-weight: bold;")  # Bright gold/yellow
        
    def _on_blink_timer(self):
        """Handle timer ticks and advance the scrolling highlight (bounce side-to-side)."""
        if not self._is_scrolling:
            return

        n = len(self._base_text)
        width = min(self._highlight_width, n)
        # If text is too short for movement, just render highlight once
        if n <= width:
            self._scroll_index = 0
            self._update_scrolling_text()
            return

        max_index = n - width
        # Move and bounce at edges
        self._scroll_index += self._direction
        if self._scroll_index >= max_index:
            self._scroll_index = max_index
            self._direction = -1
        elif self._scroll_index <= 0:
            self._scroll_index = 0
            self._direction = 1

        self._update_scrolling_text()

    def _escape_char(self, ch: str) -> str:
        if ch == '\n':
            return '<br>'
        if ch == ' ':
            return '&nbsp;'
        if ch == '&':
            return '&amp;'
        if ch == '<':
            return '&lt;'
        if ch == '>':
            return '&gt;'
        return ch

    def _update_scrolling_text(self):
        """Render the label text as HTML with a single-character highlight that scrolls."""
        parts = []
        highlight_color = '#FFD700'
        normal_color = '#666666'
        n = len(self._base_text)
        width = min(self._highlight_width, n)
        # Build per-character spans so spacing and ampersands are preserved
        start = self._scroll_index
        end = start + width
        for i, ch in enumerate(self._base_text):
            esc = self._escape_char(ch)
            if start <= i < end:
                parts.append(f"<span style=\"color: {highlight_color}; font-weight: bold;\">{esc}</span>")
            else:
                parts.append(f"<span style=\"color: {normal_color};\">{esc}</span>")

        # Use rich text; QLabel will render it. Keep font set via setFont.
        html = ''.join(parts)
        self.hold_still_label.setText(html)
    
    def start_blinking(self):
        """Start the scrolling-color animation (keeps old API name)."""
        if self._is_scrolling:
            return  # Already running

        self._is_scrolling = True
        self._scroll_index = 0
        self._direction = 1
        # Faster update and 5-char wide highlight
        self._blink_timer.start(15)  # advance every 15ms
        # Immediately render current state
        self._update_scrolling_text()
        
    def stop_blinking(self):
        """Stop the scrolling-color animation and reset to normal text."""
        if not self._is_scrolling:
            return

        self._is_scrolling = False
        self._blink_timer.stop()
        self._scroll_index = 0
        # Reset to simple plain text to avoid leftover HTML styling
        self.hold_still_label.setText(self._base_text)
        self._set_normal_style()
        
    def is_blinking(self):
        """Return whether the panel is currently blinking."""
        return self._is_scrolling


# Two-line button implemented by subclassing QPushButton so QSS targeting
# QPushButton still applies. It draws the button background using the
# current QStyle and renders a bold main line with an optional smaller
# secondary line beneath it (used to display shortcut names).
from PyQt5.QtWidgets import QStyleOptionButton, QPushButton
from PyQt5.QtGui import QPainter, QFont, QFontMetrics
from PyQt5.QtCore import QRect, QSize

class TwoLineButton(QPushButton):
    def __init__(self, main_text: str = "", sub_text: str = "", parent=None):
        super().__init__(main_text, parent)
        self._main = main_text or ""
        self._sub = sub_text or ""
        self.setCursor(Qt.PointingHandCursor)
        # Ensure QPushButton's default sizePolicy is preserved

    def setParts(self, main: str, sub: str):
        self._main = main or ""
        self._sub = sub or ""
        # Keep accessibility/plain text in text property
        self.setText(self._main)
        # Inform layout that size may have changed
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        """Return a size that can comfortably contain two lines plus padding.

        Compute heights using the same font metrics and padding used by paintEvent
        so the rendered text won't be clipped by the layout.
        """
        base = super().sizeHint()
        # Main line metrics (bold)
        mainFont = QFont(self.font())
        mainFont.setBold(True)
        mainFm = QFontMetrics(mainFont)
        width_main = mainFm.horizontalAdvance(self._main)
        mainH = mainFm.height()

        subH = 0
        width_sub = 0
        if self._sub:
            subFont = QFont(self.font())
            subFont.setPointSize(max(subFont.pointSize() - 2, 8))
            subFm = QFontMetrics(subFont)
            subH = subFm.height()
            width_sub = subFm.horizontalAdvance(self._sub)

        # Padding matches paintEvent's rect.adjusted(8,6,-8,-6) -> vertical padding 6+6
        vertical_padding = 12
        interline_spacing = 2 if self._sub else 0

        height = mainH + (subH + interline_spacing if self._sub else 0) + vertical_padding
        width = max(base.width(), width_main + 24, width_sub + 24)
        return QSize(width, height)

    def paintEvent(self, event):
        opt = QStyleOptionButton()
        opt.initFrom(self)
        # Prevent style from drawing the text so we can render two lines
        opt.text = ""
        p = QPainter(self)
        # Draw the button background and frame using QStyle so theming/QSS applies
        self.style().drawControl(self.style().CE_PushButton, opt, p, self)

        # Compute drawing rect and render the two lines centered
        rect = self.rect().adjusted(8, 6, -8, -6)
        mainFont = QFont(self.font())
        mainFont.setBold(True)
        mainFm = QFontMetrics(mainFont)
        mainH = mainFm.height()

        # Choose text color based on enabled state so disabled buttons appear muted
        try:
            from PyQt5.QtGui import QPalette
            if self.isEnabled():
                main_pen = self.palette().color(QPalette.ButtonText)
            else:
                # Use the palette's disabled button text color for consistent theming
                main_pen = self.palette().color(QPalette.Disabled, QPalette.ButtonText)
        except Exception:
            main_pen = QColor(0, 0, 0)

        if self._sub:
            subFont = QFont(self.font())
            subFont.setPointSize(max(subFont.pointSize() - 2, 8))
            subFm = QFontMetrics(subFont)
            totalH = mainH + 2 + subFm.height()
            y = rect.top() + max(0, (rect.height() - totalH) // 2)

            p.setFont(mainFont)
            p.setPen(main_pen)
            p.drawText(QRect(rect.left(), y, rect.width(), mainH), int(Qt.AlignCenter), self._main)

            p.setFont(subFont)
            # Subline should be slightly more muted when disabled
            try:
                if self.isEnabled():
                    sub_pen = self.palette().color(QPalette.ButtonText)
                else:
                    sub_pen = self.palette().color(QPalette.Disabled, QPalette.ButtonText)
            except Exception:
                sub_pen = main_pen
            y2 = y + mainH + 2
            p.setPen(sub_pen)
            p.drawText(QRect(rect.left(), y2, rect.width(), subFm.height()), int(Qt.AlignCenter), self._sub)
        else:
            p.setFont(mainFont)
            p.setPen(main_pen)
            p.drawText(rect, int(Qt.AlignCenter), self._main)

        p.end()

import math
import queue


# Theming is handled via application QSS; per-widget theme helper removed.

class KeyCaptureDialog(QDialog):
    """Dialog to capture keyboard and gamepad input via input worker."""
    
    def __init__(self, parent=None, current_key=None, input_command_queue=None, input_response_queue=None, owner_panel=None):
        super().__init__(parent)
        self.setWindowTitle("Capture Reset Shortcut")
        self.setModal(True)
        self.resize(300, 150)
        
        # Store input worker queues
        self.input_command_queue = input_command_queue
        self.input_response_queue = input_response_queue
        # Panel that normally polls input_response_queue continuously for
        # shortcut triggers (e.g. CalibrationPanelQt). While this dialog is
        # open we ask it to pause so we don't race for 'input_captured'
        # responses on the shared queue.
        self.owner_panel = owner_panel
        
        # Apply dark mode styling if parent uses dark theme
        if parent:
            try:
                bg_color = parent.palette().color(parent.backgroundRole())
                is_dark = bg_color.value() < 128
                if is_dark:
                    dark_style = """
                    QDialog {
                        background-color: #2b2b2b;
                        color: #ffffff;
                    }
                    QLabel {
                        color: #ffffff;
                        background-color: transparent;
                    }
                    QLabel[status="disabled"] {
                        color: #888888;
                    }
                    """
                    self.setStyleSheet(dark_style)
            except Exception:
                pass
        
        # Center dialog over parent
        if parent:
            try:
                parent_geo = parent.geometry()
                x = parent_geo.x() + (parent_geo.width() - 300) // 2
                y = parent_geo.y() + (parent_geo.height() - 120) // 2
                self.move(x, y)
            except Exception:
                pass
        
        self.captured_key = current_key if current_key and current_key != 'None' else None
        self.display_name = None
        
        layout = QVBoxLayout()
        
        # Instructions
        instructions = QLabel("Press any key or gamepad button to set as shortcut:")
        instructions.setAlignment(Qt.AlignCenter)
        layout.addWidget(instructions)
        
        # Additional info
        info_label = QLabel("(Keyboard, gamepad buttons, or D-pad supported)\\n(Esc to cancel)")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)
        
        if current_key and current_key != 'None':
            layout.addWidget(QLabel(f"Current: {current_key}"))
            
        self.status_label = QLabel("Waiting for input...")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
        self.setLayout(layout)
        
        # Pause the owner panel's continuous input_response_queue polling
        # so it doesn't race with us for 'input_captured' responses.
        if self.owner_panel and hasattr(self.owner_panel, 'pause_input_response_monitoring'):
            try:
                self.owner_panel.pause_input_response_monitoring()
            except Exception:
                pass

        # Start input capture via input worker
        if self.input_command_queue:
            try:
                self.input_command_queue.put(('start_capture',))
                print("[KeyCaptureDialog] Sent start_capture command to input worker")
                # Start timer to check for responses
                self.response_timer = QTimer()
                self.response_timer.timeout.connect(self._check_input_response)
                self.response_timer.start(50)  # Check every 50ms
            except Exception as e:
                print(f"[KeyCaptureDialog] Error starting capture: {e}")
                self.status_label.setText("Input capture unavailable")
        else:
            print("[KeyCaptureDialog] No input command queue available")
            self.status_label.setText("Input capture unavailable")
    
    def _check_input_response(self):
        """Check for responses from input worker."""
        if not self.input_response_queue:
            return
            
        try:
            response = self.input_response_queue.get_nowait()
            print(f"[KeyCaptureDialog] Received response from input worker: {response}")
            if response and len(response) >= 3 and response[0] == 'input_captured':
                self.captured_key = response[1]
                self.display_name = response[2]
                print(f"[KeyCaptureDialog] Captured input: key={self.captured_key}, display={self.display_name}")
                self.status_label.setText(f"Captured: {self.display_name}")
                QApplication.processEvents()
                QTimer.singleShot(500, self.accept)
        except queue.Empty:
            pass  # No response available
        except Exception as e:
            print(f"[KeyCaptureDialog] Error checking response: {e}")
    
    def done(self, r):
        """Single reliable cleanup point for this dialog.

        QDialog routes accept(), reject(), and the default window-close
        handling (closeEvent -> reject()) all through done(), so hooking
        cleanup here (rather than closeEvent, which is NOT invoked by
        accept()/reject() directly) guarantees it always runs exactly once.
        """
        if hasattr(self, 'response_timer'):
            try:
                self.response_timer.stop()
            except Exception:
                pass
        
        # Only stop capture if dialog was rejected (not accepted)
        # When accepted, the caller sends set_shortcut which starts the appropriate listener
        if r != QDialog.Accepted and self.input_command_queue:
            try:
                self.input_command_queue.put(('stop_capture',))
                print("[KeyCaptureDialog] Dialog cancelled - sent stop_capture command to input worker")
            except Exception as e:
                print(f"[KeyCaptureDialog] Error stopping capture: {e}")
        
        # Resume the owner panel's continuous input_response_queue polling
        # now that we're done consuming 'input_captured' responses.
        if self.owner_panel and hasattr(self.owner_panel, 'resume_input_response_monitoring'):
            try:
                self.owner_panel.resume_input_response_monitoring()
            except Exception:
                pass
        
        super().done(r)
    
    def keyPressEvent(self, event):
        """Capture keyboard input directly."""
        key = event.key()
        
        # ESC to cancel
        if key == 0x01000000:  # Qt.Key_Escape
            self.reject()
            return
            
        # Map numpad keys to their string representations
        numpad_keys = {
            0x01000030: 'KP_0', 0x01000031: 'KP_1', 0x01000032: 'KP_2',
            0x01000033: 'KP_3', 0x01000034: 'KP_4', 0x01000035: 'KP_5',
            0x01000036: 'KP_6', 0x01000037: 'KP_7', 0x01000038: 'KP_8',
            0x01000039: 'KP_9', 0x01000041: 'KP_Decimal', 0x01000042: 'KP_Divide',
            0x01000043: 'KP_Multiply', 0x01000044: 'KP_Subtract',
            0x01000045: 'KP_Add', 0x01000046: 'KP_Enter'
        }
        
        # Check if it's a numpad key
        if key in numpad_keys:
            self.captured_key = numpad_keys[key]
            self.display_name = f"Numpad {numpad_keys[key][3:]}"
        else:
            # For regular keys, use the text
            text = event.text()
            if text and text.isprintable():
                self.captured_key = text.lower()
                self.display_name = text.upper()
            else:
                # Special keys like F1-F12, Space, etc.
                key_name = QKeySequence(key).toString()
                if key_name:
                    self.captured_key = key_name.lower()
                    self.display_name = key_name
                else:
                    self.status_label.setText("Unsupported key, try another")
                    return
        
        self.status_label.setText(f"Captured: {self.display_name}")
        QTimer.singleShot(500, self.accept)


# OrientationVisualizationWidget moved here from calibration_panel.py
class OrientationVisualizationWidget(QWidget):
    """Real-time visualization of pitch, yaw, and roll orientation."""
    
    def __init__(self, parent=None, range_degrees=None):
        """
        Initialize the orientation visualization.
        
        Args:
            parent: Parent widget
            range_degrees: +/- range for pitch/yaw axes in degrees (defaults to config value)
        """
        super().__init__(parent)
        # Use config value if not specified, allows for dynamic updates
        try:
            from config.config import VISUALIZATION_RANGE, VISUALIZATION_SIZE
            self.range_degrees = range_degrees if range_degrees is not None else VISUALIZATION_RANGE
            # Allow the visualization to expand to fill available space; keep a reasonable minimum
            self.setMinimumSize(int(VISUALIZATION_SIZE * 0.5), int(VISUALIZATION_SIZE * 0.5))
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        except Exception:
            # Fallback sizes if config not accessible
            self.range_degrees = range_degrees if range_degrees is not None else 25.0
            self.setMinimumSize(100, 100)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Current orientation values
        self.pitch = 0.0
        self.yaw = 0.0
        self.roll = 0.0
        
        # Drift correction status
        self.drift_correction_active = False
        self.drift_angle_yaw = 5.0  # Default yaw drift angle in degrees
        self.drift_angle_pitch = 5.0  # Default pitch drift angle in degrees
        self.drift_angle_roll = 5.0  # Default roll drift angle in degrees
        
        # Axis inversion settings
        self.invert_yaw = False
        self.invert_pitch = False
        self.invert_roll = False
        
        # Widget appearance
        self.setStyleSheet("background-color: black; border: 1px solid gray;")
    
    def update_orientation(self, pitch, yaw, roll):
        """
        Update the visualization with new orientation data.
        
        Args:
            pitch: Pitch angle in degrees
            yaw: Yaw angle in degrees  
            roll: Roll angle in degrees
        """
        self.pitch = float(pitch)
        self.yaw = float(yaw)
        self.roll = float(roll)
        self.update()  # Trigger repaint
    
    def update_drift_correction(self, active):
        """
        Update the drift correction status.
        
        Args:
            active: Boolean indicating if drift correction is active
        """
        self.drift_correction_active = bool(active)
        self.update()  # Trigger repaint
    
    def update_drift_angle_yaw(self, angle):
        """
        Update the yaw drift angle for ellipse calculation.
        
        Args:
            angle: Yaw drift angle in degrees
        """
        self.drift_angle_yaw = float(angle)
        self.update()  # Trigger repaint
    
    def update_drift_angle_pitch(self, angle):
        """
        Update the pitch drift angle for ellipse calculation.
        
        Args:
            angle: Pitch drift angle in degrees
        """
        self.drift_angle_pitch = float(angle)
        self.update()  # Trigger repaint
    
    def update_drift_angle_roll(self, angle):
        """
        Update the roll drift angle for ellipse calculation.
        
        Args:
            angle: Roll drift angle in degrees
        """
        self.drift_angle_roll = float(angle)
        self.update()  # Trigger repaint
    
    def set_invert_yaw(self, invert):
        """
        Set yaw axis inversion.
        
        Args:
            invert: Boolean indicating if yaw should be inverted
        """
        self.invert_yaw = bool(invert)
        self.update()  # Trigger repaint
    
    def set_invert_pitch(self, invert):
        """
        Set pitch axis inversion.
        
        Args:
            invert: Boolean indicating if pitch should be inverted
        """
        self.invert_pitch = bool(invert)
        self.update()  # Trigger repaint
    
    def set_invert_roll(self, invert):
        """
        Set roll axis inversion.
        
        Args:
            invert: Boolean indicating if roll should be inverted
        """
        self.invert_roll = bool(invert)
        self.update()  # Trigger repaint

    def paintEvent(self, event):
        """
        Draw the orientation visualization.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Get widget center and size
        width = self.width()
        height = self.height()
        center_x = width // 2
        center_y = height // 2
        
        # Draw coordinate system
        self._draw_coordinate_system(painter, center_x, center_y, width, height)
        
        # Draw orientation indicator
        self._draw_orientation_indicator(painter, center_x, center_y, width, height)
        
        # Draw drift correction circle
        self._draw_drift_correction_circle(painter, center_x, center_y)

    def _get_theme_colors(self):
        """
        Get theme-appropriate colors based on current application style.
        
        Returns:
            dict: Dictionary of color values for different elements
        """
        # Try to detect if we're in dark mode by checking widget background
        bg_color = self.palette().color(self.backgroundRole())
        is_dark = bg_color.value() < 128  # Dark if background is dark
        
        if is_dark:
            return {
                'grid': QColor(80, 80, 80),
                'axis': QColor(160, 160, 160),
                'text': QColor(200, 200, 200),
                'center': QColor(255, 255, 255),
                'within_threshold': QColor(100, 150, 255),  # Blue
                'outside_threshold': QColor(100, 255, 100),  # Green
                'drift_active': QColor(255, 100, 100),  # Red
                'drift_inactive': QColor(100, 100, 100)  # Gray
            }
        else:
            return {
                'grid': QColor(200, 200, 200),
                'axis': QColor(100, 100, 100),
                'text': QColor(50, 50, 50),
                'center': QColor(0, 0, 0),
                'within_threshold': QColor(0, 50, 200),  # Dark Blue
                'outside_threshold': QColor(0, 150, 0),  # Dark Green
                'drift_active': QColor(200, 0, 0),  # Dark Red
                'drift_inactive': QColor(150, 150, 150)  # Light Gray
            }

    


    def _draw_coordinate_system(self, painter, center_x, center_y, width, height):
        """
        Draw the coordinate grid and axes.
        """
        colors = self._get_theme_colors()
        
        # Get current range from config (allows dynamic updates)
        try:
            from config.config import VISUALIZATION_RANGE
            current_range = VISUALIZATION_RANGE
        except Exception:
            current_range = getattr(self, 'range_degrees', 25.0)
        
        # Grid lines
        painter.setPen(QPen(colors['grid'], 1))
        
        # Vertical grid lines
        for i in range(-2, 3):  
            if i != 0:
                x = center_x + i * (width // 5)
                if 5 <= x <= width - 5:
                    painter.drawLine(x, 5, x, height - 5)
        
        # Horizontal grid lines  
        for i in range(-2, 3):  
            if i != 0:
                y = center_y + i * (height // 5)
                if 5 <= y <= height - 5:
                    painter.drawLine(5, y, width - 5, y)
        
        # Center axes
        painter.setPen(QPen(colors['axis'], 2))
        painter.drawLine(center_x, 5, center_x, height - 5)  # Vertical axis
        painter.drawLine(5, center_y, width - 5, center_y)   # Horizontal axis
        
        # Corner range labels (use current config value)
        painter.setPen(QPen(colors['text'], 1))
        painter.drawText(5, 15, f"{current_range:.0f}")
        painter.drawText(width - 25, 15, f"{current_range:.0f}")
        painter.drawText(5, height - 5, f"{-current_range:.0f}")
        painter.drawText(width - 30, height - 5, f"{-current_range:.0f}")
    
    def _draw_orientation_indicator(self, painter, center_x, center_y, width, height):
        """
        Draw the orientation indicator line with color based on threshold status.
        Blue when all angles are within their respective thresholds, green when outside.
        """
        colors = self._get_theme_colors()
        
        # Get current range from config (allows dynamic updates)
        try:
            from config.config import VISUALIZATION_RANGE
            current_range = VISUALIZATION_RANGE
        except Exception:
            current_range = getattr(self, 'range_degrees', 25.0)
        
        # Data is already inverted by fusion worker, so just use it directly
        # Calculate position based on pitch/yaw
        # Yaw maps to X axis, Pitch maps to Y axis
        yaw_ratio = max(-1.0, min(1.0, -self.yaw / current_range))  # Negate yaw for display
        pitch_ratio = max(-1.0, min(1.0, self.pitch / current_range))
        
        indicator_x = center_x + yaw_ratio * (width // 2 - 10)
        indicator_y = center_y + pitch_ratio * (height // 2 - 10)
        
        # Calculate line endpoints based on roll angle
        roll_rad = math.radians(self.roll)
        line_length = 20
        
        start_x = indicator_x - line_length * math.cos(roll_rad)
        start_y = indicator_y - line_length * math.sin(roll_rad)
        end_x = indicator_x + line_length * math.cos(roll_rad)
        end_y = indicator_y + line_length * math.sin(roll_rad)
        
        # Check if all angles are within their respective thresholds
        def _angle_diff(a, b):
            diff = abs(a - b)
            return min(diff, 360 - diff)
        
        yaw_within = _angle_diff(self.yaw, 0) < self.drift_angle_yaw
        pitch_within = _angle_diff(self.pitch, 0) < self.drift_angle_pitch
        roll_within = _angle_diff(self.roll, 0) < self.drift_angle_roll
        
        all_within_threshold = yaw_within and pitch_within and roll_within
        
        # Choose line color: blue if within all thresholds, green if outside
        if all_within_threshold:
            line_color = colors['within_threshold']
        else:
            line_color = colors['outside_threshold']
        
        # Draw orientation line
        painter.setPen(QPen(line_color, 3))
        painter.drawLine(int(start_x), int(start_y), int(end_x), int(end_y))
        
        # Draw center dot
        painter.setPen(QPen(colors['center'], 2))
        painter.drawEllipse(int(indicator_x - 3), int(indicator_y - 3), 6, 6)
    
    def _draw_drift_correction_circle(self, painter, center_x, center_y):
        """
        Draw the drift correction status ellipse at the center.
        Ellipse size corresponds to the yaw and pitch drift correction angles scaled to coordinate system.
        Forms a circle when yaw and pitch angles are equal, ellipse when different.
        Red outline at all times, blue filled when drift correction is active.
        """
        colors = self._get_theme_colors()
        
        # Get current range from config (allows dynamic updates)
        try:
            from config.config import VISUALIZATION_RANGE
            current_range = VISUALIZATION_RANGE
        except Exception:
            current_range = getattr(self, 'range_degrees', 25.0)

        # Calculate radii based on drift angles using the actual widget size so
        # the ellipse scales consistently with the orientation indicator.
        w = self.width()
        h = self.height()
        usable_radius = (min(w, h) // 2) - 10  # Usable radius in pixels (margin for edges)
        if usable_radius <= 0:
            usable_radius = 1
        pixels_per_degree = usable_radius / current_range  # Pixels per degree
        
        # Convert drift angles directly to pixels
        # Yaw maps to horizontal (width), pitch maps to vertical (height)
        ellipse_width_pixels = int(self.drift_angle_yaw * pixels_per_degree * 2)  # Full width
        ellipse_height_pixels = int(self.drift_angle_pitch * pixels_per_degree * 2)  # Full height
        
        # Ensure minimum visibility and maximum size
        ellipse_width_pixels = max(4, min(ellipse_width_pixels, usable_radius * 2))
        ellipse_height_pixels = max(4, min(ellipse_height_pixels, usable_radius * 2))
        
        # Calculate ellipse rectangle
        ellipse_rect_x = center_x - ellipse_width_pixels // 2
        ellipse_rect_y = center_y - ellipse_height_pixels // 2
        
        if self.drift_correction_active:
            # Active: Blue filled with red outline
            painter.setBrush(QColor(100, 150, 255, 100))  # Semi-transparent blue fill
            painter.setPen(QPen(QColor(255, 50, 50), 2))  # Red outline
            painter.drawEllipse(ellipse_rect_x, ellipse_rect_y, ellipse_width_pixels, ellipse_height_pixels)
        else:
            # Inactive: Red outline only
            from PyQt5.QtCore import Qt as _Qt
            painter.setBrush(_Qt.NoBrush)  # No fill
            painter.setPen(QPen(QColor(255, 50, 50), 2))  # Red outline
            painter.drawEllipse(ellipse_rect_x, ellipse_rect_y, ellipse_width_pixels, ellipse_height_pixels)

class SquareContainer(QWidget):
    """Container that keeps a single child widget square and centered.

    The child is given no layout management; instead this container
    manually positions it on resize so it always stays square (using
    the smaller of the available width/height) and centered within
    the available space.
    """

    def __init__(self, child, parent=None):
        super().__init__(parent)
        self._child = child
        self._child.setParent(self)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        side = max(0, min(self.width(), self.height()))
        x = (self.width() - side) // 2
        y = (self.height() - side) // 2
        self._child.setGeometry(x, y, side, side)


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
        # Main layout - single column for data displays only
        main_layout = QVBoxLayout()
        # Add modest vertical padding inside the panel to match other panels
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(4, 6, 4, 6)
        self.setLayout(main_layout)
        
        # Allow panel to expand vertically to fill available space when appropriate
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        # Build display components now arranged as a vertical split: visualization left, values right
        split_layout = QHBoxLayout()
        split_layout.setSpacing(12)
        split_layout.setContentsMargins(0, 0, 0, 0)

        # Left: visualization frame
        viz_frame = QFrame()
        # Use Ignored horizontal policy so the layout distributes width purely
        # according to the stretch factors below (true 50/50 split) instead of
        # being skewed by the frame's content size hints.
        viz_frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        viz_layout = QVBoxLayout(viz_frame)
        viz_layout.setSpacing(6)
        viz_layout.setContentsMargins(6, 4, 6, 4)

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

        split_layout.addWidget(viz_frame, stretch=1)

        # Vertical divider styled like calibration panel
        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setObjectName("sectionDivider")
        divider.setFixedWidth(1)
        split_layout.addWidget(divider)

        # Right: values list (Yaw / Pitch / Roll)
        values_frame = QFrame()
        # Use Ignored horizontal policy so both halves split evenly (matches
        # viz_frame policy above), keeping the divider centered.
        values_frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        values_layout = QVBoxLayout(values_frame)
        # Reduce spacing and margins to remove superfluous padding
        values_layout.setSpacing(6)
        values_layout.setContentsMargins(6, 6, 6, 6)

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
            divider.setFixedHeight(1)
            divider.setStyleSheet("background-color: rgba(120,120,120,0.4);")
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
            top_spacer = QSpacerItem(20, 20, QSP.Minimum, QSP.Expanding)
            bottom_spacer = QSpacerItem(20, 20, QSP.Minimum, QSP.Expanding)
        except Exception:
            top_spacer = None
            bottom_spacer = None

        # Insert a flexible spacer above the euler grid
        if top_spacer:
            values_layout.addItem(top_spacer)

        # Euler angles: headers on top row, values on second row (three columns)
        euler_grid = QGridLayout()
        euler_grid.setContentsMargins(0, 0, 0, 0)
        euler_grid.setHorizontalSpacing(12)
        # Reduce vertical space between header row and value row
        try:
            euler_grid.setVerticalSpacing(4)
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
        self.yaw_value_label.setStyleSheet("font-size: 18px;")
        euler_grid.addWidget(self.yaw_value_label, 1, 0)

        self.pitch_value_label = QLabel("0.0°")
        self.pitch_value_label.setAlignment(Qt.AlignCenter)
        self.pitch_value_label.setStyleSheet("font-size: 18px;")
        euler_grid.addWidget(self.pitch_value_label, 1, 1)

        self.roll_value_label = QLabel("0.0°")
        self.roll_value_label.setAlignment(Qt.AlignCenter)
        self.roll_value_label.setStyleSheet("font-size: 18px;")
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

        self.hold_indicator = HoldPanelQt(text="- HOLD STILL & UPRIGHT -", height=56)
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
            angles_buttons_divider.setFixedHeight(1)
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
            desired_h = max(self.disengage_btn.sizeHint().height(), text_height + 16)
            # Use fixed height based on sizeHint to avoid clipping of two-line content
            self.disengage_btn.setFixedHeight(desired_h + 6)
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

        # Connect to calibration panel handlers if panel already connected
        try:
            if hasattr(self, 'calibration_panel') and self.calibration_panel:
                # Let calibration panel reference this button
                self.calibration_panel.disengage_btn = self.disengage_btn
                self.disengage_btn.pressed.connect(self.calibration_panel._on_disengage_pressed)
                self.disengage_btn.released.connect(self.calibration_panel._on_disengage_released)
        except Exception:
            pass

        # Always connect to local handlers so the buttons work even if calibration_panel
        # wiring is performed later (or not at all). This ensures the UI remains
        # functional after the consolidation into OrientationPanel.
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
            # stored_drift_* exist on calibration_panel historically; mirror here for safety
            self.stored_drift_yaw = getattr(self, 'drift_angle_yaw_value', 5.0)
            self.stored_drift_pitch = getattr(self, 'drift_angle_pitch_value', 5.0)
            self.stored_drift_roll = getattr(self, 'drift_angle_roll_value', 5.0)
        except Exception:
            pass

        # Shortcut button uses the same widget and styling path as every other
        # button in the panel; only its width is constrained by the row layout.
        try:
            self.disengage_shortcut_btn = QPushButton("🔧")
            self.disengage_shortcut_btn.setToolTip("Set shortcut for Disengage Drift Correction")
            try:
                desired_h = max(self.disengage_btn.sizeHint().height(), text_height + 16)
                self.disengage_shortcut_btn.setFixedHeight(desired_h + 6)
            except Exception:
                pass
            # Shortcut button keeps fixed width but match vertical height
            self.disengage_shortcut_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            disengage_row.addWidget(self.disengage_shortcut_btn)

            # Connect handler to open capture dialog and set shortcut via calibration panel
            def _on_set_disengage_shortcut():
                try:
                    # KeyCaptureDialog is defined above in this module
                    # Prefer this panel's input queues (set by gui_wrk). Fall back to calibration_panel if not present.
                    cal = getattr(self, 'calibration_panel', None)
                    current = getattr(self, 'disengage_shortcut', None)
                    if current is None and cal:
                        current = getattr(cal, 'disengage_shortcut', 'None')
                    input_cmd_q = getattr(self, 'input_command_queue', None) or (getattr(cal, 'input_command_queue', None) if cal else None)
                    input_resp_q = getattr(self, 'input_response_queue', None) or (getattr(cal, 'input_response_queue', None) if cal else None)
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

                        # Update PreferencesPanel state so saving persists this shortcut
                        try:
                            prefs = getattr(self, 'preferences_panel', None) or (getattr(cal, 'preferences_panel', None) if cal else None)
                            if prefs:
                                prefs.disengage_shortcut = key
                                prefs.disengage_shortcut_display_name = display_name
                                # Emit preferences_changed so TabbedGUIWorker saves immediately
                                try:
                                    prefs.preferences_changed.emit()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[OrientationPanel] Error setting disengage shortcut: {e}")

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
                                from util.error_utils import safe_queue_put
                                from config.config import QUEUE_PUT_TIMEOUT
                                if self.control_queue:
                                    # Use the same action as calibration_panel to disable thresholds atomically
                                    safe_queue_put(self.control_queue, ('set_threshold', 0.0, 0.0, 0.0), timeout=QUEUE_PUT_TIMEOUT)
                            except Exception:
                                pass
                        else:
                            # Restore stored values in the fusion/control worker but preserve the slider UI values
                            try:
                                from util.error_utils import safe_queue_put
                                from config.config import QUEUE_PUT_TIMEOUT
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
        reset_row.setSpacing(6)
        reset_row.setContentsMargins(0, 0, 0, 0)

        self.reset_button = TwoLineButton("Reset Orientation", "")
        # Size to match width of column
        self.reset_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Fixed height similar to disengage button
        try:
            desired_h = max(self.reset_button.sizeHint().height(), text_height + 16)
            # Ensure reset button is tall enough for two-line rendering
            self.reset_button.setFixedHeight(desired_h + 6)
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
            self.reset_shortcut_btn = QPushButton("🔧")
            self.reset_shortcut_btn.setToolTip("Set shortcut for Reset Orientation")
            try:
                desired_h = max(self.reset_button.sizeHint().height(), text_height + 16)
                self.reset_shortcut_btn.setFixedHeight(desired_h + 6)
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
                    # Prefer this panel's input queues (set by gui_wrk). Fall back to calibration_panel if not present.
                    cal = getattr(self, 'calibration_panel', None)
                    current = getattr(self, 'reset_shortcut', None)
                    if current is None and cal:
                        current = getattr(cal, 'reset_shortcut', 'None')
                    input_cmd_q = getattr(self, 'input_command_queue', None) or (getattr(cal, 'input_command_queue', None) if cal else None)
                    input_resp_q = getattr(self, 'input_response_queue', None) or (getattr(cal, 'input_response_queue', None) if cal else None)
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

                        # Update PreferencesPanel state so saving persists this shortcut
                        try:
                            prefs = getattr(self, 'preferences_panel', None) or (getattr(cal, 'preferences_panel', None) if cal else None)
                            if prefs:
                                prefs.reset_shortcut = key
                                prefs.reset_shortcut_display_name = display_name
                                # Emit preferences_changed so TabbedGUIWorker saves immediately
                                try:
                                    prefs.preferences_changed.emit()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[OrientationPanel] Error setting reset shortcut: {e}")

            self.reset_shortcut_btn.clicked.connect(_on_set_reset_shortcut)
        except Exception:
            self.reset_shortcut_btn = None

        # Wire to calibration panel logic if available
        try:
            if hasattr(self, 'calibration_panel') and self.calibration_panel:
                # Give calibration panel a reference to the reset button for UI sync,
                # but avoid connecting the calibration handler directly to the click
                # to prevent calibration-clearing behaviour. The local handler is
                # the authoritative action invoked on click.
                self.calibration_panel.reset_button = self.reset_button
        except Exception:
            pass

        # Ensure reset button always calls the local handler so the action works
        # even without legacy calibration_panel wiring.
        try:
            self.reset_button.clicked.connect(self._on_reset_orientation)
        except Exception:
            pass

        values_layout.addLayout(reset_row)

        # Recalibrate yaw drift correction button (moved from Calibration panel)
        try:
            self.recal_button = QPushButton("Recalibrate Yaw Drift Correction")
            # Match sizing of other buttons
            try:
                desired_h = max(getattr(self, 'recal_button', None).sizeHint().height() if getattr(self, 'recal_button', None) else 0, text_height + 16)
                self.recal_button.setFixedHeight(desired_h + 6)
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
            # Connect to calibration panel handler if available
            try:
                if hasattr(self, 'calibration_panel') and self.calibration_panel and hasattr(self.calibration_panel, '_on_recalibrate'):
                    self.calibration_panel.recal_button = self.recal_button
                    self.recal_button.clicked.connect(self.calibration_panel._on_recalibrate)
            except Exception:
                pass

            # Also connect to local handler so the button works even without legacy wiring
            try:
                self.recal_button.clicked.connect(self._on_recalibrate)
            except Exception:
                pass

            values_layout.addWidget(self.recal_button)
        except Exception:
            self.recal_button = None

        split_layout.addWidget(values_frame, stretch=1)

        # Add the split layout to the main layout
        main_layout.addLayout(split_layout)

        # Connect sliders to handlers
        try:
            self.drift_yaw_slider.valueChanged.connect(self._on_drift_yaw_angle_change)
        except Exception:
            pass
        try:
            self.drift_pitch_slider.valueChanged.connect(self._on_drift_pitch_angle_change)
        except Exception:
            pass
        try:
            self.drift_roll_slider.valueChanged.connect(self._on_drift_roll_angle_change)
        except Exception:
            pass

        # Horizontal divider separating main panel from sliders
        try:
            sliders_div = QFrame()
            sliders_div.setFrameShape(QFrame.HLine)
            sliders_div.setFrameShadow(QFrame.Sunken)
            sliders_div.setFixedHeight(1)
            sliders_div.setStyleSheet("background-color: rgba(120,120,120,0.4);")
            main_layout.addWidget(sliders_div)
        except Exception:
            pass

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

        # End of UI build

        # --- Drift angle sliders spanning full width ---
        try:
            sliders_frame = QFrame()
            sliders_layout = QVBoxLayout(sliders_frame)
            sliders_layout.setContentsMargins(6, 6, 6, 6)
            sliders_layout.setSpacing(8)

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
                self.drift_roll_slider.valueChanged.connect(self._on_drift_roll_angle_change)
            except Exception:
                pass
            roll_row.addWidget(self.drift_roll_slider, 1)
            self.drift_angle_roll_label = QLabel(f"{self.drift_angle_roll_value:.1f}°")
            self.drift_angle_roll_label.setMinimumWidth(40)
            self.drift_angle_roll_label.setAlignment(Qt.AlignCenter)
            roll_row.addWidget(self.drift_angle_roll_label)
            sliders_layout.addLayout(roll_row)

            main_layout.addWidget(sliders_frame)
        except Exception:
            pass

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

            # Also notify legacy calibration_panel if present for backwards compatibility
            try:
                if hasattr(self, 'calibration_panel') and self.calibration_panel and hasattr(self.calibration_panel, 'update_orientation'):
                    # Old calibration panel used pitch,yaw,roll ordering; keep that call for compatibility
                    try:
                        self.calibration_panel.update_orientation(pitch, yaw, roll)
                    except Exception:
                        try:
                            self.calibration_panel.update_orientation(yaw, pitch, roll)
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception:
            pass

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

            # Also notify calibration panel if present so it can update internal state
            if hasattr(self, 'calibration_panel') and self.calibration_panel:
                try:
                    self.calibration_panel._processing_active = getattr(self.calibration_panel, '_processing_active', False)
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
                print(f"[OrientationPanel] update_processing_status called -> active={active}")
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
        Discard any locally-stored calibration data and reset calibration UI.

        Called when the serial connection is stopped or an external event requires
        clearing calibration state so the UI does not display stale values.
        """
        # Keep the function defensive but simpler to avoid nested try/except matching issues.
        try:
            # Reset calibration indicator
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

            # Reset stored drift angles to defaults
            try:
                from config.config import DEFAULT_CENTER_THRESHOLD
                self.drift_angle_yaw_value = DEFAULT_CENTER_THRESHOLD
                self.drift_angle_pitch_value = DEFAULT_CENTER_THRESHOLD
                self.drift_angle_roll_value = DEFAULT_CENTER_THRESHOLD
            except Exception:
                # If config import fails, fall back to existing values or zeros
                self.drift_angle_yaw_value = getattr(self, 'drift_angle_yaw_value', 0.0)
                self.drift_angle_pitch_value = getattr(self, 'drift_angle_pitch_value', 0.0)
                self.drift_angle_roll_value = getattr(self, 'drift_angle_roll_value', 0.0)

            # Update UI controls if present
            try:
                if hasattr(self, 'drift_angle_yaw_label') and self.drift_angle_yaw_label:
                    self.drift_angle_yaw_label.setText(f"{self.drift_angle_yaw_value:.1f}°")
                if hasattr(self, 'drift_yaw_slider') and self.drift_yaw_slider:
                    self.drift_yaw_slider.setValue(int(self.drift_angle_yaw_value * 10))
                if hasattr(self, 'drift_angle_pitch_label') and self.drift_angle_pitch_label:
                    self.drift_angle_pitch_label.setText(f"{self.drift_angle_pitch_value:.1f}°")
                if hasattr(self, 'drift_pitch_slider') and self.drift_pitch_slider:
                    self.drift_pitch_slider.setValue(int(self.drift_angle_pitch_value * 10))
                if hasattr(self, 'drift_angle_roll_label') and self.drift_angle_roll_label:
                    self.drift_angle_roll_label.setText(f"{self.drift_angle_roll_value:.1f}°")
                if hasattr(self, 'drift_roll_slider') and self.drift_roll_slider:
                    self.drift_roll_slider.setValue(int(self.drift_angle_roll_value * 10))
            except Exception:
                pass

            # Mirror cleared values and stored drifts to attached calibration panel if present
            try:
                self.stored_drift_yaw = self.drift_angle_yaw_value
                self.stored_drift_pitch = self.drift_angle_pitch_value
                self.stored_drift_roll = self.drift_angle_roll_value
                cal = getattr(self, 'calibration_panel', None)
                if cal is not None:
                    if hasattr(cal, 'drift_angle_yaw_value'):
                        cal.drift_angle_yaw_value = self.drift_angle_yaw_value
                    if hasattr(cal, 'drift_angle_pitch_value'):
                        cal.drift_angle_pitch_value = self.drift_angle_pitch_value
                    if hasattr(cal, 'drift_angle_roll_value'):
                        cal.drift_angle_roll_value = self.drift_angle_roll_value
                    if hasattr(cal, 'clear_calibration_state'):
                        try:
                            cal.clear_calibration_state()
                        except Exception:
                            pass
            except Exception:
                pass
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
            from util.error_utils import safe_queue_put
            from config.config import QUEUE_PUT_TIMEOUT
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
            from util.error_utils import safe_queue_put
            from config.config import QUEUE_PUT_TIMEOUT
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
            from util.error_utils import safe_queue_put
            from config.config import QUEUE_PUT_TIMEOUT
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
                from util.error_utils import safe_queue_put
                from config.config import QUEUE_PUT_TIMEOUT
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
                from util.error_utils import safe_queue_put
                from config.config import QUEUE_PUT_TIMEOUT
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
                from util.error_utils import safe_queue_put
                from config.config import QUEUE_PUT_TIMEOUT
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

        # Mirror value to calibration panel so get_prefs() returns updated values for persistence
        try:
            cal = getattr(self, 'calibration_panel', None)
            if cal is not None:
                try:
                    cal.drift_angle_yaw_value = vq
                    if hasattr(cal, 'drift_angle_yaw_label') and cal.drift_angle_yaw_label:
                        cal.drift_angle_yaw_label.setText(f"{vq:.1f}°")
                except Exception:
                    pass
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

        # Mirror value to calibration panel so get_prefs() returns updated values for persistence
        try:
            cal = getattr(self, 'calibration_panel', None)
            if cal is not None:
                try:
                    cal.drift_angle_pitch_value = vq
                    if hasattr(cal, 'drift_angle_pitch_label') and cal.drift_angle_pitch_label:
                        cal.drift_angle_pitch_label.setText(f"{vq:.1f}°")
                except Exception:
                    pass
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

        # Mirror value to calibration panel so get_prefs() returns updated values for persistence
        try:
            cal = getattr(self, 'calibration_panel', None)
            if cal is not None:
                try:
                    cal.drift_angle_roll_value = vq
                    if hasattr(cal, 'drift_angle_roll_label') and cal.drift_angle_roll_label:
                        cal.drift_angle_roll_label.setText(f"{vq:.1f}°")
                except Exception:
                    pass
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
            from util.error_utils import safe_queue_put
            from config.config import QUEUE_PUT_TIMEOUT
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
        try:
            from util.error_utils import safe_queue_put
            from config.config import QUEUE_PUT_TIMEOUT
            if self.control_queue:
                            safe_queue_put(self.control_queue, 'reset_orientation', timeout=QUEUE_PUT_TIMEOUT)
        except Exception:
            pass

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
            from util.error_utils import safe_queue_put
            from config.config import QUEUE_PUT_TIMEOUT
            if self.control_queue:
                safe_queue_put(self.control_queue, cmd, timeout=QUEUE_PUT_TIMEOUT)
        except Exception:
            pass

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
            from util.error_utils import safe_queue_put
            from config.config import QUEUE_PUT_TIMEOUT
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
            from util.error_utils import safe_queue_put
            from config.config import QUEUE_PUT_TIMEOUT
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
                        print(f"[OrientationPanel] input_captured: {key} ({disp})")
                    elif tag == 'shortcut_pressed':
                        key = resp[1] if len(resp) > 1 else None
                        action = resp[2] if len(resp) > 2 else None
                        print(f"[OrientationPanel] shortcut_pressed: {key} -> {action}")
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
                        print(f"[OrientationPanel] shortcut_released: {key} -> {action}")
                        if action == 'disengage_drift' and hasattr(self, '_on_disengage_released'):
                            try:
                                self._on_disengage_released()
                            except Exception:
                                pass
                except Exception as e:
                    print(f"[OrientationPanel] Error handling input response: {e}")
        except Exception:
            pass

    def connect_calibration_panel(self, calibration_panel):
        """
        Connect to the calibration panel for visualization updates.
        
        Args:
            calibration_panel: CalibrationPanelQt instance with visualization widget
        """
        # Store reference to calibration panel and link back so calibration
        # panel can forward visualization updates to this panel's widget.
        self.calibration_panel = calibration_panel
        try:
            calibration_panel.orientation_panel = self
        except Exception:
            pass

        # If the calibration panel was expecting a disengage button, wire it up
        try:
            if hasattr(self, 'disengage_btn') and self.disengage_btn and hasattr(calibration_panel, '_on_disengage_pressed'):
                calibration_panel.disengage_btn = self.disengage_btn
                # Connect the signals to the calibration panel handlers
                self.disengage_btn.pressed.connect(calibration_panel._on_disengage_pressed)
                self.disengage_btn.released.connect(calibration_panel._on_disengage_released)
        except Exception:
            pass

        # Wire up reset button if present on this panel and calibration expects it
        try:
            if hasattr(self, 'reset_button') and self.reset_button:
                # Give calibration panel a reference to the button for its UI, but
                # ensure the actual click handler is the orientation panel's
                # _on_reset_orientation (which issues a 'reset' command to the
                # control queue) so calibration is not cleared inadvertently.
                calibration_panel.reset_button = self.reset_button
                try:
                    if hasattr(self, '_on_reset_orientation'):
                        # Connect the button to the orientation panel's handler.
                        # Avoid binding calibration_panel._on_reset_orientation here
                        # because that implementation may clear calibration state.
                        self.reset_button.clicked.connect(self._on_reset_orientation)
                except Exception:
                    pass

                # If calibration panel had stored shortcut info before wiring, update button text now
                try:
                    rs = getattr(calibration_panel, 'reset_shortcut', None)
                    rs_disp = getattr(calibration_panel, 'reset_shortcut_display_name', None)
                    if rs and rs != 'None':
                        # Use panel method to update display and (re)register shortcut with input worker
                        calibration_panel._set_reset_shortcut(rs, rs_disp)
                    else:
                        # Ensure button text is default
                        try:
                            self.reset_button.setText("Reset Orientation")
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # Wire up gyro calibration status label and recalibrate button if present
        try:
            if hasattr(self, 'calib_status_label') and self.calib_status_label:
                calibration_panel.calib_status_label = self.calib_status_label
            if hasattr(self, 'recal_button') and self.recal_button:
                calibration_panel.recal_button = self.recal_button
                if hasattr(calibration_panel, '_on_recalibrate'):
                    self.recal_button.clicked.connect(calibration_panel._on_recalibrate)
        except Exception:
            pass

        # Ensure calibration panel's drift setters and stored values use this panel's widgets
        try:
            # Redirect calibration panel's setter methods to this panel so saved prefs
            # and initial load will affect the orientation panel UI and state.
            calibration_panel.set_drift_angle_yaw = self.set_drift_angle_yaw
            calibration_panel.set_drift_angle_pitch = self.set_drift_angle_pitch
            calibration_panel.set_drift_angle_roll = self.set_drift_angle_roll

            # Mirror current drift values into calibration panel so get_prefs() returns correct values
            try:
                calibration_panel.drift_angle_yaw_value = getattr(self, 'drift_angle_yaw_value', getattr(calibration_panel, 'drift_angle_yaw_value', 5.0))
                calibration_panel.drift_angle_pitch_value = getattr(self, 'drift_angle_pitch_value', getattr(calibration_panel, 'drift_angle_pitch_value', 5.0))
                calibration_panel.drift_angle_roll_value = getattr(self, 'drift_angle_roll_value', getattr(calibration_panel, 'drift_angle_roll_value', 5.0))
            except Exception:
                pass
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
                preferences_panel.calibration_panel = self
            except Exception:
                pass

            # Restore shortcuts from preferences to this panel (if present)
            try:
                rs = getattr(preferences_panel, 'reset_shortcut', 'None')
                rs_disp = getattr(preferences_panel, 'reset_shortcut_display_name', rs)
                if rs and rs != 'None':
                    try:
                        self._set_reset_shortcut(rs, rs_disp)
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                ds = getattr(preferences_panel, 'disengage_shortcut', 'None')
                ds_disp = getattr(preferences_panel, 'disengage_shortcut_display_name', ds)
                if ds and ds != 'None':
                    try:
                        self._set_disengage_shortcut(ds, ds_disp)
                    except Exception:
                        pass
            except Exception:
                pass

            # Sync disengage toggle mode
            try:
                dtm = getattr(preferences_panel, 'disengage_toggle_mode', False)
                self.set_disengage_toggle_mode(bool(dtm))
                if hasattr(preferences_panel, 'disengage_toggle_checkbox'):
                    try:
                        preferences_panel.disengage_toggle_checkbox.setChecked(self.disengage_toggle_mode)
                    except Exception:
                        pass
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
