"""Gyro-assisted adaptive rotational filter for head-worn IMUs.

Angle-only filters (One Euro, Accela) have to infer motion from the noisy
pose itself, so they either smooth intentional movement or let tremor
through. An IMU measures angular rate directly; the fusion worker publishes
gyro-derived Euler rates with each pose and this filter uses them per axis:

* Sustained motion: a 2-pole low-pass of the *signed* rate. Tremor oscillates
  and averages out; deliberate movement, even slow aiming, does not.
* Snap motion: a held peak of the raw rate opens the filter on the first
  sample of a fast turn. Its threshold rises with the user's learned tremor
  level so strong shaking does not trip it. Brief reverse-rate twitches do
  not inherit the previous turn's snap response.
* Catch-up: large differences the gyro never saw (recenter, hold release)
  are followed quickly instead of drifting in.
* Cross-axis: movement on one axis lowers the others' motion threshold, so
  the small component of a diagonal turn is not held back, while noise on an
  idle axis still averages out and stays smoothed.

While at rest the pose is low-passed with a small soft deadzone; while
moving the filter is close to pass-through, so smoothing adds no turn lag.
"""

import math
from typing import Optional, Sequence

from src.config.config import (
    GYRO_ADAPTIVE_CATCH_UP_DEG,
    GYRO_ADAPTIVE_CROSS_AXIS_THRESHOLD_FLOOR,
    GYRO_ADAPTIVE_DEADZONE_DEG_DEFAULT,
    GYRO_ADAPTIVE_DEADZONE_DEG_RANGE,
    GYRO_ADAPTIVE_DEADZONE_DEG_STEP,
    GYRO_ADAPTIVE_FAST_RATE_MULTIPLIER,
    GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_DEFAULT,
    GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_RANGE,
    GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_STEP,
    GYRO_ADAPTIVE_MOVING_HZ,
    GYRO_ADAPTIVE_RATE_LOWPASS_HZ,
    GYRO_ADAPTIVE_RATE_RELEASE_S,
    GYRO_ADAPTIVE_REVERSAL_CONFIRM_S,
    GYRO_ADAPTIVE_REST_HZ_DEFAULT,
    GYRO_ADAPTIVE_REST_HZ_RANGE,
    GYRO_ADAPTIVE_REST_HZ_STEP,
    GYRO_ADAPTIVE_TREMOR_MULTIPLIER,
    GYRO_ADAPTIVE_TREMOR_TAU_S,
)
from .base import FilterDefinition, FilterParameter
from .utils import low_pass_alpha, wrap_delta_degrees


GYRO_ADAPTIVE_FILTER_ID = "gyro_adaptive"
GYRO_ADAPTIVE_FILTER_LABEL = "Gyro-assisted (IMU)"
GYRO_ADAPTIVE_FILTER_PARAMETERS = (
    FilterParameter(
        key="rest_hz",
        label="Rest smoothing",
        tooltip=(
            "Smoothing applied while the head is still (Hz). Lower values "
            "remove more jitter; movement is detected from the gyro and is "
            "not slowed by this setting."
        ),
        minimum=GYRO_ADAPTIVE_REST_HZ_RANGE[0],
        maximum=GYRO_ADAPTIVE_REST_HZ_RANGE[1],
        step=GYRO_ADAPTIVE_REST_HZ_STEP,
        default=GYRO_ADAPTIVE_REST_HZ_DEFAULT,
        decimals=1,
        unit="Hz",
    ),
    FilterParameter(
        key="motion_threshold_dps",
        label="Motion threshold",
        tooltip=(
            "Sustained rotation speed treated as intentional movement. "
            "Lower values follow slow aiming more closely; raise it if "
            "jitter breaks through while still."
        ),
        minimum=GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_RANGE[0],
        maximum=GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_RANGE[1],
        step=GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_STEP,
        default=GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_DEFAULT,
        decimals=1,
        unit="°/s",
    ),
    FilterParameter(
        key="deadzone_deg",
        label="Rest deadzone",
        tooltip=(
            "Ignore pose changes smaller than this while still. The "
            "deadzone fades out as soon as movement is detected."
        ),
        minimum=GYRO_ADAPTIVE_DEADZONE_DEG_RANGE[0],
        maximum=GYRO_ADAPTIVE_DEADZONE_DEG_RANGE[1],
        step=GYRO_ADAPTIVE_DEADZONE_DEG_STEP,
        default=GYRO_ADAPTIVE_DEADZONE_DEG_DEFAULT,
        decimals=2,
        unit="°",
    ),
)


def _smoothstep(value, low, high):
    fraction = min(1.0, max(0.0, (value - low) / (high - low)))
    return fraction * fraction * (3.0 - 2.0 * fraction)


class GyroAdaptivePoseFilter:
    """Smooth at rest and pass motion through, classified by gyro rate."""

    accepts_rates = True

    def __init__(self, settings):
        self.settings = dict(settings)
        self.reset()

    def reset(self):
        self._last_time = None
        self._last_raw = None
        self._unwrapped_input = None
        self._filtered_pose = None
        self._rate_lp1 = [0.0, 0.0, 0.0]
        self._rate_lp2 = [0.0, 0.0, 0.0]
        self._rate_peak = [0.0, 0.0, 0.0]
        self._rate_peak_direction = [0, 0, 0]
        self._rate_reversal_direction = [0, 0, 0]
        self._rate_reversal_duration = [0.0, 0.0, 0.0]
        self._tremor_var = [0.0, 0.0, 0.0]

    def process(
        self,
        angles: Sequence[float],
        timestamp: float,
        rates: Optional[Sequence[float]] = None,
    ):
        if len(angles) != 3:
            raise ValueError(
                "Gyro-assisted filter requires yaw, pitch, and roll"
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

        previous_input = self._unwrapped_input
        self._unwrapped_input = [
            current + wrap_delta_degrees(raw, previous)
            for current, raw, previous in zip(
                self._unwrapped_input,
                angles,
                self._last_raw,
            )
        ]
        if (
            rates is None
            or len(rates) != 3
            or any(not math.isfinite(rate) for rate in rates)
        ):
            # Without gyro data fall back to the pose's own derivative.
            rates = [
                (current - previous) / delta_time
                for current, previous in zip(
                    self._unwrapped_input,
                    previous_input,
                )
            ]

        threshold = self.settings["motion_threshold_dps"]
        rest_hz = self.settings["rest_hz"]
        deadzone = self.settings["deadzone_deg"]
        rate_alpha = low_pass_alpha(GYRO_ADAPTIVE_RATE_LOWPASS_HZ, delta_time)
        peak_decay = math.exp(-delta_time / GYRO_ADAPTIVE_RATE_RELEASE_S)
        tremor_alpha = 1.0 - math.exp(-delta_time / GYRO_ADAPTIVE_TREMOR_TAU_S)
        catch_up_low, catch_up_high = GYRO_ADAPTIVE_CATCH_UP_DEG
        reversal_threshold = max(0.5, 0.25 * threshold)
        reversing = [False, False, False]

        for axis in range(3):
            rate = rates[axis]
            self._rate_lp1[axis] += rate_alpha * (rate - self._rate_lp1[axis])
            self._rate_lp2[axis] += rate_alpha * (
                self._rate_lp1[axis] - self._rate_lp2[axis]
            )
            direction = self._rate_peak_direction[axis]
            peak_reversed = (
                direction != 0
                and rate * direction < 0.0
                and abs(rate) >= reversal_threshold
            )
            rate_opposes_motion = (
                abs(self._rate_lp2[axis]) >= threshold
                and rate * self._rate_lp2[axis] < 0.0
                and abs(rate) >= reversal_threshold
            )
            if rate_opposes_motion:
                current_direction = 1 if rate > 0.0 else -1
                if self._rate_reversal_direction[axis] != current_direction:
                    self._rate_reversal_direction[axis] = current_direction
                    self._rate_reversal_duration[axis] = 0.0
                self._rate_reversal_duration[axis] += delta_time
                if (
                    self._rate_reversal_duration[axis]
                    >= GYRO_ADAPTIVE_REVERSAL_CONFIRM_S
                ):
                    # A continued counter-turn is intentional; seed the rate
                    # smoother in its new direction after rejecting brief
                    # reverse twitches.
                    self._rate_lp1[axis] = rate
                    self._rate_lp2[axis] = rate
                    rate_opposes_motion = False
                    self._rate_reversal_duration[axis] = 0.0
            else:
                self._rate_reversal_direction[axis] = 0
                self._rate_reversal_duration[axis] = 0.0
            reversing[axis] = rate_opposes_motion
            if peak_reversed:
                # Don't let a previous fast turn keep a small opposite twitch
                # in snap mode. A genuinely fast counter-turn starts a new
                # peak immediately and still opens the filter.
                self._rate_peak[axis] = abs(rate)
                self._rate_peak_direction[axis] = 1 if rate > 0.0 else -1
            else:
                decayed_peak = self._rate_peak[axis] * peak_decay
                if abs(rate) >= decayed_peak and rate != 0.0:
                    self._rate_peak_direction[axis] = (
                        1 if rate > 0.0 else -1
                    )
                self._rate_peak[axis] = max(abs(rate), decayed_peak)

        tremor_sigmas = [math.sqrt(variance) for variance in self._tremor_var]
        snaps = []
        own_motion = []
        for axis in range(3):
            fast_threshold = max(
                GYRO_ADAPTIVE_FAST_RATE_MULTIPLIER * threshold,
                GYRO_ADAPTIVE_TREMOR_MULTIPLIER * tremor_sigmas[axis],
            )
            snap = _smoothstep(
                self._rate_peak[axis], fast_threshold, 3.0 * fast_threshold
            )
            sustained = _smoothstep(
                abs(self._rate_lp2[axis]), threshold, 3.0 * threshold
            )
            if reversing[axis]:
                sustained = 0.0
            snaps.append(snap)
            own_motion.append(max(snap, sustained))

        for axis in range(3):
            rate = rates[axis]
            # Movement on another axis lowers this axis's threshold so the
            # minor part of a diagonal turn follows; idle-axis noise still
            # averages out of the low-passed rate and stays smoothed.
            other_motion = max(
                own_motion[other] for other in range(3) if other != axis
            )
            axis_threshold = threshold * (
                1.0
                - (1.0 - GYRO_ADAPTIVE_CROSS_AXIS_THRESHOLD_FLOOR)
                * other_motion
            )
            sustained = _smoothstep(
                abs(self._rate_lp2[axis]),
                axis_threshold,
                3.0 * axis_threshold,
            )
            if reversing[axis]:
                sustained = 0.0

            # Learn tremor only when not deliberately moving, and clip
            # outliers, so turns cannot inflate the estimate.
            oscillation = min(
                abs(rate - self._rate_lp2[axis]),
                3.0 * tremor_sigmas[axis] + 2.0,
            )
            self._tremor_var[axis] += (
                (1.0 - sustained)
                * tremor_alpha
                * (oscillation * oscillation - self._tremor_var[axis])
            )

            error = self._unwrapped_input[axis] - self._filtered_pose[axis]
            catch_up = _smoothstep(abs(error), catch_up_low, catch_up_high)
            motion = max(sustained, snaps[axis], catch_up)

            magnitude = abs(error) - deadzone * (1.0 - motion)
            if magnitude <= 0.0:
                continue
            cutoff_hz = rest_hz + (GYRO_ADAPTIVE_MOVING_HZ - rest_hz) * motion
            self._filtered_pose[axis] += math.copysign(
                magnitude, error
            ) * low_pass_alpha(cutoff_hz, delta_time)

        self._last_raw = tuple(angles)
        self._last_time = timestamp
        return tuple(self._filtered_pose)

    def _set_initial_pose(self, angles, timestamp):
        self._last_raw = tuple(angles)
        self._unwrapped_input = list(angles)
        self._filtered_pose = list(angles)
        self._last_time = timestamp
        self._rate_lp1 = [0.0, 0.0, 0.0]
        self._rate_lp2 = [0.0, 0.0, 0.0]
        self._rate_peak = [0.0, 0.0, 0.0]
        self._rate_peak_direction = [0, 0, 0]
        self._rate_reversal_direction = [0, 0, 0]
        self._rate_reversal_duration = [0.0, 0.0, 0.0]


GYRO_ADAPTIVE_FILTER = FilterDefinition(
    identifier=GYRO_ADAPTIVE_FILTER_ID,
    label=GYRO_ADAPTIVE_FILTER_LABEL,
    parameters=GYRO_ADAPTIVE_FILTER_PARAMETERS,
    factory=GyroAdaptivePoseFilter,
    description=(
        "Uses the IMU's rotation-speed readings to tell when your head is "
        "still or moving. It smooths jitter while still and follows "
        "deliberate turns with little added lag."
    ),
)
