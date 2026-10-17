"""Sensor-clock pose samples exchanged between fusion and output workers."""

from typing import NamedTuple


class TrackingSample(NamedTuple):
    timestamp: float
    angles: tuple[float, float, float]
    rates: tuple[float, float, float]
    reset_filter: bool = False
