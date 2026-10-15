"""Native FreeTrack and NPClient output settings dialog and curve editor."""

import json
import logging
import math
import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.config.config import (
    CUSTOM_INPUT_LIMIT_DEG_RANGE,
    CUSTOM_INPUT_LIMIT_DEG_STEP,
    INPUT_LIMIT_CALIBRATION_POSE_STALE_S,
    QUEUE_PUT_TIMEOUT,
)
from src.filters import available_filters, default_filter_settings, get_filter_definition
from src.filters.one_euro import ONE_EURO_FILTER_ID
from src.util.error_utils import safe_queue_put
from .input_limit_calibration import InputMagnitudeCalibration
from src.outputs.native_output_processing import (
    AXES,
    CURVE_INPUT_LIMIT_DEG,
    CURVE_OUTPUT_LIMIT_DEG,
    DEFAULT_INPUT_LIMIT_DEG,
    INPUT_LIMIT_CHOICES_DEG,
    ROTATION_OFFSET_LIMIT_DEG,
    TRANSLATION_OFFSET_LIMIT_MM,
    default_native_output_settings,
    default_response_curve,
    normalize_native_output_settings,
    map_response_curve,
    validate_curve,
)

_LEGACY_CURVE_OUTPUT_LIMIT_DEG = 360.0


def _migrate_legacy_curve(points, input_limit_deg):
    """Rescale a previously saved fixed-domain curve to its selected limit."""
    if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= 32:
        raise ValueError("Legacy curve must contain 3 to 32 points")

    legacy = []
    previous_x = None
    for index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"Legacy curve point {index + 1} is malformed")
        x_value, y_value = float(point[0]), float(point[1])
        if not math.isfinite(x_value) or not math.isfinite(y_value):
            raise ValueError("Legacy curve values must be finite")
        if not -CURVE_INPUT_LIMIT_DEG <= x_value <= CURVE_INPUT_LIMIT_DEG:
            raise ValueError("Legacy curve input is out of range")
        if abs(y_value) > _LEGACY_CURVE_OUTPUT_LIMIT_DEG:
            raise ValueError("Legacy curve output is out of range")
        if previous_x is not None and x_value <= previous_x:
            raise ValueError("Legacy curve inputs must be strictly increasing")
        legacy.append((x_value, y_value))
        previous_x = x_value

    if (
        legacy[0][0] == -CURVE_INPUT_LIMIT_DEG
        and legacy[-1][0] == CURVE_INPUT_LIMIT_DEG
    ):
        magnitude_points = [(0.0, 0.0)]
        magnitude_points.extend(
            (x_value, min(CURVE_OUTPUT_LIMIT_DEG, abs(y_value)))
            for x_value, y_value in legacy
            if 0.0 < x_value < CURVE_INPUT_LIMIT_DEG
        )
    elif (
        legacy[0][0] == 0.0
        and legacy[-1][0] == CURVE_INPUT_LIMIT_DEG
    ):
        magnitude_points = [(0.0, 0.0)]
        magnitude_points.extend(
            (x_value, min(CURVE_OUTPUT_LIMIT_DEG, y_value))
            for x_value, y_value in legacy
            if 0.0 < x_value < CURVE_INPUT_LIMIT_DEG
        )
    else:
        raise ValueError("Curve is not in a recognized legacy format")

    magnitude_points.append((CURVE_INPUT_LIMIT_DEG, CURVE_OUTPUT_LIMIT_DEG))
    if len(magnitude_points) == 2:
        for left, right in zip(magnitude_points, magnitude_points[1:]):
            if left[0] <= CURVE_INPUT_LIMIT_DEG / 2.0 <= right[0]:
                fraction = (
                    CURVE_INPUT_LIMIT_DEG / 2.0 - left[0]
                ) / (right[0] - left[0])
                midpoint_output = (
                    left[1] + fraction * (right[1] - left[1])
                )
                magnitude_points.insert(
                    1,
                    (CURVE_INPUT_LIMIT_DEG / 2.0, midpoint_output),
                )
                break

    scale = input_limit_deg / CURVE_INPUT_LIMIT_DEG
    rescaled = [(0.0, 0.0)]
    rescaled.extend(
        (x_value * scale, y_value)
        for x_value, y_value in magnitude_points
        if 0.0 < x_value < CURVE_INPUT_LIMIT_DEG
    )
    rescaled.append((input_limit_deg, CURVE_OUTPUT_LIMIT_DEG))
    return validate_curve(rescaled, input_limit_deg)


def native_output_preferences_from_section(section, log_warning=None):
    """Parse saved scalar preferences, falling back safely per setting."""
    settings = default_native_output_settings()
    if not isinstance(section, dict):
        return settings

    def warn(key, error):
        if log_warning is not None:
            log_warning(f"Ignoring invalid native output preference {key}: {error}")

    input_limits = dict(settings["input_limits"])
    input_limit_custom = dict(settings["input_limit_custom"])
    for axis in AXES:
        limit_key = f"input_limit_{axis}_deg"
        custom_key = f"input_limit_custom_{axis}"
        raw_limit = section.get(limit_key)
        raw_custom = section.get(custom_key)
        if raw_limit is None and raw_custom is None:
            continue
        try:
            input_limit = (
                float(raw_limit)
                if raw_limit is not None
                else input_limits[axis]
            )
        except (TypeError, ValueError, OverflowError) as error:
            warn(limit_key, error)
            continue

        custom_value = None
        if raw_custom is not None:
            normalized_custom = str(raw_custom).strip().lower()
            if normalized_custom in ("true", "1", "yes", "on"):
                custom_value = True
            elif normalized_custom in ("false", "0", "no", "off"):
                custom_value = False
            else:
                warn(custom_key, "expected true or false")
        if custom_value is None:
            custom_value = input_limit not in INPUT_LIMIT_CHOICES_DEG

        try:
            if custom_value:
                if not (
                    CUSTOM_INPUT_LIMIT_DEG_RANGE[0]
                    <= input_limit
                    <= CUSTOM_INPUT_LIMIT_DEG_RANGE[1]
                ):
                    raise ValueError("custom input limit is out of range")
            elif input_limit not in INPUT_LIMIT_CHOICES_DEG:
                raise ValueError("unsupported input limit")
        except (TypeError, ValueError, OverflowError) as error:
            warn(limit_key, error)
            continue

        input_limits[axis] = input_limit
        input_limit_custom[axis] = custom_value
        settings["input_limits"] = dict(input_limits)
        settings["input_limit_custom"] = dict(input_limit_custom)

    encoded_curves = section.get("curves")
    if encoded_curves:
        try:
            curves = json.loads(encoded_curves)
            normalized_curves = {}
            for axis in AXES:
                limit = settings["input_limits"][axis]
                curve = curves.get(axis, default_response_curve(limit))
                try:
                    normalized_curves[axis] = validate_curve(curve, limit)
                except ValueError:
                    normalized_curves[axis] = _migrate_legacy_curve(curve, limit)
            settings["curves"] = normalized_curves
            settings = normalize_native_output_settings(settings)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
            warn("curves", error)
            settings["curves"] = {
                axis: default_response_curve(settings["input_limits"][axis])
                for axis in AXES
            }
    else:
        settings["curves"] = {
            axis: default_response_curve(settings["input_limits"][axis])
            for axis in AXES
        }

    enabled_value = section.get("filter_enabled")
    if enabled_value is not None:
        normalized_enabled = str(enabled_value).strip().lower()
        if normalized_enabled in ("true", "1", "yes", "on"):
            settings["filter_enabled"] = True
        elif normalized_enabled in ("false", "0", "no", "off"):
            settings["filter_enabled"] = False
        else:
            warn("filter_enabled", "expected true or false")

    filter_type = section.get("filter_type", settings["filter_type"])
    try:
        get_filter_definition(filter_type)
        settings["filter_type"] = filter_type
    except (TypeError, ValueError) as error:
        warn("filter_type", error)

    serialized_filter_settings = section.get("filter_settings")
    if serialized_filter_settings:
        try:
            filter_settings = json.loads(serialized_filter_settings)
            if not isinstance(filter_settings, dict):
                raise ValueError("filter settings must be a mapping")
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            warn("filter_settings", error)
            filter_settings = {}
    else:
        filter_settings = {}

    legacy_one_euro_keys = {
        "filter_min_cutoff_hz": "min_cutoff_hz",
        "filter_beta": "beta",
        "filter_derivative_cutoff_hz": "derivative_cutoff_hz",
    }
    has_legacy_one_euro_values = any(
        key in section for key in legacy_one_euro_keys
    )
    if (
        has_legacy_one_euro_values
        and not section.get("filter_type")
        and not serialized_filter_settings
    ):
        settings["filter_type"] = ONE_EURO_FILTER_ID
    if ONE_EURO_FILTER_ID not in filter_settings and has_legacy_one_euro_values:
        one_euro_defaults = get_filter_definition(
            ONE_EURO_FILTER_ID
        ).default_settings()
        filter_settings[ONE_EURO_FILTER_ID] = {
            parameter: section.get(legacy_key, one_euro_defaults[parameter])
            for legacy_key, parameter in legacy_one_euro_keys.items()
        }

    normalized_filter_settings = {}
    for definition in available_filters():
        raw_settings = filter_settings.get(
            definition.identifier,
            definition.default_settings(),
        )
        try:
            normalized_filter_settings[definition.identifier] = (
                definition.normalize_settings(raw_settings)
            )
        except (TypeError, ValueError, OverflowError) as error:
            warn(f"filter_settings.{definition.identifier}", error)
            normalized_filter_settings[definition.identifier] = (
                definition.default_settings()
            )
    unknown_filters = set(filter_settings) - {
        definition.identifier for definition in available_filters()
    }
    for identifier in sorted(unknown_filters):
        warn(
            f"filter_settings.{identifier}",
            "filter is not installed in this version",
        )

    settings["filter_settings"] = normalized_filter_settings
    try:
        settings["filter_type"] = get_filter_definition(
            settings["filter_type"]
        ).identifier
    except ValueError:
        settings["filter_type"] = default_filter_settings()["filter_type"]

    offsets = settings["offsets"]
    for key in offsets:
        preference_key = f"offset_{key}"
        if preference_key not in section:
            continue
        try:
            candidate = dict(settings)
            candidate_offsets = dict(offsets)
            candidate_offsets[key] = float(section[preference_key])
            candidate["offsets"] = candidate_offsets
            settings = normalize_native_output_settings(candidate)
            offsets = settings["offsets"]
        except (TypeError, ValueError, OverflowError) as error:
            warn(preference_key, error)

    return normalize_native_output_settings(settings)


def native_output_section_from_settings(settings):
    """Serialize validated settings into ConfigParser-compatible strings."""
    normalized = normalize_native_output_settings(settings)
    section = {
        "curves": json.dumps(normalized["curves"], separators=(",", ":")),
        "filter_type": normalized["filter_type"],
        "filter_enabled": str(normalized["filter_enabled"]).lower(),
        "filter_settings": json.dumps(
            normalized["filter_settings"],
            separators=(",", ":"),
        ),
    }
    section.update({
        f"offset_{key}": str(value)
        for key, value in normalized["offsets"].items()
    })
    section.update({
        f"input_limit_{axis}_deg": str(int(value))
        for axis, value in normalized["input_limits"].items()
        if not normalized["input_limit_custom"][axis]
    })
    section.update({
        f"input_limit_{axis}_deg": str(value)
        for axis, value in normalized["input_limits"].items()
        if normalized["input_limit_custom"][axis]
    })
    section.update({
        f"input_limit_custom_{axis}": str(value).lower()
        for axis, value in normalized["input_limit_custom"].items()
    })
    return section


class ResponseCurveWidget(QWidget):
    """Small editable, smoothly interpolated response curve."""

    curve_changed = pyqtSignal(object)

    def __init__(self, axis, points=None, parent=None,
                 input_limit_deg=DEFAULT_INPUT_LIMIT_DEG):
        super().__init__(parent)
        self.axis = axis
        self.setObjectName("nativeOutputCurve")
        self.setAutoFillBackground(True)
        self.input_limit_deg = float(input_limit_deg)
        self.points = validate_curve(
            points or default_response_curve(self.input_limit_deg),
            self.input_limit_deg,
        )
        self.current_input = None
        self._drag_index = None
        self._hover_index = None
        self.setMouseTracking(True)
        self.setMinimumSize(360, 240)
        self.setToolTip(
            "Double-click to add a control point. Drag points to edit them. "
            "Hover over a point to see its values. Right-click an interior "
            "point to remove it. The curve spans the selected input limit and "
            "up to 180 degrees of output, symmetrically for both signs."
        )

    def set_points(self, points):
        self.points = validate_curve(points, self.input_limit_deg)
        self._hover_index = None
        self.update()

    def set_current_input(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            self.current_input = None
        else:
            self.current_input = value if math.isfinite(value) else None
        self.update()

    def set_input_limit(self, value, rescale=False):
        input_limit_deg = float(value)
        if input_limit_deg == self.input_limit_deg:
            return
        if rescale:
            scale = input_limit_deg / self.input_limit_deg
            points = [(0.0, 0.0)]
            points.extend(
                (x_value * scale, y_value)
                for x_value, y_value in self.points[1:-1]
            )
            points.append((input_limit_deg, self.points[-1][1]))
            self.points = validate_curve(points, input_limit_deg)
        self.input_limit_deg = input_limit_deg
        self.update()

    def _plot_rect(self):
        return self.rect().adjusted(46, 14, -18, -34)

    def _to_position(self, x_value, y_value):
        rect = self._plot_rect()
        x = rect.left() + (
            x_value / self.input_limit_deg
        ) * rect.width()
        y = rect.bottom() - (
            y_value / CURVE_OUTPUT_LIMIT_DEG
        ) * rect.height()
        return x, y

    def _from_position(self, position):
        rect = self._plot_rect()
        x_fraction = (position.x() - rect.left()) / max(1.0, rect.width())
        y_fraction = (rect.bottom() - position.y()) / max(1.0, rect.height())
        x_value = x_fraction * self.input_limit_deg
        y_value = y_fraction * CURVE_OUTPUT_LIMIT_DEG
        return (
            max(0.0, min(self.input_limit_deg, x_value)),
            max(0.0, min(CURVE_OUTPUT_LIMIT_DEG, y_value)),
        )

    def _point_at(self, position):
        for index, (x_value, y_value) in enumerate(self.points):
            point_x, point_y = self._to_position(x_value, y_value)
            if math.hypot(position.x() - point_x, position.y() - point_y) <= 12:
                return index
        return None

    def _set_point(self, index, position):
        x_value, y_value = self._from_position(position)
        points = list(self.points)
        if index == 0:
            x_value, y_value = 0.0, 0.0
        elif index == len(points) - 1:
            x_value = self.input_limit_deg
            y_value = round(y_value, 1)
        else:
            x_value = max(points[index - 1][0] + 0.1, x_value)
            x_value = min(points[index + 1][0] - 0.1, x_value)
            y_value = round(y_value, 1)
        points[index] = (x_value, y_value)
        self.points = validate_curve(points, self.input_limit_deg)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            index = self._point_at(event.pos())
            if (
                len(self.points) > 3
                and index not in (None, 0, len(self.points) - 1)
            ):
                points = list(self.points)
                points.pop(index)
                self.points = validate_curve(points, self.input_limit_deg)
                self._hover_index = None
                QToolTip.hideText()
                self.curve_changed.emit(self.points)
                self.update()
            return
        if event.button() == Qt.LeftButton:
            self._drag_index = self._point_at(event.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_index is not None:
            self._set_point(self._drag_index, event.pos())
            self._hover_index = self._drag_index
            QToolTip.showText(
                event.globalPos(),
                self._point_tooltip(self._drag_index),
                self,
            )
        else:
            index = self._point_at(event.pos())
            if index != self._hover_index:
                self._hover_index = index
                if index is None:
                    QToolTip.hideText()
                else:
                    QToolTip.showText(
                        event.globalPos(),
                        self._point_tooltip(index),
                        self,
                    )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._drag_index is None:
            self._hover_index = None
            QToolTip.hideText()
        super().leaveEvent(event)

    def _point_tooltip(self, index):
        input_value, output_value = self.points[index]
        return (
            f"Input magnitude: {input_value:.1f}°\n"
            f"Output magnitude: {output_value:.1f}°"
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_index is not None:
            self._drag_index = None
            self.curve_changed.emit(self.points)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._plot_rect().contains(event.pos()):
            x_value, y_value = self._from_position(event.pos())
            if len(self.points) < 32 and x_value >= 1.0 and all(
                abs(x_value - point[0]) >= 1.0 for point in self.points
            ):
                points = list(self.points)
                points.append((x_value, y_value))
                points.sort(key=lambda point: point[0])
                self.points = validate_curve(points, self.input_limit_deg)
                self.curve_changed.emit(self.points)
                self.update()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.palette().window())
        plot = self._plot_rect()

        grid_pen = QPen(self.palette().mid().color())
        grid_pen.setStyle(Qt.DotLine)
        painter.setPen(grid_pen)
        for index in range(5):
            value = self.input_limit_deg * index / 4.0
            x, _ = self._to_position(value, 0)
            painter.drawLine(round(x), plot.top(), round(x), plot.bottom())
            painter.drawText(
                round(x) - 22,
                plot.bottom() + 2,
                44,
                18,
                Qt.AlignHCenter,
                f"{value:g}",
            )
        for value in (0, 45, 90, 135, 180):
            _, y = self._to_position(0, value)
            painter.drawLine(plot.left(), round(y), plot.right(), round(y))
            painter.drawText(
                0,
                round(y) - 9,
                42,
                18,
                Qt.AlignRight | Qt.AlignVCenter,
                f"{value:g}",
            )

        painter.setPen(self.palette().text().color())
        painter.drawRect(plot)
        painter.drawText(0, 12, self.width(), 18, Qt.AlignHCenter, f"{self.axis.title()} response")
        painter.drawText(0, self.height() - 22, self.width(), 18, Qt.AlignHCenter, "Input magnitude (degrees)")
        painter.save()
        painter.translate(13, plot.center().y())
        painter.rotate(-90)
        painter.drawText(
            -(plot.height() // 2),
            -4,
            plot.height(),
            18,
            Qt.AlignHCenter,
            "Output magnitude (degrees)",
        )
        painter.restore()

        curve_path = QPainterPath()
        start_x, start_y = self._to_position(*self.points[0])
        curve_path.moveTo(start_x, start_y)
        sample_count = max(64, plot.width())
        for index in range(1, sample_count + 1):
            input_value = self.input_limit_deg * index / sample_count
            output_value = map_response_curve(
                self.points,
                input_value,
                self.input_limit_deg,
            )
            x, y = self._to_position(input_value, output_value)
            curve_path.lineTo(x, y)
        painter.setPen(QPen(QColor(40, 135, 220), 2))
        painter.drawPath(curve_path)

        for point in self.points:
            x, y = self._to_position(*point)
            painter.setBrush(QColor(40, 135, 220))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(round(x) - 5, round(y) - 5, 10, 10)

        if self.current_input is not None:
            x, _ = self._to_position(
                min(self.input_limit_deg, abs(self.current_input)),
                0,
            )
            painter.setPen(QPen(QColor(220, 110, 35), 1, Qt.DashLine))
            painter.drawLine(round(x), plot.top(), round(x), plot.bottom())


class NativeOutputSettingsDialog(QDialog):
    """Own the native-output settings window, UI, and preference conversion."""

    preferences_changed = pyqtSignal()

    def __init__(self, output_control_queue=None, log_message=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setObjectName("nativeOutputSettingsDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setWindowTitle("Native Output Settings")
        self.setMinimumSize(720, 830)
        self.resize(720, 830)
        self.output_control_queue = output_control_queue
        self.log_message = log_message or logging.getLogger(__name__).warning
        self.settings = default_native_output_settings()
        self._loading = True
        self._calibration_controller = None
        self._current_pose = None
        self._current_pose_time = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._apply_pending_settings)
        self.setup_ui()
        self.set_settings(self.settings)
        self._loading = False

    def setup_ui(self):
        root_layout = QVBoxLayout(self)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        description_label = QLabel(
            "These settings apply only to direct FreeTrack and NPClient "
            "output. Rotation values follow Orienta's yaw/pitch/roll axes; "
            "translation offsets use FreeTrack X/Y/Z millimeters. Each curve "
            "also has an independent input-magnitude limit."
        )
        description_label.setWordWrap(True)
        layout.addWidget(description_label)

        curves_group = QGroupBox("Axis response curves")
        curves_layout = QVBoxLayout(curves_group)
        self.curve_tabs = QTabWidget()
        self.curve_widgets = {}
        self.input_limit_controls = {}
        self.input_limit_custom_checks = {}
        self.input_limit_custom_spins = {}
        for axis in AXES:
            widget = ResponseCurveWidget(axis)
            widget.curve_changed.connect(
                lambda points, selected_axis=axis: self._on_curve_changed(
                    selected_axis,
                    points,
                )
            )
            self.curve_widgets[axis] = widget
            axis_page = QWidget()
            axis_layout = QVBoxLayout(axis_page)
            axis_layout.setContentsMargins(0, 0, 0, 0)
            axis_layout.addWidget(widget)
            limit_row = QHBoxLayout()
            limit_row.addWidget(QLabel("Maximum input magnitude:"))
            limit_combo = QComboBox()
            limit_combo.setProperty("nativeAxis", axis)
            for limit in INPUT_LIMIT_CHOICES_DEG:
                limit_combo.addItem(f"{int(limit)}°", limit)
            limit_combo.currentIndexChanged.connect(
                self._on_input_limit_changed
            )
            self.input_limit_controls[axis] = limit_combo
            limit_row.addWidget(limit_combo)
            custom_check = QCheckBox("Custom Value")
            custom_check.toggled.connect(
                lambda checked, selected_axis=axis:
                self._on_input_limit_custom_toggled(selected_axis, checked)
            )
            self.input_limit_custom_checks[axis] = custom_check
            limit_row.addWidget(custom_check)
            custom_spin = self._make_spinbox(
                CUSTOM_INPUT_LIMIT_DEG_RANGE[0],
                CUSTOM_INPUT_LIMIT_DEG_RANGE[1],
                1,
                CUSTOM_INPUT_LIMIT_DEG_STEP,
            )
            custom_spin.setSuffix("°")
            custom_spin.setMaximumWidth(105)
            custom_spin.setEnabled(False)
            custom_spin.valueChanged.connect(
                lambda value, selected_axis=axis:
                self._on_custom_input_limit_changed(selected_axis, value)
            )
            self.input_limit_custom_spins[axis] = custom_spin
            limit_row.addWidget(custom_spin)
            limit_row.addStretch()
            axis_layout.addLayout(limit_row)
            self.curve_tabs.addTab(axis_page, axis.title())
        curves_layout.addWidget(self.curve_tabs)
        curve_buttons = QHBoxLayout()
        self.reset_curve_button = QPushButton("Reset Selected Curve")
        self.reset_curve_button.clicked.connect(self._reset_selected_curve)
        curve_buttons.addWidget(self.reset_curve_button)
        self.auto_calibrate_button = QPushButton("Set automatically")
        self.auto_calibrate_button.clicked.connect(
            self._start_automatic_calibration
        )
        self.auto_calibrate_button.setToolTip(
            "Calibrate yaw and pitch from four comfortable screen-edge turns. "
            "Roll is not changed."
        )
        curve_buttons.addWidget(self.auto_calibrate_button)
        curve_buttons.addStretch()
        curve_help = QLabel(
            "Double-click to add; drag to edit; right-click to remove."
        )
        curve_help.setWordWrap(True)
        curve_help.setMaximumWidth(280)
        curve_buttons.addWidget(curve_help)
        curves_layout.addLayout(curve_buttons)
        layout.addWidget(curves_group)

        filter_group = QGroupBox("Pose filter")
        filter_layout = QVBoxLayout(filter_group)
        self.filter_enabled_checkbox = QCheckBox("Enable pose filtering")
        self.filter_enabled_checkbox.setToolTip(
            "Turn temporal pose filtering on or off. Response curves and "
            "offsets remain active when filtering is disabled."
        )
        self.filter_enabled_checkbox.toggled.connect(self._on_filter_enabled_changed)
        filter_layout.addWidget(self.filter_enabled_checkbox)

        filter_type_row = QHBoxLayout()
        filter_type_row.addWidget(QLabel("Filter:"))
        self.filter_type_combo = QComboBox()
        self.filter_definitions = {
            definition.identifier: definition
            for definition in available_filters()
        }
        for definition in self.filter_definitions.values():
            self.filter_type_combo.addItem(
                definition.label,
                definition.identifier,
            )
        filter_type_row.addWidget(self.filter_type_combo, 1)
        filter_layout.addLayout(filter_type_row)

        self.filter_description_label = QLabel()
        self.filter_description_label.setStyleSheet(
            "color: #666666; font-size: 10px;"
        )
        self.filter_description_label.setWordWrap(True)
        filter_layout.addWidget(self.filter_description_label)

        self.filter_settings_stack = QStackedWidget()
        self.filter_controls_by_type = {}
        self.filter_value_labels_by_type = {}
        self.filter_parameter_annotations_by_type = {}
        self._filter_slider_specs_by_type = {}
        self._filter_settings_by_type = {
            identifier: definition.default_settings()
            for identifier, definition in self.filter_definitions.items()
        }
        for definition in self.filter_definitions.values():
            filter_page, controls, value_labels, annotations, slider_specs = (
                self._build_filter_settings_page(definition)
            )
            self.filter_settings_stack.addWidget(filter_page)
            self.filter_controls_by_type[definition.identifier] = controls
            self.filter_value_labels_by_type[definition.identifier] = value_labels
            self.filter_parameter_annotations_by_type[definition.identifier] = (
                annotations
            )
            self._filter_slider_specs_by_type[definition.identifier] = slider_specs
        filter_layout.addWidget(self.filter_settings_stack)
        self.filter_controls = self.filter_controls_by_type[
            self.filter_type_combo.currentData()
        ]
        self.filter_value_labels = self.filter_value_labels_by_type[
            self.filter_type_combo.currentData()
        ]
        self._filter_slider_specs = self._filter_slider_specs_by_type[
            self.filter_type_combo.currentData()
        ]
        self.filter_type_combo.currentIndexChanged.connect(
            self._on_filter_type_changed
        )
        layout.addWidget(filter_group)

        offsets_group = QGroupBox("Static output offsets")
        offsets_layout = QGridLayout(offsets_group)
        self.offset_controls = {}
        offset_specs = (
            ("yaw_deg", "Yaw (degrees)", ROTATION_OFFSET_LIMIT_DEG),
            ("x_mm", "X (mm)", TRANSLATION_OFFSET_LIMIT_MM),
            ("pitch_deg", "Pitch (degrees)", ROTATION_OFFSET_LIMIT_DEG),
            ("y_mm", "Y (mm)", TRANSLATION_OFFSET_LIMIT_MM),
            ("roll_deg", "Roll (degrees)", ROTATION_OFFSET_LIMIT_DEG),
            ("z_mm", "Z (mm)", TRANSLATION_OFFSET_LIMIT_MM),
        )
        for index, (key, label, limit) in enumerate(offset_specs):
            row, column = divmod(index, 2)
            cell = QVBoxLayout()
            cell.addWidget(QLabel(label))
            spin = self._make_spinbox(-limit, limit, 1)
            spin.valueChanged.connect(self._schedule_apply)
            cell.addWidget(spin)
            offsets_layout.addLayout(cell, row, column)
            self.offset_controls[key] = spin
        layout.addWidget(offsets_group)

        scroll_area.setWidget(content_widget)
        root_layout.addWidget(scroll_area, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel()
        self.reset_all_button = QPushButton("Reset Native Output Settings")
        self.reset_all_button.clicked.connect(self.reset_defaults)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.reset_all_button)
        root_layout.addLayout(footer)

    def _build_filter_settings_page(self, definition):
        page = QWidget()
        layout = QGridLayout(page)
        controls = {}
        value_labels = {}
        annotations = {}
        slider_specs = {}
        settings = self._filter_settings_by_type[definition.identifier]

        for row, parameter in enumerate(definition.parameters):
            control_row = row * 2
            label = QLabel(parameter.label)
            label.setToolTip(parameter.tooltip)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(
                0,
                round((parameter.maximum - parameter.minimum) / parameter.step),
            )
            slider.setToolTip(parameter.tooltip)
            value_label = QLabel()
            value_label.setMinimumWidth(72)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value_label.setToolTip(parameter.tooltip)
            controls[parameter.key] = slider
            value_labels[parameter.key] = value_label
            slider_specs[parameter.key] = parameter
            default_position = round(
                (settings[parameter.key] - parameter.minimum) / parameter.step
            )
            slider.setValue(default_position)
            slider.valueChanged.connect(
                lambda position, filter_id=definition.identifier,
                setting_key=parameter.key:
                self._on_filter_slider_changed(
                    filter_id,
                    setting_key,
                    position,
                )
            )
            annotation = QLabel(parameter.tooltip)
            annotation.setStyleSheet("color: #666666; font-size: 10px;")
            annotation.setWordWrap(True)
            annotations[parameter.key] = annotation
            layout.addWidget(label, control_row, 0)
            layout.addWidget(slider, control_row, 1)
            layout.addWidget(value_label, control_row, 2)
            layout.addWidget(annotation, control_row + 1, 0, 1, 3)
            value_label.setText(self._format_filter_parameter(
                parameter,
                settings[parameter.key],
            ))
        return page, controls, value_labels, annotations, slider_specs

    @staticmethod
    def _make_spinbox(minimum, maximum, decimals, step=None):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(step if step is not None else 1.0)
        return spin

    @staticmethod
    def _format_filter_parameter(parameter, value):
        text = f"{value:.{parameter.decimals}f}"
        return f"{text} {parameter.unit}" if parameter.unit else text

    def _filter_slider_value(self, filter_id, key, position=None):
        parameter = self._filter_slider_specs_by_type[filter_id][key]
        slider = self.filter_controls_by_type[filter_id][key]
        return round(
            parameter.minimum
            + (
                slider.value()
                if position is None
                else position
            )
            * parameter.step,
            parameter.decimals,
        )

    def _update_filter_value_label(self, filter_id, key, position=None):
        parameter = self._filter_slider_specs_by_type[filter_id][key]
        value = self._filter_slider_value(filter_id, key, position)
        self.filter_value_labels_by_type[filter_id][key].setText(
            self._format_filter_parameter(parameter, value)
        )

    def _on_filter_slider_changed(self, filter_id, key, position):
        value = self._filter_slider_value(filter_id, key, position)
        self._filter_settings_by_type[filter_id][key] = value
        self._update_filter_value_label(filter_id, key, position)
        self._schedule_apply()

    def _on_filter_type_changed(self, index):
        if index < 0:
            return
        filter_id = self.filter_type_combo.itemData(index)
        if filter_id not in self.filter_controls_by_type:
            return
        page_index = list(self.filter_controls_by_type).index(filter_id)
        self.filter_settings_stack.setCurrentIndex(page_index)
        self.filter_description_label.setText(
            self.filter_definitions[filter_id].description
        )
        self.filter_controls = self.filter_controls_by_type[filter_id]
        self.filter_value_labels = self.filter_value_labels_by_type[filter_id]
        self._filter_slider_specs = self._filter_slider_specs_by_type[filter_id]
        self._schedule_apply()

    def get_settings(self):
        filter_type = self.filter_type_combo.currentData()
        filter_settings = {
            identifier: dict(values)
            for identifier, values in self._filter_settings_by_type.items()
        }
        for identifier, controls in self.filter_controls_by_type.items():
            filter_settings[identifier] = {
                key: self._filter_slider_value(identifier, key)
                for key in controls
            }
        settings = {
            "curves": {
                axis: [list(point) for point in widget.points]
                for axis, widget in self.curve_widgets.items()
            },
            "input_limits": {
                axis: (
                    self.input_limit_custom_spins[axis].value()
                    if self.input_limit_custom_checks[axis].isChecked()
                    else combo.currentData()
                )
                for axis, combo in self.input_limit_controls.items()
            },
            "input_limit_custom": {
                axis: check.isChecked()
                for axis, check in self.input_limit_custom_checks.items()
            },
            "filter_type": filter_type,
            "filter_enabled": self.filter_enabled_checkbox.isChecked(),
            "filter_settings": filter_settings,
            "offsets": {
                key: spin.value()
                for key, spin in self.offset_controls.items()
            },
        }
        return normalize_native_output_settings(settings)

    def set_settings(self, settings):
        normalized = normalize_native_output_settings(settings)
        self._loading = True
        try:
            self._filter_settings_by_type = {
                identifier: dict(values)
                for identifier, values in normalized["filter_settings"].items()
            }
            filter_type_index = self.filter_type_combo.findData(
                normalized["filter_type"]
            )
            self.filter_type_combo.blockSignals(True)
            self.filter_type_combo.setCurrentIndex(filter_type_index)
            self.filter_type_combo.blockSignals(False)
            filter_page_index = list(self.filter_controls_by_type).index(
                normalized["filter_type"]
            )
            self.filter_settings_stack.setCurrentIndex(filter_page_index)
            self.filter_description_label.setText(
                self.filter_definitions[normalized["filter_type"]].description
            )
            self.filter_controls = self.filter_controls_by_type[
                normalized["filter_type"]
            ]
            self.filter_value_labels = self.filter_value_labels_by_type[
                normalized["filter_type"]
            ]
            self._filter_slider_specs = self._filter_slider_specs_by_type[
                normalized["filter_type"]
            ]
            for axis, widget in self.curve_widgets.items():
                limit_combo = self.input_limit_controls[axis]
                custom_check = self.input_limit_custom_checks[axis]
                custom_spin = self.input_limit_custom_spins[axis]
                custom_value = normalized["input_limit_custom"][axis]
                limit_value = normalized["input_limits"][axis]
                preset = min(
                    INPUT_LIMIT_CHOICES_DEG,
                    key=lambda value: abs(value - limit_value),
                )
                limit_index = limit_combo.findData(preset)
                limit_combo.blockSignals(True)
                limit_combo.setCurrentIndex(limit_index)
                limit_combo.blockSignals(False)
                custom_check.blockSignals(True)
                custom_check.setChecked(custom_value)
                custom_check.blockSignals(False)
                custom_spin.blockSignals(True)
                custom_spin.setValue(limit_value)
                custom_spin.blockSignals(False)
                self._update_input_limit_controls(axis, custom_value)
                widget.set_input_limit(limit_value)
                widget.set_points(normalized["curves"][axis])
                active_limit = (
                    custom_spin.value()
                    if custom_value
                    else limit_combo.currentData()
                )
                if active_limit != limit_value:
                    widget.set_input_limit(active_limit, rescale=True)
            self.filter_enabled_checkbox.setChecked(normalized["filter_enabled"])
            for identifier, controls in self.filter_controls_by_type.items():
                for key, slider in controls.items():
                    parameter = self._filter_slider_specs_by_type[identifier][key]
                    value = self._filter_settings_by_type[identifier][key]
                    position = round(
                        (value - parameter.minimum) / parameter.step
                    )
                    slider.blockSignals(True)
                    slider.setValue(position)
                    slider.blockSignals(False)
                    self._update_filter_value_label(identifier, key)
            for key, spin in self.offset_controls.items():
                spin.setValue(normalized["offsets"][key])
            self._set_filter_controls_enabled(normalized["filter_enabled"])
            self.settings = self.get_settings()
        finally:
            self._loading = False

    def get_preferences(self):
        return native_output_section_from_settings(self.get_settings())

    def set_preferences(self, section):
        settings = native_output_preferences_from_section(
            section,
            self.log_message,
        )
        self.set_settings(settings)
        self.apply_settings_to_worker()

    def apply_settings_to_worker(self):
        settings = self.get_settings()
        if self.output_control_queue is None:
            return
        if not safe_queue_put(
            self.output_control_queue,
            ("set_native_processing", settings),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            self.log_message("Failed to send native output processing settings")

    def set_current_pose(self, pose):
        if not isinstance(pose, (list, tuple)) or len(pose) < 3:
            return
        try:
            current_pose = tuple(float(pose[index]) for index in range(3))
        except (TypeError, ValueError, OverflowError):
            return
        if not all(math.isfinite(value) for value in current_pose):
            return
        self._current_pose = current_pose
        self._current_pose_time = time.perf_counter()
        for axis_index, axis in enumerate(AXES):
            self.curve_widgets[axis].set_current_input(current_pose[axis_index])

    def _get_recent_pose_for_calibration(self):
        if (
            self._current_pose is None
            or self._current_pose_time is None
            or time.perf_counter() - self._current_pose_time
            > INPUT_LIMIT_CALIBRATION_POSE_STALE_S
        ):
            return None
        return self._current_pose

    def _start_automatic_calibration(self):
        if (
            self._calibration_controller is not None
            and self._calibration_controller.active
        ):
            return
        calibration = InputMagnitudeCalibration(
            self._get_recent_pose_for_calibration,
            self._apply_calibrated_limits,
            self.log_message,
        )
        try:
            calibration.start()
        except RuntimeError as error:
            self.log_message(f"Could not start input magnitude calibration: {error}")
            self.status_label.setText(f"Could not start calibration: {error}")
            return
        self._calibration_controller = calibration

    def _apply_calibrated_limits(self, yaw_limit, pitch_limit):
        applied_limits = {}
        self._loading = True
        try:
            for axis, limit in (("yaw", yaw_limit), ("pitch", pitch_limit)):
                custom_check = self.input_limit_custom_checks[axis]
                custom_spin = self.input_limit_custom_spins[axis]
                custom_check.blockSignals(True)
                custom_check.setChecked(True)
                custom_check.blockSignals(False)
                custom_spin.blockSignals(True)
                custom_spin.setValue(limit)
                custom_spin.blockSignals(False)
                applied_limit = custom_spin.value()
                applied_limits[axis] = applied_limit
                self._update_input_limit_controls(axis, True)
                self.curve_widgets[axis].set_input_limit(
                    applied_limit,
                    rescale=True,
                )
            self._calibration_controller = None
        finally:
            self._loading = False

        self.status_label.setText(
            f"Calibration set yaw to {applied_limits['yaw']:.1f}° and "
            f"pitch to {applied_limits['pitch']:.1f}°."
        )
        self._apply_pending_settings()

    def open_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_curve_changed(self, axis, points):
        self.curve_widgets[axis].set_points(points)
        self._schedule_apply()

    def _on_input_limit_changed(self, index):
        combo = self.sender()
        if combo is None or index < 0:
            return
        axis = combo.property("nativeAxis")
        if axis not in self.input_limit_controls:
            return
        limit = combo.itemData(index)
        custom_spin = self.input_limit_custom_spins[axis]
        custom_spin.blockSignals(True)
        custom_spin.setValue(limit)
        custom_spin.blockSignals(False)
        if not self.input_limit_custom_checks[axis].isChecked():
            self.curve_widgets[axis].set_input_limit(limit, rescale=True)
        self._schedule_apply()

    def _on_input_limit_custom_toggled(self, axis, custom):
        self._update_input_limit_controls(axis, custom)
        if self._loading:
            return
        limit = (
            self.input_limit_custom_spins[axis].value()
            if custom
            else self.input_limit_controls[axis].currentData()
        )
        self.curve_widgets[axis].set_input_limit(limit, rescale=True)
        self._schedule_apply()

    def _on_custom_input_limit_changed(self, axis, value):
        if not self.input_limit_custom_checks[axis].isChecked():
            return
        self.curve_widgets[axis].set_input_limit(value, rescale=True)
        self._schedule_apply()

    def _update_input_limit_controls(self, axis, custom):
        self.input_limit_controls[axis].setEnabled(not custom)
        self.input_limit_custom_spins[axis].setEnabled(custom)

    def _reset_selected_curve(self):
        axis = AXES[self.curve_tabs.currentIndex()]
        widget = self.curve_widgets[axis]
        widget.set_points(default_response_curve(widget.input_limit_deg))
        self._schedule_apply()

    def _on_filter_enabled_changed(self, enabled):
        self._set_filter_controls_enabled(enabled)
        self._schedule_apply()

    def _set_filter_controls_enabled(self, enabled):
        for controls in self.filter_controls_by_type.values():
            for slider in controls.values():
                slider.setEnabled(enabled)
        for labels in self.filter_value_labels_by_type.values():
            for label in labels.values():
                label.setEnabled(enabled)

    def _schedule_apply(self, *args):
        del args
        if not self._loading:
            self._save_timer.start(120)

    def _apply_pending_settings(self):
        try:
            settings = self.get_settings()
        except ValueError as error:
            self.status_label.setText(f"Invalid native output settings: {error}")
            self.log_message(str(error))
            return
        self.settings = settings
        self.apply_settings_to_worker()
        self.status_label.setText("Native output settings applied.")
        self.preferences_changed.emit()

    def reset_defaults(self):
        self.set_settings(default_native_output_settings())
        self._apply_pending_settings()
