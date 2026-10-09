"""Orientation visualization widgets used by :mod:`orientation_panel`."""

import math

from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

from .base_panel import LINE_THICKNESS


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
            from src.config.config import VISUALIZATION_RANGE, VISUALIZATION_SIZE
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
        self.held_orientation = None
        self.reset_follow_active = False
        
        # Drift correction status
        self.drift_correction_active = False
        self.drift_angle_yaw = 5.0  # Default yaw drift angle in degrees
        self.drift_angle_pitch = 5.0  # Default pitch drift angle in degrees
        self.drift_angle_roll = 5.0  # Default roll drift angle in degrees
        
        # Axis inversion settings
        self.invert_yaw = False
        self.invert_pitch = False
        self.invert_roll = False
        
        # Widget appearance - use shared line thickness for border
        try:
            self.setStyleSheet(f"background-color: black; border: {LINE_THICKNESS}px solid gray;")
        except Exception:
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

    def set_held_orientation(self, yaw, pitch, roll):
        """Show a separate marker for a held pose while live orientation updates."""
        self.held_orientation = (float(yaw), float(pitch), float(roll))
        self.update()

    def clear_held_orientation(self):
        """Remove the held-pose marker."""
        self.held_orientation = None
        self.update()

    def set_reset_follow_active(self, active):
        """Show or hide the recenter-on-release guide."""
        self.reset_follow_active = bool(active)
        self.update()

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
        
        # Draw the held pose underneath the live marker.
        self._draw_held_orientation_indicator(painter, center_x, center_y, width, height)

        # Draw orientation indicator
        self._draw_orientation_indicator(painter, center_x, center_y, width, height)
        
        # Draw drift correction circle
        self._draw_drift_correction_circle(painter, center_x, center_y)
        self._draw_reset_follow_circle(painter, center_x, center_y)

    def _draw_held_orientation_indicator(self, painter, center_x, center_y, width, height):
        """Draw a live-style held marker with a yellow dotted orientation line."""
        if self.held_orientation is None:
            return

        try:
            from src.config.config import VISUALIZATION_RANGE
            current_range = VISUALIZATION_RANGE
        except Exception:
            current_range = getattr(self, 'range_degrees', 25.0)

        yaw, pitch, roll = self.held_orientation
        yaw_ratio = max(-1.0, min(1.0, yaw / current_range))
        pitch_ratio = max(-1.0, min(1.0, -pitch / current_range))
        indicator_x = center_x + yaw_ratio * (width // 2 - 10)
        indicator_y = center_y + pitch_ratio * (height // 2 - 10)

        roll_rad = math.radians(-roll)
        line_length = 20
        start_x = indicator_x - line_length * math.cos(roll_rad)
        start_y = indicator_y - line_length * math.sin(roll_rad)
        end_x = indicator_x + line_length * math.cos(roll_rad)
        end_y = indicator_y + line_length * math.sin(roll_rad)

        painter.setPen(
            QPen(QColor(255, 255, 0), max(1, int(LINE_THICKNESS * 3)), Qt.DotLine)
        )
        painter.drawLine(int(start_x), int(start_y), int(end_x), int(end_y))
        colors = self._get_theme_colors()
        painter.setPen(QPen(colors['center'], max(1, int(LINE_THICKNESS * 2))))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(indicator_x - 3), int(indicator_y - 3), 6, 6)

    def _draw_coordinate_system(self, painter, center_x, center_y, width, height):
        """
        Draw the coordinate grid and axes.
        """
        colors = self._get_theme_colors()
        
        # Get current range from config (allows dynamic updates)
        try:
            from src.config.config import VISUALIZATION_RANGE
            current_range = VISUALIZATION_RANGE
        except Exception:
            current_range = getattr(self, 'range_degrees', 25.0)

        painter.setPen(QPen(colors['grid'], LINE_THICKNESS))
        
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

        # Axis range labels placed on the axes (top/bottom center and left/right center)
        painter.setPen(QPen(colors['text'], LINE_THICKNESS))
        fm = painter.fontMetrics()
        pad_x = 6
        pad_y = 2

        top_text = f"{current_range:.0f}\u00B0"
        bottom_text = f"{-current_range:.0f}\u00B0"
        left_text = f"{-current_range:.0f}\u00B0"
        right_text = f"{current_range:.0f}\u00B0"

        # Compute text sizes (support older PyQt versions)
        try:
            top_w = fm.horizontalAdvance(top_text)
            bottom_w = fm.horizontalAdvance(bottom_text)
            left_w = fm.horizontalAdvance(left_text)
            right_w = fm.horizontalAdvance(right_text)
        except AttributeError:
            top_w = fm.width(top_text)
            bottom_w = fm.width(bottom_text)
            left_w = fm.width(left_text)
            right_w = fm.width(right_text)
        txt_h = fm.height()

        # Create label rectangles with padding
        top_rect = QRect(center_x - (top_w // 2) - pad_x, 5, top_w + 2 * pad_x, txt_h + 2 * pad_y)
        bottom_rect = QRect(center_x - (bottom_w // 2) - pad_x, height - (txt_h + 2 * pad_y) - 5, bottom_w + 2 * pad_x, txt_h + 2 * pad_y)
        left_rect = QRect(5, center_y - (txt_h // 2) - pad_y, left_w + 2 * pad_x, txt_h + 2 * pad_y)
        right_rect = QRect(width - (right_w + 2 * pad_x) - 5, center_y - (txt_h // 2) - pad_y, right_w + 2 * pad_x, txt_h + 2 * pad_y)

        # Clamp rectangles within widget bounds
        def _clamp_rect(r):
            x = max(5, r.x())
            y = max(5, r.y())
            w_ = min(r.width(), width - 10)
            h_ = min(r.height(), height - 10)
            # Ensure right/bottom are within bounds
            if x + w_ > width - 5:
                x = width - 5 - w_
            if y + h_ > height - 5:
                y = height - 5 - h_
            return QRect(x, y, w_, h_)

        top_rect = _clamp_rect(top_rect)
        bottom_rect = _clamp_rect(bottom_rect)
        left_rect = _clamp_rect(left_rect)
        right_rect = _clamp_rect(right_rect)

        # Shorten main axes slightly so centered labels fit cleanly
        label_margin = max(6, pad_x + 2)  # vertical margin for top/bottom labels
        # Use a slightly smaller horizontal margin so the horizontal axis has less padding
        horiz_margin = max(4, label_margin - 3)

        top_padding = int(top_rect.bottom() + label_margin)
        bottom_padding = int(bottom_rect.top() - label_margin)
        left_padding = int(left_rect.right() + horiz_margin)
        right_padding = int(right_rect.left() - horiz_margin)

        # Safety clamp if widget is too small or paddings overlap
        if top_padding >= bottom_padding - 2:
            top_padding = 5 + label_margin
            bottom_padding = height - 5 - label_margin
        if left_padding >= right_padding - 2:
            left_padding = 5 + horiz_margin
            right_padding = width - 5 - label_margin

        # Draw shortened center axes (leave space for centered labels)
        painter.setPen(QPen(colors['axis'], max(1, int(LINE_THICKNESS * 2))))
        painter.drawLine(center_x, top_padding, center_x, bottom_padding)  # Vertical axis
        painter.drawLine(left_padding, center_y, right_padding, center_y)   # Horizontal axis

        # Draw labels centered on the axes
        painter.setPen(QPen(colors['text'], LINE_THICKNESS))
        painter.drawText(QRect(center_x - top_w // 2, top_rect.y(), top_w, top_rect.height()), int(Qt.AlignCenter), top_text)
        painter.drawText(QRect(center_x - bottom_w // 2, bottom_rect.y(), bottom_w, bottom_rect.height()), int(Qt.AlignCenter), bottom_text)
        painter.drawText(QRect(left_rect.x(), center_y - txt_h // 2, left_rect.width(), left_rect.height()), int(Qt.AlignVCenter | Qt.AlignLeft), left_text)
        painter.drawText(QRect(right_rect.x(), center_y - txt_h // 2, right_rect.width(), right_rect.height()), int(Qt.AlignVCenter | Qt.AlignRight), right_text)

    def _draw_orientation_indicator(self, painter, center_x, center_y, width, height):
        """
        Draw the orientation indicator line with color based on threshold status.
        Blue when all angles are within their respective thresholds, green when outside.
        """
        colors = self._get_theme_colors()
        
        # Get current range from config (allows dynamic updates)
        try:
            from src.config.config import VISUALIZATION_RANGE
            current_range = VISUALIZATION_RANGE
        except Exception:
            current_range = getattr(self, 'range_degrees', 25.0)
        
        # Data is already inverted by fusion worker, so just use it directly.
        # Screen Y grows downwards, so pitch/roll are negated to match the
        # physical sense of the reported angles.
        yaw_ratio = max(-1.0, min(1.0, self.yaw / current_range))
        pitch_ratio = max(-1.0, min(1.0, -self.pitch / current_range))
        
        indicator_x = center_x + yaw_ratio * (width // 2 - 10)
        indicator_y = center_y + pitch_ratio * (height // 2 - 10)
        
        # Calculate line endpoints based on roll angle
        roll_rad = math.radians(-self.roll)
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
        painter.setPen(QPen(line_color, max(1, int(LINE_THICKNESS * 3))))
        painter.drawLine(int(start_x), int(start_y), int(end_x), int(end_y))
        
        # Draw center dot
        painter.setPen(QPen(colors['center'], max(1, int(LINE_THICKNESS * 2))))
        painter.drawEllipse(int(indicator_x - 3), int(indicator_y - 3), 6, 6)

    def _draw_drift_correction_circle(self, painter, center_x, center_y):
        """
        Draw the drift correction status ellipse at the center.
        Ellipse size corresponds to the yaw and pitch drift correction angles scaled to coordinate system.
        Forms a circle when yaw and pitch angles are equal, ellipse when different.
        Red outline at all times, blue filled when drift correction is active.
        """
        ellipse_rect = self._get_drift_correction_ellipse_rect(center_x, center_y)

        if self.drift_correction_active:
            # Active: Blue filled with red outline
            painter.setBrush(QColor(100, 150, 255, 100))  # Semi-transparent blue fill
            painter.setPen(QPen(QColor(255, 50, 50), max(1, int(LINE_THICKNESS * 2))))  # Red outline
            painter.drawEllipse(ellipse_rect)
        else:
            # Inactive: Red outline only
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 50, 50), max(1, int(LINE_THICKNESS * 2))))  # Red outline
            painter.drawEllipse(ellipse_rect)

    def _draw_reset_follow_circle(self, painter, center_x, center_y):
        """Outline the live orientation gadget while Reset Orientation is held."""
        if not self.reset_follow_active:
            return

        try:
            from src.config.config import VISUALIZATION_RANGE
            current_range = VISUALIZATION_RANGE
        except Exception:
            current_range = getattr(self, 'range_degrees', 25.0)

        yaw_ratio = max(-1.0, min(1.0, self.yaw / current_range))
        pitch_ratio = max(-1.0, min(1.0, -self.pitch / current_range))
        gadget_x = int(center_x + yaw_ratio * (self.width() // 2 - 10))
        gadget_y = int(center_y + pitch_ratio * (self.height() // 2 - 10))
        ellipse_rect = self._get_drift_correction_ellipse_rect(
            gadget_x, gadget_y
        )
        painter.setBrush(Qt.NoBrush)
        painter.setPen(
            QPen(
                QColor(160, 160, 160),
                max(1, int(LINE_THICKNESS * 2)),
                Qt.DotLine,
            )
        )
        painter.drawEllipse(ellipse_rect)

    def _get_drift_correction_ellipse_rect(self, center_x, center_y):
        """Calculate the guide ellipse using the configured yaw/pitch angles."""
        # Get current range from config (allows dynamic updates)
        try:
            from src.config.config import VISUALIZATION_RANGE
            current_range = VISUALIZATION_RANGE
        except Exception:
            current_range = getattr(self, 'range_degrees', 25.0)

        # Calculate radii based on drift angles using the actual widget size so
        # the ellipse scales consistently with the orientation indicator.
        usable_radius = (min(self.width(), self.height()) // 2) - 10
        if usable_radius <= 0:
            usable_radius = 1
        pixels_per_degree = usable_radius / current_range
        
        # Convert drift angles directly to pixels
        # Yaw maps to horizontal (width), pitch maps to vertical (height)
        ellipse_width_pixels = int(self.drift_angle_yaw * pixels_per_degree * 2)  # Full width
        ellipse_height_pixels = int(self.drift_angle_pitch * pixels_per_degree * 2)  # Full height
        
        # Ensure minimum visibility and maximum size
        ellipse_width_pixels = max(4, min(ellipse_width_pixels, usable_radius * 2))
        ellipse_height_pixels = max(4, min(ellipse_height_pixels, usable_radius * 2))
        
        ellipse_rect_x = center_x - ellipse_width_pixels // 2
        ellipse_rect_y = center_y - ellipse_height_pixels // 2
        return QRect(
            ellipse_rect_x,
            ellipse_rect_y,
            ellipse_width_pixels,
            ellipse_height_pixels,
        )


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

    def refresh_child_geometry(self):
        """Force-update the child's geometry to remain square and centered.

        Useful when the child is reparented back into this container and
        a layout pass hasn't triggered a resize event yet.
        """
        try:
            side = max(0, min(self.width(), self.height()))
            x = (self.width() - side) // 2
            y = (self.height() - side) // 2
            if hasattr(self, '_child') and self._child is not None:
                try:
                    self._child.setGeometry(x, y, side, side)
                except Exception:
                    pass
        except Exception:
            pass