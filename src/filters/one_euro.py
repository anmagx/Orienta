"""One Euro adaptive low-pass filter for rotational pose angles."""

import math
from typing import Sequence

from src.config.config import (
    FILTER_BETA_DEFAULT,
    FILTER_BETA_RANGE,
    FILTER_BETA_STEP,
    FILTER_DERIVATIVE_CUTOFF_HZ_DEFAULT,
    FILTER_DERIVATIVE_CUTOFF_HZ_RANGE,
    FILTER_DERIVATIVE_CUTOFF_HZ_STEP,
    FILTER_MIN_CUTOFF_HZ_DEFAULT,
    FILTER_MIN_CUTOFF_HZ_RANGE,
    FILTER_MIN_CUTOFF_HZ_STEP,
)
from .base import FilterDefinition, FilterParameter


ONE_EURO_FILTER_ID = "one_euro"
ONE_EURO_FILTER_LABEL = "One Euro"
ONE_EURO_FILTER_PARAMETERS = (
    FilterParameter(
        key="min_cutoff_hz",
        label="Low-speed smoothing",
        tooltip=(
            "One Euro minimum cutoff (Hz). Lower values smooth quiet "
            "movement more but add lag; higher values feel more immediate."
        ),
        minimum=FILTER_MIN_CUTOFF_HZ_RANGE[0],
        maximum=FILTER_MIN_CUTOFF_HZ_RANGE[1],
        step=FILTER_MIN_CUTOFF_HZ_STEP,
        default=FILTER_MIN_CUTOFF_HZ_DEFAULT,
        decimals=1,
        unit="Hz",
    ),
    FilterParameter(
        key="beta",
        label="Fast-movement responsiveness",
        tooltip=(
            "One Euro beta. Controls how quickly smoothing decreases as "
            "input angular speed rises; higher values pass fast movement "
            "more readily. The value scales Hz per degree/second."
        ),
        minimum=FILTER_BETA_RANGE[0],
        maximum=FILTER_BETA_RANGE[1],
        step=FILTER_BETA_STEP,
        default=FILTER_BETA_DEFAULT,
        decimals=2,
    ),
    FilterParameter(
        key="derivative_cutoff_hz",
        label="Speed-estimate stability",
        tooltip=(
            "One Euro derivative cutoff (Hz). It smooths the angular-speed "
            "estimate that drives the adaptive cutoff. Lower values make "
            "that estimate steadier but slower to react."
        ),
        minimum=FILTER_DERIVATIVE_CUTOFF_HZ_RANGE[0],
        maximum=FILTER_DERIVATIVE_CUTOFF_HZ_RANGE[1],
        step=FILTER_DERIVATIVE_CUTOFF_HZ_STEP,
        default=FILTER_DERIVATIVE_CUTOFF_HZ_DEFAULT,
        decimals=1,
        unit="Hz",
    ),
)


def wrap_delta_degrees(value, reference):
    return (value - reference + 180.0) % 360.0 - 180.0


def _low_pass_alpha(cutoff_hz, delta_time):
    time_constant = 1.0 / (2.0 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + time_constant / delta_time)


class _OneEuroAxisFilter:
    def __init__(self, min_cutoff_hz, beta, derivative_cutoff_hz,
                 wrap_degrees=False):
        self.min_cutoff_hz = min_cutoff_hz
        self.beta = beta
        self.derivative_cutoff_hz = derivative_cutoff_hz
        self.wrap_degrees = wrap_degrees
        self.reset()

    def reset(self):
        self._last_time = None
        self._last_raw = None
        self._last_unwrapped_raw = None
        self._filtered_value = None
        self._filtered_derivative = 0.0

    def process(self, value, timestamp):
        if not math.isfinite(value) or not math.isfinite(timestamp):
            raise ValueError("Filter input and timestamp must be finite")
        if self._last_time is None:
            self._last_time = timestamp
            self._last_raw = value
            self._last_unwrapped_raw = value
            self._filtered_value = value
            return value

        delta_time = timestamp - self._last_time
        if not 0.0 < delta_time <= 1.0:
            self.reset()
            self._last_time = timestamp
            self._last_raw = value
            self._last_unwrapped_raw = value
            self._filtered_value = value
            return value

        if self.wrap_degrees:
            delta = wrap_delta_degrees(value, self._last_raw)
            unwrapped_value = self._last_unwrapped_raw + delta
        else:
            delta = value - self._last_raw
            unwrapped_value = value

        derivative = delta / delta_time
        derivative_alpha = _low_pass_alpha(
            self.derivative_cutoff_hz,
            delta_time,
        )
        self._filtered_derivative = (
            derivative_alpha * derivative
            + (1.0 - derivative_alpha) * self._filtered_derivative
        )
        cutoff = (
            self.min_cutoff_hz
            + self.beta * abs(self._filtered_derivative)
        )
        value_alpha = _low_pass_alpha(cutoff, delta_time)
        filtered_value = (
            value_alpha * unwrapped_value
            + (1.0 - value_alpha) * self._filtered_value
        )

        self._last_time = timestamp
        self._last_raw = value
        self._last_unwrapped_raw = unwrapped_value
        self._filtered_value = filtered_value
        return filtered_value


class OneEuroPoseFilter:
    """Apply independent One Euro filters to yaw, pitch, and roll."""

    def __init__(self, settings):
        self.settings = dict(settings)
        self.reset()

    def reset(self):
        self._axis_filters = (
            _OneEuroAxisFilter(
                self.settings["min_cutoff_hz"],
                self.settings["beta"],
                self.settings["derivative_cutoff_hz"],
                wrap_degrees=True,
            ),
            _OneEuroAxisFilter(
                self.settings["min_cutoff_hz"],
                self.settings["beta"],
                self.settings["derivative_cutoff_hz"],
            ),
            _OneEuroAxisFilter(
                self.settings["min_cutoff_hz"],
                self.settings["beta"],
                self.settings["derivative_cutoff_hz"],
            ),
        )

    def process(self, angles: Sequence[float], timestamp: float):
        if len(angles) != 3:
            raise ValueError("One Euro pose filter requires yaw, pitch, and roll")
        return tuple(
            axis_filter.process(value, timestamp)
            for axis_filter, value in zip(self._axis_filters, angles)
        )


ONE_EURO_FILTER = FilterDefinition(
    identifier=ONE_EURO_FILTER_ID,
    label=ONE_EURO_FILTER_LABEL,
    parameters=ONE_EURO_FILTER_PARAMETERS,
    factory=OneEuroPoseFilter,
)
