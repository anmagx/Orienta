"""Critically damped second-order smoothing for rotational pose angles."""

import math
from typing import Sequence

from src.config.config import (
    CRITICALLY_DAMPED_RESPONSE_HZ_DEFAULT,
    CRITICALLY_DAMPED_RESPONSE_HZ_RANGE,
    CRITICALLY_DAMPED_RESPONSE_HZ_STEP,
)
from .base import FilterDefinition, FilterParameter
from .utils import wrap_delta_degrees


CRITICALLY_DAMPED_FILTER_ID = "critically_damped"
CRITICALLY_DAMPED_FILTER_LABEL = "Critically Damped"
CRITICALLY_DAMPED_FILTER_PARAMETERS = (
    FilterParameter(
        key="response_hz",
        label="Responsiveness",
        tooltip=(
            "Natural response frequency (Hz). Lower values smooth movement "
            "more but add lag; higher values follow head movement faster."
        ),
        minimum=CRITICALLY_DAMPED_RESPONSE_HZ_RANGE[0],
        maximum=CRITICALLY_DAMPED_RESPONSE_HZ_RANGE[1],
        step=CRITICALLY_DAMPED_RESPONSE_HZ_STEP,
        default=CRITICALLY_DAMPED_RESPONSE_HZ_DEFAULT,
        decimals=1,
        unit="Hz",
    ),
)


class _CriticallyDampedAxisFilter:
    def __init__(self, response_hz, wrap_degrees=False):
        self.response_hz = response_hz
        self.wrap_degrees = wrap_degrees
        self.reset()

    def reset(self):
        self._last_time = None
        self._last_raw = None
        self._unwrapped_input = None
        self._position = None
        self._velocity = 0.0

    def process(self, value, timestamp):
        if not math.isfinite(value) or not math.isfinite(timestamp):
            raise ValueError("Filter input and timestamp must be finite")
        if (
            self._last_time is None
            or not 0.0 < timestamp - self._last_time <= 1.0
        ):
            self._set_initial_value(value, timestamp)
            return value

        if self.wrap_degrees:
            self._unwrapped_input += wrap_delta_degrees(value, self._last_raw)
        else:
            self._unwrapped_input = value

        delta_time = timestamp - self._last_time
        angular_frequency = 2.0 * math.pi * self.response_hz
        position_error = self._position - self._unwrapped_input
        intermediate_velocity = (
            self._velocity + angular_frequency * position_error
        )
        decay = math.exp(-angular_frequency * delta_time)
        self._position = self._unwrapped_input + (
            position_error + intermediate_velocity * delta_time
        ) * decay
        self._velocity = (
            self._velocity
            - angular_frequency * intermediate_velocity * delta_time
        ) * decay
        self._last_time = timestamp
        self._last_raw = value
        return self._position

    def _set_initial_value(self, value, timestamp):
        self._last_time = timestamp
        self._last_raw = value
        self._unwrapped_input = value
        self._position = value
        self._velocity = 0.0


class CriticallyDampedPoseFilter:
    """Smooth yaw, pitch, and roll without overshooting a fixed target."""

    def __init__(self, settings):
        self.settings = dict(settings)
        self.reset()

    def reset(self):
        response_hz = self.settings["response_hz"]
        self._axis_filters = (
            _CriticallyDampedAxisFilter(response_hz, wrap_degrees=True),
            _CriticallyDampedAxisFilter(response_hz),
            _CriticallyDampedAxisFilter(response_hz),
        )

    def process(self, angles: Sequence[float], timestamp: float):
        if len(angles) != 3:
            raise ValueError(
                "Critically damped pose filter requires yaw, pitch, and roll"
            )
        return tuple(
            axis_filter.process(value, timestamp)
            for axis_filter, value in zip(self._axis_filters, angles)
        )


CRITICALLY_DAMPED_FILTER = FilterDefinition(
    identifier=CRITICALLY_DAMPED_FILTER_ID,
    label=CRITICALLY_DAMPED_FILTER_LABEL,
    parameters=CRITICALLY_DAMPED_FILTER_PARAMETERS,
    factory=CriticallyDampedPoseFilter,
)
