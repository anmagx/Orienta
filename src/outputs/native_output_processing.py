"""Smooth pose mapping and low-latency filtering for native game-client output."""

import math

from src.config.config import (
    CUSTOM_INPUT_LIMIT_DEG_RANGE,
)
from src.filters import (
    available_filters,
    default_filter_settings,
    get_filter_definition,
)
from src.filters.one_euro import ONE_EURO_FILTER_ID, wrap_delta_degrees

AXES = ("yaw", "pitch", "roll")
CURVE_INPUT_LIMIT_DEG = 180.0
CURVE_OUTPUT_LIMIT_DEG = 180.0
ROTATION_OFFSET_LIMIT_DEG = 180.0
TRANSLATION_OFFSET_LIMIT_MM = 500.0
INPUT_LIMIT_CHOICES_DEG = (30.0, 45.0, 60.0, 75.0, 90.0)
DEFAULT_INPUT_LIMIT_DEG = 90.0


def default_response_curve(input_limit_deg=DEFAULT_INPUT_LIMIT_DEG):
    """Return a linear response curve spanning input_limit_deg to 180 degrees."""
    input_limit_deg = float(input_limit_deg)
    return (
        (0.0, 0.0),
        (input_limit_deg / 2.0, CURVE_OUTPUT_LIMIT_DEG / 2.0),
        (input_limit_deg, CURVE_OUTPUT_LIMIT_DEG),
    )


def default_native_output_settings():
    """Return a fresh, mutable native-output settings snapshot."""
    input_limits = {axis: DEFAULT_INPUT_LIMIT_DEG for axis in AXES}
    filter_defaults = default_filter_settings()
    return {
        "curves": {
            axis: [list(point) for point in default_response_curve(input_limits[axis])]
            for axis in AXES
        },
        "input_limits": input_limits,
        "input_limit_custom": {axis: False for axis in AXES},
        **filter_defaults,
        "offsets": {
            "yaw_deg": 0.0,
            "pitch_deg": 0.0,
            "roll_deg": 0.0,
            "x_mm": 0.0,
            "y_mm": 0.0,
            "z_mm": 0.0,
        },
    }


def _finite_number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def validate_curve(points, input_limit_deg=DEFAULT_INPUT_LIMIT_DEG):
    """Validate a smooth magnitude curve with fixed input endpoints."""
    input_limit_deg = _finite_number(input_limit_deg, "Curve input limit")
    if not (
        CUSTOM_INPUT_LIMIT_DEG_RANGE[0]
        <= input_limit_deg
        <= CUSTOM_INPUT_LIMIT_DEG_RANGE[1]
    ):
        raise ValueError("Curve input limit is out of range")
    if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= 32:
        raise ValueError("A response curve must contain 3 to 32 points")

    normalized = []
    previous_x = None
    for index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"Curve point {index + 1} must contain input and output")
        x = _finite_number(point[0], f"Curve point {index + 1} input")
        y = _finite_number(point[1], f"Curve point {index + 1} output")
        if not 0.0 <= x <= input_limit_deg:
            raise ValueError(
                f"Curve input magnitudes must be between 0 and {input_limit_deg:g} degrees"
            )
        if not 0.0 <= y <= CURVE_OUTPUT_LIMIT_DEG:
            raise ValueError("Curve output magnitudes must be between 0 and 180 degrees")
        if previous_x is not None and x <= previous_x:
            raise ValueError("Curve inputs must be strictly increasing")
        normalized.append((x, y))
        previous_x = x

    if normalized[0] != (0.0, 0.0):
        raise ValueError("The first response-curve point must be fixed at (0, 0)")
    if normalized[-1][0] != input_limit_deg:
        raise ValueError("The final input point must match the selected input limit")
    return tuple(normalized)


def normalize_native_output_settings(settings):
    """Return a validated, independent native-output settings snapshot."""
    if not isinstance(settings, dict):
        raise ValueError("Native output settings must be a mapping")

    defaults = default_native_output_settings()
    curves = settings.get("curves", {})
    input_limits = settings.get("input_limits", defaults["input_limits"])
    input_limit_custom = settings.get(
        "input_limit_custom",
        defaults["input_limit_custom"],
    )
    offsets = settings.get("offsets", defaults["offsets"])
    if not isinstance(curves, dict):
        raise ValueError("Native output curves must be a mapping")
    if not isinstance(input_limits, dict):
        raise ValueError("Native output input limits must be a mapping")
    if not isinstance(input_limit_custom, dict):
        raise ValueError("Native output custom-limit settings must be a mapping")
    if not isinstance(offsets, dict):
        raise ValueError("Native output offsets must be a mapping")

    normalized_input_limits = {}
    normalized_input_limit_custom = {}
    for axis in AXES:
        custom_value = input_limit_custom.get(
            axis,
            defaults["input_limit_custom"][axis],
        )
        if not isinstance(custom_value, bool):
            raise ValueError(f"{axis} custom-input-limit flag must be a boolean")
        value = _finite_number(
            input_limits.get(axis, defaults["input_limits"][axis]),
            f"{axis} input limit",
        )
        if custom_value:
            if not CUSTOM_INPUT_LIMIT_DEG_RANGE[0] <= value <= CUSTOM_INPUT_LIMIT_DEG_RANGE[1]:
                raise ValueError(
                    f"{axis} custom input limit must be between "
                    f"{CUSTOM_INPUT_LIMIT_DEG_RANGE[0]} and "
                    f"{CUSTOM_INPUT_LIMIT_DEG_RANGE[1]} degrees"
                )
        elif value not in INPUT_LIMIT_CHOICES_DEG:
            raise ValueError(
                f"{axis} input limit must be one of "
                f"{', '.join(str(int(choice)) for choice in INPUT_LIMIT_CHOICES_DEG)} degrees"
            )
        normalized_input_limits[axis] = value
        normalized_input_limit_custom[axis] = custom_value
    normalized_curves = {
        axis: validate_curve(
            curves.get(
                axis,
                default_response_curve(normalized_input_limits[axis]),
            ),
            normalized_input_limits[axis],
        )
        for axis in AXES
    }

    filter_type = settings.get("filter_type", defaults["filter_type"])
    get_filter_definition(filter_type)
    filter_enabled = settings.get("filter_enabled", defaults["filter_enabled"])
    if not isinstance(filter_enabled, bool):
        raise ValueError("Filter enabled must be a boolean")

    filter_settings = settings.get("filter_settings")
    if filter_settings is None:
        filter_settings = {}
        legacy_keys = (
            "filter_min_cutoff_hz",
            "filter_beta",
            "filter_derivative_cutoff_hz",
        )
        if any(key in settings for key in legacy_keys):
            one_euro_defaults = get_filter_definition(
                ONE_EURO_FILTER_ID
            ).default_settings()
            filter_settings[ONE_EURO_FILTER_ID] = {
                "min_cutoff_hz": settings.get(
                    "filter_min_cutoff_hz",
                    one_euro_defaults["min_cutoff_hz"],
                ),
                "beta": settings.get("filter_beta", one_euro_defaults["beta"]),
                "derivative_cutoff_hz": settings.get(
                    "filter_derivative_cutoff_hz",
                    one_euro_defaults["derivative_cutoff_hz"],
                ),
            }
    if not isinstance(filter_settings, dict):
        raise ValueError("Filter settings must be a mapping by filter identifier")
    filter_settings = dict(filter_settings)

    registered_filter_ids = {
        definition.identifier for definition in available_filters()
    }
    unknown_filter_ids = set(filter_settings) - registered_filter_ids
    if unknown_filter_ids:
        raise ValueError(
            "Settings provided for unregistered filters: "
            + ", ".join(sorted(unknown_filter_ids))
        )
    normalized_filter_settings = {
        definition.identifier: definition.normalize_settings(
            filter_settings.get(
                definition.identifier,
                definition.default_settings(),
            )
        )
        for definition in available_filters()
    }
    normalized_offsets = {}
    limits_by_key = {
        "yaw_deg": ROTATION_OFFSET_LIMIT_DEG,
        "pitch_deg": ROTATION_OFFSET_LIMIT_DEG,
        "roll_deg": ROTATION_OFFSET_LIMIT_DEG,
        "x_mm": TRANSLATION_OFFSET_LIMIT_MM,
        "y_mm": TRANSLATION_OFFSET_LIMIT_MM,
        "z_mm": TRANSLATION_OFFSET_LIMIT_MM,
    }
    for key, limit in limits_by_key.items():
        value = _finite_number(offsets.get(key, defaults["offsets"][key]), key)
        if not -limit <= value <= limit:
            raise ValueError(f"{key} must be between {-limit} and {limit}")
        normalized_offsets[key] = value

    return {
        "curves": normalized_curves,
        "input_limits": normalized_input_limits,
        "input_limit_custom": normalized_input_limit_custom,
        "filter_type": filter_type,
        "filter_enabled": filter_enabled,
        "filter_settings": normalized_filter_settings,
        "offsets": normalized_offsets,
    }


def map_response_curve(points, value, input_limit_deg=DEFAULT_INPUT_LIMIT_DEG):
    """Map an input magnitude with a shape-preserving cubic Hermite curve."""
    curve = validate_curve(points, input_limit_deg)
    x_value = _finite_number(value, "Curve input")
    x_value = max(0.0, min(input_limit_deg, x_value))
    if x_value <= curve[0][0]:
        return curve[0][1]
    if x_value >= curve[-1][0]:
        return curve[-1][1]

    widths = [
        right[0] - left[0]
        for left, right in zip(curve, curve[1:])
    ]
    slopes = [
        (right[1] - left[1]) / width
        for left, right, width in zip(curve, curve[1:], widths)
    ]

    def endpoint_tangent(first_width, next_width, first_slope, next_slope):
        tangent = (
            (2.0 * first_width + next_width) * first_slope
            - first_width * next_slope
        ) / (first_width + next_width)
        if first_slope == 0.0 or tangent * first_slope <= 0.0:
            return 0.0
        if (
            first_slope * next_slope < 0.0
            and abs(tangent) > 3.0 * abs(first_slope)
        ):
            return 3.0 * first_slope
        return tangent

    tangents = [endpoint_tangent(widths[0], widths[1], slopes[0], slopes[1])]
    for index in range(1, len(curve) - 1):
        previous_slope = slopes[index - 1]
        next_slope = slopes[index]
        if (
            previous_slope == 0.0
            or next_slope == 0.0
            or previous_slope * next_slope < 0.0
        ):
            tangents.append(0.0)
        else:
            previous_width = widths[index - 1]
            next_width = widths[index]
            weight_previous = 2.0 * next_width + previous_width
            weight_next = next_width + 2.0 * previous_width
            tangents.append(
                (weight_previous + weight_next)
                / (
                    weight_previous / previous_slope
                    + weight_next / next_slope
                )
            )
    tangents.append(
        endpoint_tangent(
            widths[-1],
            widths[-2],
            slopes[-1],
            slopes[-2],
        )
    )

    for index, ((left_x, left_y), (right_x, right_y)) in enumerate(
        zip(curve, curve[1:])
    ):
        if x_value <= right_x:
            width = right_x - left_x
            fraction = (x_value - left_x) / width
            fraction_squared = fraction * fraction
            fraction_cubed = fraction_squared * fraction
            value_at_x = (
                (2.0 * fraction_cubed - 3.0 * fraction_squared + 1.0) * left_y
                + (fraction_cubed - 2.0 * fraction_squared + fraction)
                * width
                * tangents[index]
                + (-2.0 * fraction_cubed + 3.0 * fraction_squared) * right_y
                + (fraction_cubed - fraction_squared)
                * width
                * tangents[index + 1]
            )
            return max(min(left_y, right_y), min(max(left_y, right_y), value_at_x))
    return curve[-1][1]


class NativeOutputProcessor:
    """Map, filter, and offset poses destined for native game clients."""

    def __init__(self, settings=None):
        self.settings = normalize_native_output_settings(
            settings if settings is not None else default_native_output_settings()
        )
        self._filter = None
        self.reset()

    def set_settings(self, settings):
        """Replace settings atomically and reset all time-dependent state."""
        self.settings = normalize_native_output_settings(settings)
        self.reset()

    def reset(self):
        """Clear filter history, e.g. after a protocol or output-state change."""
        self._filter = get_filter_definition(
            self.settings["filter_type"]
        ).create(
            self.settings["filter_settings"][self.settings["filter_type"]]
        )
        self._last_yaw_input = None
        self._unwrapped_yaw_input = None
        self._last_pose_time = None

    def process(self, pose, timestamp):
        """Return a six-axis native pose in degrees and millimeters."""
        if not isinstance(pose, (list, tuple)) or len(pose) < 3:
            raise ValueError("Native output pose requires yaw, pitch, and roll")
        angles = tuple(
            _finite_number(pose[index], f"{axis} input")
            for index, axis in enumerate(AXES)
        )
        timestamp = _finite_number(timestamp, "Pose timestamp")

        yaw_input = angles[0]
        if (
            self._last_pose_time is None
            or not 0.0 < timestamp - self._last_pose_time <= 1.0
        ):
            self._unwrapped_yaw_input = yaw_input
        else:
            self._unwrapped_yaw_input += wrap_delta_degrees(
                yaw_input,
                self._last_yaw_input,
            )
        self._last_yaw_input = yaw_input
        self._last_pose_time = timestamp
        angles = (self._unwrapped_yaw_input, angles[1], angles[2])

        if self.settings["filter_enabled"]:
            filtered_angles = self._filter.process(angles, timestamp)
        else:
            filtered_angles = angles

        mapped = tuple(
            math.copysign(
                map_response_curve(
                    self.settings["curves"][axis],
                    min(
                        abs(value),
                        self.settings["input_limits"][axis],
                    ),
                    self.settings["input_limits"][axis],
                ),
                value,
            )
            if value != 0.0
            else 0.0
            for axis, value in zip(AXES, filtered_angles)
        )

        offsets = self.settings["offsets"]
        rotations = tuple(
            max(
                -CURVE_OUTPUT_LIMIT_DEG,
                min(
                    CURVE_OUTPUT_LIMIT_DEG,
                    mapped[index] + offsets[f"{axis}_deg"],
                ),
            )
            for index, axis in enumerate(AXES)
        )
        return (
            *rotations,
            offsets["x_mm"],
            offsets["y_mm"],
            offsets["z_mm"],
        )
