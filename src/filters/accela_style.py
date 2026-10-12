"""Independent Accela-inspired rotational filter; see third-party notices."""

import math
from typing import Sequence

from src.config.config import (
    ACCELA_STYLE_ACCELERATION_GAIN_DEFAULT,
    ACCELA_STYLE_ACCELERATION_GAIN_RANGE,
    ACCELA_STYLE_ACCELERATION_GAIN_STEP,
    ACCELA_STYLE_ACCELERATION_SCALE_DEG_DEFAULT,
    ACCELA_STYLE_ACCELERATION_SCALE_DEG_RANGE,
    ACCELA_STYLE_ACCELERATION_SCALE_DEG_STEP,
    ACCELA_STYLE_DEADZONE_DEG_DEFAULT,
    ACCELA_STYLE_DEADZONE_DEG_RANGE,
    ACCELA_STYLE_DEADZONE_DEG_STEP,
    ACCELA_STYLE_RESPONSE_HZ_DEFAULT,
    ACCELA_STYLE_RESPONSE_HZ_RANGE,
    ACCELA_STYLE_RESPONSE_HZ_STEP,
)
from .base import FilterDefinition, FilterParameter
from .utils import low_pass_alpha, wrap_delta_degrees


ACCELA_STYLE_FILTER_ID = "accela_style"
ACCELA_STYLE_FILTER_LABEL = "Accela-style (opentrack-inspired)"
ACCELA_STYLE_FILTER_PARAMETERS = (
    FilterParameter(
        key="response_hz",
        label="Low-speed response",
        tooltip=(
            "Base response frequency (Hz). Lower values smooth slow "
            "movement more but add lag."
        ),
        minimum=ACCELA_STYLE_RESPONSE_HZ_RANGE[0],
        maximum=ACCELA_STYLE_RESPONSE_HZ_RANGE[1],
        step=ACCELA_STYLE_RESPONSE_HZ_STEP,
        default=ACCELA_STYLE_RESPONSE_HZ_DEFAULT,
        decimals=1,
        unit="Hz",
    ),
    FilterParameter(
        key="acceleration_gain",
        label="Fast-movement boost",
        tooltip=(
            "Maximum extra response at large angular deviations. Higher "
            "values make the filter catch up more quickly after fast turns."
        ),
        minimum=ACCELA_STYLE_ACCELERATION_GAIN_RANGE[0],
        maximum=ACCELA_STYLE_ACCELERATION_GAIN_RANGE[1],
        step=ACCELA_STYLE_ACCELERATION_GAIN_STEP,
        default=ACCELA_STYLE_ACCELERATION_GAIN_DEFAULT,
        decimals=1,
    ),
    FilterParameter(
        key="acceleration_scale_deg",
        label="Boost onset angle",
        tooltip=(
            "Angular deviation where the fast-movement boost reaches half "
            "of its maximum. Larger values spread the boost over a wider "
            "range of movement."
        ),
        minimum=ACCELA_STYLE_ACCELERATION_SCALE_DEG_RANGE[0],
        maximum=ACCELA_STYLE_ACCELERATION_SCALE_DEG_RANGE[1],
        step=ACCELA_STYLE_ACCELERATION_SCALE_DEG_STEP,
        default=ACCELA_STYLE_ACCELERATION_SCALE_DEG_DEFAULT,
        decimals=2,
        unit="°",
    ),
    FilterParameter(
        key="deadzone_deg",
        label="Rotation deadzone",
        tooltip=(
            "Ignore angular deviations smaller than this value on each "
            "axis to reduce stationary sensor jitter."
        ),
        minimum=ACCELA_STYLE_DEADZONE_DEG_RANGE[0],
        maximum=ACCELA_STYLE_DEADZONE_DEG_RANGE[1],
        step=ACCELA_STYLE_DEADZONE_DEG_STEP,
        default=ACCELA_STYLE_DEADZONE_DEG_DEFAULT,
        decimals=2,
        unit="°",
    ),
)


class AccelaStylePoseFilter:
    """Use deadzoned vector error to adapt rotational response."""

    def __init__(self, settings):
        self.settings = dict(settings)
        self.reset()

    def reset(self):
        self._last_time = None
        self._last_raw = None
        self._unwrapped_input = None
        self._filtered_pose = None

    def process(self, angles: Sequence[float], timestamp: float):
        if len(angles) != 3:
            raise ValueError(
                "Accela-style filter requires yaw, pitch, and roll"
            )
        if not math.isfinite(timestamp) or any(
            not math.isfinite(value) for value in angles
        ):
            raise ValueError("Filter input and timestamp must be finite")

        if self._last_time is None:
            self._set_initial_pose(angles, timestamp)
            return tuple(angles)

        delta_time = timestamp - self._last_time
        if not 0.0 < delta_time <= 1.0:
            self._set_initial_pose(angles, timestamp)
            return tuple(angles)

        self._unwrapped_input = tuple(
            current + wrap_delta_degrees(raw, previous)
            for current, raw, previous in zip(
                self._unwrapped_input,
                angles,
                self._last_raw,
            )
        )
        deadzone = self.settings["deadzone_deg"]
        deltas = tuple(
            math.copysign(max(0.0, abs(delta) - deadzone), delta)
            if delta != 0.0
            else 0.0
            for delta in (
                target - filtered
                for target, filtered in zip(
                    self._unwrapped_input,
                    self._filtered_pose,
                )
            )
        )
        error_magnitude = math.hypot(*deltas)
        if error_magnitude > 0.0:
            acceleration_scale = self.settings["acceleration_scale_deg"]
            normalized_error = error_magnitude / (
                error_magnitude + acceleration_scale
            )
            response_hz = self.settings["response_hz"] * (
                1.0
                + self.settings["acceleration_gain"]
                * normalized_error
                * normalized_error
            )
            alpha = low_pass_alpha(response_hz, delta_time)
            self._filtered_pose = tuple(
                filtered + delta * alpha
                for filtered, delta in zip(self._filtered_pose, deltas)
            )

        self._last_raw = tuple(angles)
        self._last_time = timestamp
        return self._filtered_pose

    def _set_initial_pose(self, angles, timestamp):
        self._last_raw = tuple(angles)
        self._unwrapped_input = tuple(angles)
        self._filtered_pose = tuple(angles)
        self._last_time = timestamp


ACCELA_STYLE_FILTER = FilterDefinition(
    identifier=ACCELA_STYLE_FILTER_ID,
    label=ACCELA_STYLE_FILTER_LABEL,
    parameters=ACCELA_STYLE_FILTER_PARAMETERS,
    factory=AccelaStylePoseFilter,
)
