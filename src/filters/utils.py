"""Shared angle and low-pass helpers for native pose filters."""

import math


def wrap_delta_degrees(value, reference):
    return (value - reference + 180.0) % 360.0 - 180.0


def low_pass_alpha(cutoff_hz, delta_time):
    time_constant = 1.0 / (2.0 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + time_constant / delta_time)
