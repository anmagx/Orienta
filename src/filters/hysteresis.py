"""Hysteresis deadband for suppressing small rotational pose jitter."""

import math
from typing import Sequence

from src.config.config import (
    HYSTERESIS_RELEASE_PERCENT_DEFAULT,
    HYSTERESIS_RELEASE_PERCENT_RANGE,
    HYSTERESIS_RELEASE_PERCENT_STEP,
    HYSTERESIS_THRESHOLD_DEG_DEFAULT,
    HYSTERESIS_THRESHOLD_DEG_RANGE,
    HYSTERESIS_THRESHOLD_DEG_STEP,
)
from .base import FilterDefinition, FilterParameter
from .utils import wrap_delta_degrees


HYSTERESIS_FILTER_ID = "hysteresis"
HYSTERESIS_FILTER_LABEL = "Hysteresis (Jitter Suppression)"
HYSTERESIS_FILTER_PARAMETERS = (
    FilterParameter(
        key="threshold_deg",
        label="Jitter threshold",
        tooltip=(
            "Ignore small input movements until they exceed this angle. "
            "Smaller values preserve fine movement; larger values suppress "
            "more jitter but increase the deadband."
        ),
        minimum=HYSTERESIS_THRESHOLD_DEG_RANGE[0],
        maximum=HYSTERESIS_THRESHOLD_DEG_RANGE[1],
        step=HYSTERESIS_THRESHOLD_DEG_STEP,
        default=HYSTERESIS_THRESHOLD_DEG_DEFAULT,
        decimals=2,
        unit="°",
    ),
    FilterParameter(
        key="release_percent",
        label="Hysteresis release",
        tooltip=(
            "After movement crosses the jitter threshold, retain this "
            "percentage of the threshold as a buffer. A larger buffer "
            "reduces chatter near the threshold but tracks less closely."
        ),
        minimum=HYSTERESIS_RELEASE_PERCENT_RANGE[0],
        maximum=HYSTERESIS_RELEASE_PERCENT_RANGE[1],
        step=HYSTERESIS_RELEASE_PERCENT_STEP,
        default=HYSTERESIS_RELEASE_PERCENT_DEFAULT,
        decimals=0,
        unit="%",
    ),
)


class _HysteresisAxisFilter:
    def __init__(self, threshold_deg, release_percent, wrap_degrees=False):
        self.threshold_deg = threshold_deg
        self.release_threshold_deg = threshold_deg * release_percent / 100.0
        self.wrap_degrees = wrap_degrees
        self.reset()

    def reset(self):
        self._last_time = None
        self._last_raw = None
        self._unwrapped_input = None
        self._output = None

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

        delta = self._unwrapped_input - self._output
        if abs(delta) > self.threshold_deg:
            self._output = (
                self._unwrapped_input
                - math.copysign(self.release_threshold_deg, delta)
            )

        self._last_time = timestamp
        self._last_raw = value
        return self._output

    def _set_initial_value(self, value, timestamp):
        self._last_time = timestamp
        self._last_raw = value
        self._unwrapped_input = value
        self._output = value


class HysteresisPoseFilter:
    """Suppress small pose changes with a Schmitt-style deadband per axis."""

    def __init__(self, settings):
        self.settings = dict(settings)
        self.reset()

    def reset(self):
        threshold_deg = self.settings["threshold_deg"]
        release_percent = self.settings["release_percent"]
        self._axis_filters = (
            _HysteresisAxisFilter(
                threshold_deg,
                release_percent,
                wrap_degrees=True,
            ),
            _HysteresisAxisFilter(threshold_deg, release_percent),
            _HysteresisAxisFilter(threshold_deg, release_percent),
        )

    def process(self, angles: Sequence[float], timestamp: float):
        if len(angles) != 3:
            raise ValueError(
                "Hysteresis pose filter requires yaw, pitch, and roll"
            )
        return tuple(
            axis_filter.process(value, timestamp)
            for axis_filter, value in zip(self._axis_filters, angles)
        )


HYSTERESIS_FILTER = FilterDefinition(
    identifier=HYSTERESIS_FILTER_ID,
    label=HYSTERESIS_FILTER_LABEL,
    parameters=HYSTERESIS_FILTER_PARAMETERS,
    factory=HysteresisPoseFilter,
    description=(
        "Ignores tiny changes until they cross a threshold. It suppresses "
        "jitter strongly, but small intentional movements may not register."
    ),
)
