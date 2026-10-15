"""Fixed-cutoff exponential moving-average filter for rotational pose angles."""

import math
from typing import Sequence

from src.config.config import (
    EMA_CUTOFF_HZ_DEFAULT,
    EMA_CUTOFF_HZ_RANGE,
    EMA_CUTOFF_HZ_STEP,
)
from .base import FilterDefinition, FilterParameter
from .utils import low_pass_alpha, wrap_delta_degrees


EMA_FILTER_ID = "ema"
EMA_FILTER_LABEL = "EMA (Exponential Moving Average)"
EMA_FILTER_PARAMETERS = (
    FilterParameter(
        key="cutoff_hz",
        label="Smoothing cutoff",
        tooltip=(
            "EMA cutoff (Hz). Lower values smooth movement more but add lag; "
            "higher values respond more immediately."
        ),
        minimum=EMA_CUTOFF_HZ_RANGE[0],
        maximum=EMA_CUTOFF_HZ_RANGE[1],
        step=EMA_CUTOFF_HZ_STEP,
        default=EMA_CUTOFF_HZ_DEFAULT,
        decimals=1,
        unit="Hz",
    ),
)


class _EmaAxisFilter:
    def __init__(self, cutoff_hz, wrap_degrees=False):
        self.cutoff_hz = cutoff_hz
        self.wrap_degrees = wrap_degrees
        self.reset()

    def reset(self):
        self._last_time = None
        self._last_raw = None
        self._last_unwrapped_raw = None
        self._filtered_value = None

    def process(self, value, timestamp):
        if not math.isfinite(value) or not math.isfinite(timestamp):
            raise ValueError("Filter input and timestamp must be finite")
        if self._last_time is None:
            self._set_initial_value(value, timestamp)
            return value

        delta_time = timestamp - self._last_time
        if not 0.0 < delta_time <= 1.0:
            self.reset()
            self._set_initial_value(value, timestamp)
            return value

        if self.wrap_degrees:
            delta = wrap_delta_degrees(value, self._last_raw)
            unwrapped_value = self._last_unwrapped_raw + delta
        else:
            unwrapped_value = value

        alpha = low_pass_alpha(self.cutoff_hz, delta_time)
        filtered_value = (
            alpha * unwrapped_value
            + (1.0 - alpha) * self._filtered_value
        )

        self._last_time = timestamp
        self._last_raw = value
        self._last_unwrapped_raw = unwrapped_value
        self._filtered_value = filtered_value
        return filtered_value

    def _set_initial_value(self, value, timestamp):
        self._last_time = timestamp
        self._last_raw = value
        self._last_unwrapped_raw = value
        self._filtered_value = value


class EmaPoseFilter:
    """Apply fixed-cutoff EMA smoothing independently to yaw, pitch, and roll."""

    def __init__(self, settings):
        self.settings = dict(settings)
        self.reset()

    def reset(self):
        cutoff_hz = self.settings["cutoff_hz"]
        self._axis_filters = (
            _EmaAxisFilter(cutoff_hz, wrap_degrees=True),
            _EmaAxisFilter(cutoff_hz),
            _EmaAxisFilter(cutoff_hz),
        )

    def process(self, angles: Sequence[float], timestamp: float):
        if len(angles) != 3:
            raise ValueError("EMA pose filter requires yaw, pitch, and roll")
        return tuple(
            axis_filter.process(value, timestamp)
            for axis_filter, value in zip(self._axis_filters, angles)
        )


EMA_FILTER = FilterDefinition(
    identifier=EMA_FILTER_ID,
    label=EMA_FILTER_LABEL,
    parameters=EMA_FILTER_PARAMETERS,
    factory=EmaPoseFilter,
    description=(
        "Applies steady smoothing to all movement. More smoothing reduces "
        "jitter but adds delay to turns."
    ),
)
