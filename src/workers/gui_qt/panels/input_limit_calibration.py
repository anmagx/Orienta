"""Borderless screen-edge calibration for native yaw and pitch input limits."""

import math
import logging

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.config.config import CUSTOM_INPUT_LIMIT_DEG_RANGE


CALIBRATION_STEPS = (
    ("left", "left"),
    ("right", "right"),
    ("top", "upper"),
    ("bottom", "lower"),
)


def symmetric_limits_from_samples(samples):
    """Return conservative symmetric yaw/pitch limits from four edge samples."""
    expected = ("left", "right", "top", "bottom")
    if not isinstance(samples, dict) or any(key not in samples for key in expected):
        raise ValueError("Calibration requires left, right, top, and bottom samples")

    magnitudes = {}
    for key in expected:
        try:
            value = abs(float(samples[key]))
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Calibration sample {key} must be a finite angle") from error
        if not math.isfinite(value):
            raise ValueError(f"Calibration sample {key} must be a finite angle")
        if not CUSTOM_INPUT_LIMIT_DEG_RANGE[0] <= value <= CUSTOM_INPUT_LIMIT_DEG_RANGE[1]:
            raise ValueError(
                f"Calibration sample {key} must be between "
                f"{CUSTOM_INPUT_LIMIT_DEG_RANGE[0]} and "
                f"{CUSTOM_INPUT_LIMIT_DEG_RANGE[1]} degrees"
            )
        magnitudes[key] = value

    return (
        min(magnitudes["left"], magnitudes["right"]),
        min(magnitudes["top"], magnitudes["bottom"]),
    )


class _BorderlessPopup(QWidget):
    def __init__(self):
        flags = Qt.Window | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setObjectName("visualizationPopupWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        app = QApplication.instance()
        if app is not None:
            if app.styleSheet():
                self.setStyleSheet(app.styleSheet())
            self.setPalette(app.palette())
        self.setAutoFillBackground(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.container = QFrame(self)
        self.container.setObjectName("visualizationPopup")
        self.container.setAttribute(Qt.WA_StyledBackground, True)
        self.container.setStyleSheet(
            "border-radius: 10px; border: none;"
        )
        outer.addWidget(self.container)
        self.content_layout = QVBoxLayout(self.container)
        self.content_layout.setContentsMargins(16, 16, 16, 16)

    def center_on_screen(self, screen_geometry):
        self.move(
            screen_geometry.center().x() - self.width() // 2,
            screen_geometry.center().y() - self.height() // 2,
        )


class _CalibrationIntro(_BorderlessPopup):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Input magnitude calibration")
        self.setFixedSize(380, 380)
        layout = self.content_layout
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(QLabel("Automatic input magnitude calibration"))
        instructions = QLabel(
            "A short calibration run will ask you to turn toward the left, "
            "right, upper, and lower edges of this screen. Keep your eyes on "
            "the center target. At each edge, click Set when you can still "
            "see the target without straining."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.start_button = QPushButton("Start calibration")
        self.cancel_button = QPushButton("Cancel")
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)


class _TargetMark(QWidget):
    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        radius = min(self.width(), self.height()) // 2 - 5
        painter.setPen(QPen(QColor(220, 45, 45), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, radius, radius)
        arm = radius * 0.55
        painter.drawLine(
            round(center.x() - arm),
            center.y(),
            round(center.x() + arm),
            center.y(),
        )
        painter.drawLine(
            center.x(),
            round(center.y() - arm),
            center.x(),
            round(center.y() + arm),
        )


class _CenterTarget(_BorderlessPopup):
    def __init__(self):
        super().__init__()
        self.setFixedSize(100, 100)
        self.setToolTip("Keep your eyes on this target during calibration.")
        self.content_layout.setContentsMargins(6, 6, 6, 6)
        self.target_mark = _TargetMark(self.container)
        self.content_layout.addWidget(self.target_mark)


class _EdgePrompt(_BorderlessPopup):
    def __init__(self, direction, step_number):
        super().__init__()
        display_direction = {"top": "upper", "bottom": "lower"}.get(
            direction,
            direction,
        )
        self.setWindowTitle(f"{display_direction.title()} edge calibration")
        self.setFixedSize(360, 360)
        layout = self.content_layout
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(QLabel(f"Turn toward the {display_direction} edge"))
        instructions = QLabel(
            "Turn your head to the edge of the screen, but keep your eyes on "
            "the center target. Click Set when you can still see it without "
            "straining."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.status_label = QLabel(f"Step {step_number} of {len(CALIBRATION_STEPS)}")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.set_button = QPushButton("Set")
        self.cancel_button = QPushButton("Cancel")
        buttons.addWidget(self.set_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def show_status(self, message):
        self.status_label.setText(message)


class InputMagnitudeCalibration:
    """Run four screen-edge captures and return symmetric yaw/pitch limits."""

    def __init__(self, current_pose_provider, on_complete, log_message=None):
        self._current_pose_provider = current_pose_provider
        self._on_complete = on_complete
        self._log_message = log_message or logging.getLogger(__name__).info
        self._screen_geometry = None
        self._intro = None
        self._target = None
        self._prompt = None
        self._step_index = 0
        self._samples = {}
        self._active = False

    @property
    def active(self):
        return self._active

    def start(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("No display is available for input calibration")
        self._screen_geometry = screen.availableGeometry()
        self._active = True
        self._intro = _CalibrationIntro()
        self._intro.start_button.clicked.connect(self._begin)
        self._intro.cancel_button.clicked.connect(self.cancel)
        self._intro.center_on_screen(self._screen_geometry)
        self._intro.show()
        self._intro.raise_()

    def cancel(self):
        if not self._active:
            return
        self._close_popups()
        self._active = False
        self._log_message("Automatic input magnitude calibration cancelled")

    def _begin(self):
        if not self._active:
            return
        self._close_popup("_intro")
        self._target = _CenterTarget()
        self._target.center_on_screen(self._screen_geometry)
        self._target.show()
        self._target.raise_()
        self._step_index = 0
        self._samples = {}
        self._show_current_edge_prompt()

    def _show_current_edge_prompt(self):
        direction, edge = CALIBRATION_STEPS[self._step_index]
        self._prompt = _EdgePrompt(direction, self._step_index + 1)
        self._prompt.set_button.clicked.connect(self._capture_current_edge)
        self._prompt.cancel_button.clicked.connect(self.cancel)
        self._position_edge_prompt(edge)
        self._prompt.show()
        self._prompt.raise_()

    def _position_edge_prompt(self, edge):
        geometry = self._screen_geometry
        prompt = self._prompt
        inset = 12
        if edge in ("left", "right"):
            y = geometry.center().y() - prompt.height() // 2
            x = (
                geometry.left() + inset
                if edge == "left"
                else geometry.right() - prompt.width() + 1 - inset
            )
        else:
            x = geometry.center().x() - prompt.width() // 2
            y = (
                geometry.top() + inset
                if edge == "top"
                else geometry.bottom() - prompt.height() + 1 - inset
            )
        x = max(geometry.left(), min(geometry.right() - prompt.width() + 1, x))
        y = max(geometry.top(), min(geometry.bottom() - prompt.height() + 1, y))
        prompt.move(x, y)

    def _capture_current_edge(self):
        if not self._active or self._prompt is None:
            return
        pose = self._current_pose_provider()
        if pose is None or len(pose) < 3:
            self._prompt.show_status(
                "No recent tracking data. Start tracking and try again."
            )
            return

        direction = CALIBRATION_STEPS[self._step_index][0]
        axis_index = 0 if direction in ("left", "right") else 1
        try:
            magnitude = abs(float(pose[axis_index]))
        except (TypeError, ValueError, OverflowError):
            self._prompt.show_status(
                "The current angle is invalid. Check tracking, then click Set again."
            )
            return
        if not math.isfinite(magnitude):
            self._prompt.show_status(
                "The current angle is invalid. Check tracking, then click Set again."
            )
            return
        if not CUSTOM_INPUT_LIMIT_DEG_RANGE[0] <= magnitude <= CUSTOM_INPUT_LIMIT_DEG_RANGE[1]:
            self._prompt.show_status(
                "Turn farther before clicking Set; the detected angle must be "
                f"at least {CUSTOM_INPUT_LIMIT_DEG_RANGE[0]:g}°."
            )
            return

        self._samples[direction] = magnitude
        self._close_popup("_prompt")
        self._step_index += 1
        if self._step_index < len(CALIBRATION_STEPS):
            self._show_current_edge_prompt()
            return

        yaw_limit, pitch_limit = symmetric_limits_from_samples(self._samples)
        self._close_popups()
        self._active = False
        self._on_complete(yaw_limit, pitch_limit)

    def _close_popup(self, attribute):
        popup = getattr(self, attribute)
        if popup is not None:
            popup.close()
            popup.deleteLater()
            setattr(self, attribute, None)

    def _close_popups(self):
        for attribute in ("_prompt", "_intro", "_target"):
            self._close_popup(attribute)
