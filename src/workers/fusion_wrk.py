"""
Sensor fusion worker using complementary filter for orientation estimation.
Reads IMU data from serialQueue and routes Euler poses to the selected output queue.
"""
import time
from queue import Empty, Full
import threading
import math
from statistics import median

from src.config.config import (
     ACCEL_THRESHOLD,
     DEFAULT_CENTER_THRESHOLD,
     ACCEL_TAU_PITCH,
     ACCEL_TAU_ROLL,
     ACCEL_INNOVATION_CUTOFF_HZ,
     ACCEL_TRUST_SIGMA_G,
     ACCEL_TRUST_GYRO_RATE_DPS,
    ACCEL_TRUST_HOLD_S,
     ACCEL_INNOVATION_SLEW_CUTOFF_HZ,
     ACCEL_INNOVATION_SLEW_SIGMA,
    ACCEL_CORRECTION_MAX_DPS,
     GYRO_BIAS_TRACKING_GAIN,
     GYRO_BIAS_TRACKING_LIMIT_DPS,
    GYRO_BIAS_TRACKING_MAX_ERROR_DEG,
    GYRO_BIAS_TRACKING_SETTLE_S,
     ALPHA_DRIFT_CORRECTION,
        GYRO_BIAS_CAL_SAMPLES,
    GYRO_BIAS_CAL_MAX_ROBUST_STDDEV_DPS,
    ACCEL_BIAS_CAL_MAX_ROBUST_STDDEV_G,
    ACCEL_BIAS_CAL_MAX_LEVEL_ANGLE_DEG,
    ACCEL_BIAS_CAL_OUTLIER_SIGMAS,
    ACCEL_BIAS_CAL_OUTLIER_FLOOR_G,
    GYRO_BIAS_CAL_OUTLIER_SIGMAS,
    GYRO_BIAS_CAL_OUTLIER_FLOOR_DPS,
    GYRO_BIAS_CAL_TIMEOUT_S,
    DEFAULT_YAW_BIAS_TRACKING_ENABLED,
    YAW_BIAS_TRACKING_DWELL_S,
    YAW_BIAS_TRACKING_TIME_CONSTANT_S,
    YAW_BIAS_TRACKING_MAX_OFFSET_DPS,
    YAW_BIAS_TRACKING_MEAN_RATE_DPS,
    YAW_BIAS_TRACKING_MEAN_SPEED_DPS,
    YAW_BIAS_TRACKING_MIN_SAMPLES,
    STATIONARY_GYRO_THRESHOLD,
    STATIONARY_DEBOUNCE_S,
     DT_MIN,
     DT_MAX,
     QUEUE_GET_TIMEOUT,
     QUEUE_PUT_TIMEOUT,
     QUEUE_HIGH_WATERMARK,
     QUEUE_CRITICAL_WATERMARK,
     FUSION_LOOP_SLEEP_MS,
     DRIFT_SMOOTHING_TIME,
     DRIFT_TRANSITION_CURVE

)
from src.util.error_utils import (
    safe_queue_put,
    safe_queue_get,
    parse_imu_line,
    normalize_angle
)
from src.util.tracking_sample import TrackingSample


def _estimate_gyro_bias(samples):
    """Estimate raw gyro bias while rejecting unstable axes and isolated outliers."""
    if not samples:
        raise ValueError("Gyro bias calibration requires stationary samples")

    estimates = []
    for axis, name in enumerate(("X", "Y", "Z")):
        values = [sample[axis] for sample in samples]
        axis_median = median(values)
        deviations = [abs(value - axis_median) for value in values]
        robust_stddev = 1.4826 * median(deviations)
        if robust_stddev > GYRO_BIAS_CAL_MAX_ROBUST_STDDEV_DPS:
            raise ValueError(
                f"{name}-axis gyro readings were unstable "
                f"(robust deviation {robust_stddev:.3f} deg/s)"
            )

        outlier_limit = max(
            GYRO_BIAS_CAL_OUTLIER_SIGMAS * robust_stddev,
            GYRO_BIAS_CAL_OUTLIER_FLOOR_DPS,
        )
        inliers = [
            value for value in values
            if abs(value - axis_median) <= outlier_limit
        ]
        if not inliers:
            raise ValueError(f"No reliable {name}-axis gyro samples")
        estimates.append(math.fsum(inliers) / len(inliers))

    return tuple(estimates)


def _estimate_accel_bias(samples):
    """Estimate accel offsets for a sensor held level with +Z upward."""
    if not samples:
        raise ValueError("Sensor bias calibration requires stationary samples")

    estimates = []
    for axis, name in enumerate(("X", "Y", "Z")):
        values = [sample[axis] for sample in samples]
        axis_median = median(values)
        deviations = [abs(value - axis_median) for value in values]
        robust_stddev = 1.4826 * median(deviations)
        if robust_stddev > ACCEL_BIAS_CAL_MAX_ROBUST_STDDEV_G:
            raise ValueError(
                f"{name}-axis accelerometer readings were unstable "
                f"(robust deviation {robust_stddev:.4f} g)"
            )

        outlier_limit = max(
            ACCEL_BIAS_CAL_OUTLIER_SIGMAS * robust_stddev,
            ACCEL_BIAS_CAL_OUTLIER_FLOOR_G,
        )
        inliers = [
            value for value in values
            if abs(value - axis_median) <= outlier_limit
        ]
        if not inliers:
            raise ValueError(f"No reliable {name}-axis accelerometer samples")
        estimates.append(math.fsum(inliers) / len(inliers))

    level_angle = math.degrees(math.atan2(
        math.hypot(estimates[0], estimates[1]),
        estimates[2],
    ))
    if estimates[2] <= 0.0 or level_angle > ACCEL_BIAS_CAL_MAX_LEVEL_ANGLE_DEG:
        raise ValueError(
            "Accelerometer reference is not level with +Z upward "
            f"(measured gravity angle {level_angle:.1f} degrees)"
        )

    return estimates[0], estimates[1], estimates[2] - 1.0


class SensorBiasCalibration:
    """Collect stable gyro and known-level accelerometer samples."""

    def __init__(self, sample_count, started_at):
        if sample_count <= 0:
            raise ValueError("Sensor bias calibration sample count must be positive")
        self.sample_count = sample_count
        self.started_at = started_at
        self.samples = []
        self.last_timestamp = None
        self.rejection_reason = None
        self.accepted_sample_count = 0
        self.rejected_accel_count = 0
        self.rejected_gyro_count = 0
        self.rejected_timestamp_count = 0
        self.malformed_sample_count = 0
        self.timestamp_gap_count = 0
        self.unstable_window_count = 0

    def add_sample(self, timestamp, accel, gyro):
        """Return the complete raw sample window when it passes stability checks."""
        if self.last_timestamp is not None:
            dt = timestamp - self.last_timestamp
            if dt < DT_MIN:
                self.rejected_timestamp_count += 1
                self.last_timestamp = timestamp
                return None
            if dt > DT_MAX:
                self.timestamp_gap_count += 1
        self.last_timestamp = timestamp

        accel_mag = math.sqrt(sum(component * component for component in accel))
        gyro_mag = math.sqrt(sum(component * component for component in gyro))
        if accel_mag < 0.01 or abs(accel_mag - 1.0) >= ACCEL_THRESHOLD:
            self.rejected_accel_count += 1
            return None
        if gyro_mag >= STATIONARY_GYRO_THRESHOLD:
            self.rejected_gyro_count += 1
            return None

        self.samples.append((accel, gyro))
        self.accepted_sample_count += 1
        if len(self.samples) < self.sample_count:
            return None

        try:
            _estimate_gyro_bias([sample[1] for sample in self.samples])
            _estimate_accel_bias([sample[0] for sample in self.samples])
        except ValueError as error:
            self.rejection_reason = str(error)
            self.unstable_window_count += 1
            self.samples.clear()
            return None
        return tuple(self.samples)

    def has_timed_out(self, now):
        return now - self.started_at >= GYRO_BIAS_CAL_TIMEOUT_S

    def timeout_message(self):
        message = (
            f"Collected {self.accepted_sample_count} qualifying samples "
            f"(need {self.sample_count}); rejected {self.rejected_accel_count} "
            f"acceleration, {self.rejected_gyro_count} gyro, "
            f"{self.rejected_timestamp_count} timestamp, and "
            f"{self.malformed_sample_count} malformed samples; observed "
            f"{self.timestamp_gap_count} timestamp gaps and "
            f"{self.unstable_window_count} unstable windows"
        )
        if self.rejection_reason:
            message += f"; last stability rejection: {self.rejection_reason}"
        return message

    def note_malformed_sample(self):
        self.malformed_sample_count += 1


def _finish_sensor_bias_calibration(
    filter_instance, status_queue, log_queue, samples=None, failure_reason=None
):
    from src.util.log_utils import log_info, log_warning

    if samples is not None:
        filter_instance.calibrate_sensor_bias(samples)
        calibration = {
            "gyro": (
                filter_instance.gyro_bias_roll,
                filter_instance.gyro_bias_pitch,
                filter_instance.gyro_bias_yaw,
            ),
            "accel": (
                filter_instance.accel_bias_x,
                filter_instance.accel_bias_y,
                filter_instance.accel_bias_z,
            ),
        }
        if not safe_queue_put(
            status_queue,
            ("sensor_calibration_data", calibration),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            log_warning(
                log_queue,
                "Fusion Worker",
                "Could not publish sensor calibration values to the GUI",
            )
        if not safe_queue_put(
            status_queue,
            ("sensor_calibration_source", "fresh"),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            log_warning(
                log_queue,
                "Fusion Worker",
                "Could not publish sensor calibration source to the GUI",
            )
        log_info(
            log_queue,
            "Fusion Worker",
            f"Runtime sensor biases recalibrated from {len(samples)} stable samples: "
            f"X={filter_instance.gyro_bias_roll:.6f}, "
            f"Y={filter_instance.gyro_bias_pitch:.6f}, "
            f"Z={filter_instance.gyro_bias_yaw:.6f} deg/s; "
            f"accel X={filter_instance.accel_bias_x:.5f}, "
            f"Y={filter_instance.accel_bias_y:.5f}, "
            f"Z={filter_instance.accel_bias_z:.5f} g",
        )
    else:
        log_warning(
            log_queue,
            "Fusion Worker",
            f"Sensor bias calibration failed; previous biases were retained: {failure_reason}",
        )
        if not safe_queue_put(
            status_queue,
            ("sensor_calibration_failed", failure_reason),
            timeout=QUEUE_PUT_TIMEOUT,
        ):
            log_warning(
                log_queue,
                "Fusion Worker",
                "Could not notify the GUI that sensor bias calibration failed",
            )

    if not safe_queue_put(
        status_queue, ("sensor_calibrating", False), timeout=QUEUE_PUT_TIMEOUT
    ):
        log_warning(log_queue, "Fusion Worker", "Could not publish sensor calibration completion")
    if not safe_queue_put(
        status_queue,
        ("sensor_calibrated", bool(filter_instance.sensor_calibrated)),
        timeout=QUEUE_PUT_TIMEOUT,
    ):
        log_warning(log_queue, "Fusion Worker", "Could not publish sensor calibration state")


def _publish_output_pose(output_queue, pose):
    """Publish one frame to the output router's bounded pose queue."""
    if output_queue is None:
        return False
    try:
        output_queue.put_nowait(pose)
        return True
    except Full:
        return False


class QuaternionComplementaryFilter:
    """Complementary filter implemented with quaternion integration.

    This provides the same external API as `ComplementaryFilter` but uses
    quaternions for orientation integration and accel-based roll/pitch
    correction.
    """

    def __init__(self, accel_threshold=ACCEL_THRESHOLD, center_threshold=DEFAULT_CENTER_THRESHOLD, center_threshold_yaw=None, center_threshold_pitch=None, center_threshold_roll=None, logQueue=None):
        # Accelerometer tilt correction time constants (seconds); math.inf
        # disables accel correction for that axis.
        self.accel_tau_pitch = ACCEL_TAU_PITCH
        self.accel_tau_roll = ACCEL_TAU_ROLL
        self.accel_innovation_cutoff_hz = ACCEL_INNOVATION_CUTOFF_HZ
        self.accel_trust_sigma_g = ACCEL_TRUST_SIGMA_G
        self.accel_trust_gyro_rate_dps = ACCEL_TRUST_GYRO_RATE_DPS
        self.accel_trust_hold_s = ACCEL_TRUST_HOLD_S
        self.gyro_bias_tracking_gain = GYRO_BIAS_TRACKING_GAIN
        self.gyro_bias_tracking_limit = math.radians(GYRO_BIAS_TRACKING_LIMIT_DPS)
        self.gyro_bias_tracking_max_error = math.sin(
            math.radians(GYRO_BIAS_TRACKING_MAX_ERROR_DEG)
        )
        self._reset_tilt_state()
        self.euler_rates = (0.0, 0.0, 0.0)
        self.alpha_drift = ALPHA_DRIFT_CORRECTION
        self.drift_smoothing_time = DRIFT_SMOOTHING_TIME
        self.drift_curve_type = DRIFT_TRANSITION_CURVE  # Load from config
        self.drift_correction_strength = 0.3
        self.accel_threshold = accel_threshold
        
        # Use separate thresholds if provided, otherwise use default for all
        self.center_threshold_yaw = center_threshold_yaw if center_threshold_yaw is not None else center_threshold
        self.center_threshold_pitch = center_threshold_pitch if center_threshold_pitch is not None else center_threshold
        self.center_threshold_roll = center_threshold_roll if center_threshold_roll is not None else center_threshold
        
        # Keep backward compatibility
        self.center_threshold = center_threshold
        self.gyro_bias_roll = 0.0
        self.gyro_bias_pitch = 0.0
        self.gyro_bias_yaw = 0.0
        self.yaw_bias_tracking_enabled = DEFAULT_YAW_BIAS_TRACKING_ENABLED
        self._yaw_bias_tracking_reference = 0.0
        self._yaw_bias_tracking_stationary_start = None
        self._yaw_bias_tracking_samples = []
        self.accel_bias_x = 0.0
        self.accel_bias_y = 0.0
        self.accel_bias_z = 0.0
        self.output_origin = (0.0, 0.0, 0.0)
        self.sensor_calibrated = False
        self._stationary_start = None
        self._last_stationary = False
        self._drift_correction_start = None
        self._gyro_stationary_threshold = STATIONARY_GYRO_THRESHOLD
        self._stationary_debounce_s = STATIONARY_DEBOUNCE_S
        # Axis inversion settings
        self.invert_yaw = False
        self.invert_pitch = False
        self.invert_roll = False
        # Quaternion as a plain 4-element tuple keeps the 250Hz update loop light.
        self.q = (1.0, 0.0, 0.0, 0.0)
        self.last_time = None
        self.logQueue = logQueue
        self.orientation_held = False
        self._held_output_q = None
        self._orientation_resume_started_at = None
        self._last_output_q = None

    # --- Quaternion helper methods ---
    def _quat_normalize(self, q):
        w, x, y, z = q
        n = math.sqrt(w*w + x*x + y*y + z*z)
        if n == 0:
            return (1.0, 0.0, 0.0, 0.0)
        return (w / n, x / n, y / n, z / n)

    def _quat_mul(self, a, b):
        # Hamilton product
        w1, x1, y1, z1 = a
        w2, x2, y2, z2 = b
        return (
            w1*w2 - x1*x2 - y1*y2 - z1*z2,
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2
        )

    # self.q is the physical sensor orientation in the IMU's right-handed frame
    # (shared by accel and gyro). The application's yaw sign is the opposite
    # of the IMU's, so yaw is negated only when converting to/from Euler
    # angles. Negating gz during integration instead would mirror the gyro
    # frame against the accelerometer and turn yaw into pitch/roll whenever
    # the sensor is not perfectly level.
    def _euler_from_quat(self, q):
        # returns application (yaw, pitch, roll) in degrees
        w, x, y, z = q
        # roll (x-axis rotation)
        t0 = 2.0 * (w * x + y * z)
        t1 = 1.0 - 2.0 * (x * x + y * y)
        roll = math.degrees(math.atan2(t0, t1))

        # pitch (y-axis)
        t2 = 2.0 * (w * y - z * x)
        t2 = max(-1.0, min(1.0, t2))
        pitch = math.degrees(math.asin(t2))

        # yaw (z-axis)
        t3 = 2.0 * (w * z + x * y)
        t4 = 1.0 - 2.0 * (y * y + z * z)
        yaw = -math.degrees(math.atan2(t3, t4))

        return yaw, pitch, roll

    def _quat_from_euler(self, yaw, pitch, roll):
        # input: application Euler angles in degrees
        yaw = -yaw
        cy = math.cos(math.radians(yaw) * 0.5)
        sy = math.sin(math.radians(yaw) * 0.5)
        cp = math.cos(math.radians(pitch) * 0.5)
        sp = math.sin(math.radians(pitch) * 0.5)
        cr = math.cos(math.radians(roll) * 0.5)
        sr = math.sin(math.radians(roll) * 0.5)

        w = cr * cp * cy + sr * sp * sy
        x = sr * cp * cy - cr * sp * sy
        y = cr * sp * cy + sr * cp * sy
        z = cr * cp * sy - sr * sp * cy
        return (w, x, y, z)

    @staticmethod
    def _euler_rates_from_body(gx, gy, gz, pitch_deg, roll_deg):
        """Application (yaw, pitch, roll) rates in deg/s from body rates."""
        pitch = math.radians(pitch_deg)
        roll = math.radians(roll_deg)
        sin_roll = math.sin(roll)
        cos_roll = math.cos(roll)
        # ZYX kinematics are singular at +/-90 deg pitch; clamp the divisor.
        cos_pitch = math.cos(pitch)
        if abs(cos_pitch) < 1e-3:
            cos_pitch = math.copysign(1e-3, cos_pitch)
        vertical = sin_roll * gy + cos_roll * gz
        roll_rate = gx + math.sin(pitch) / cos_pitch * vertical
        pitch_rate = cos_roll * gy - sin_roll * gz
        yaw_rate = -vertical / cos_pitch
        return yaw_rate, pitch_rate, roll_rate

    def recenter_to_current(self):
        """Set the output origin to the filter's current full orientation."""
        self.output_origin = self._euler_from_quat(self.q)
        self._drift_correction_start = None
        return self.output_origin

    def clear_output_origin(self):
        """Restore the zero-angle output origin."""
        self.output_origin = (0.0, 0.0, 0.0)

    def _slerp(self, a, b, t):
        """Spherical linear interpolation between quaternions for smoother drift correction.
        
        Provides much smoother interpolation than NLERP, especially for small corrections.
        """
        # Ensure we take the shorter path by checking dot product
        dot = a[0]*b[0] + a[1]*b[1] + a[2]*b[2] + a[3]*b[3]
        if dot < 0.0:
            b = (-b[0], -b[1], -b[2], -b[3])  # Flip to shorter path
            dot = -dot
        
        # If quaternions are very close, use linear interpolation to avoid division by zero
        if dot > 0.9995:
            q = tuple((1.0 - t) * ai + t * bi for ai, bi in zip(a, b))
            return self._quat_normalize(q)
        
        # Calculate angle between quaternions
        theta = math.acos(max(-1.0, min(1.0, dot)))
        sin_theta = math.sin(theta)
        
        # Spherical interpolation
        s0 = math.sin((1.0 - t) * theta) / sin_theta
        s1 = math.sin(t * theta) / sin_theta
        q = tuple(s0 * ai + s1 * bi for ai, bi in zip(a, b))
        return self._quat_normalize(q)

    def set_orientation_hold(self, held, now=None):
        """Freeze the published orientation or begin a smooth return to tracking."""
        if held:
            if self.orientation_held:
                return
            self._held_output_q = self._last_output_q or self.q
            self.orientation_held = True
            self._orientation_resume_started_at = None
            return

        if self.orientation_held:
            self.orientation_held = False
            self._orientation_resume_started_at = now

    def apply_orientation_hold(self, yaw, pitch, roll, now=None):
        """Apply hold/resume smoothing to an already filtered Euler orientation."""
        sensor_q = self._quat_normalize(self._quat_from_euler(yaw, pitch, roll))

        if self.orientation_held:
            output_q = self._held_output_q or sensor_q
            output_euler = self._euler_from_quat(output_q)
        elif self._held_output_q is not None:
            if self._orientation_resume_started_at is None:
                self._orientation_resume_started_at = (
                    time.perf_counter() if now is None else now
                )
            current_time = time.perf_counter() if now is None else now
            smoothing_time = max(float(self.drift_smoothing_time), 1e-6)
            correction_strength = max(float(self.drift_correction_strength), 1e-6)
            # Match drift correction's 0.3 reference strength for resume timing.
            resume_duration = smoothing_time * 0.3 / correction_strength
            progress = max(
                0.0,
                min(
                    (current_time - self._orientation_resume_started_at)
                    / resume_duration,
                    1.0,
                ),
            )
            if progress >= 1.0:
                output_q = sensor_q
                output_euler = (yaw, pitch, roll)
                self._held_output_q = None
                self._orientation_resume_started_at = None
            else:
                if self.drift_curve_type == 'exponential':
                    eased_progress = (1.0 - math.exp(-3.0 * progress)) / (
                        1.0 - math.exp(-3.0)
                    )
                elif self.drift_curve_type == 'cosine':
                    eased_progress = 0.5 * (1.0 - math.cos(math.pi * progress))
                elif self.drift_curve_type == 'quadratic':
                    eased_progress = progress * progress
                else:
                    eased_progress = progress
                output_q = self._slerp(
                    self._held_output_q, sensor_q, eased_progress
                )
                output_euler = self._euler_from_quat(output_q)
        else:
            output_q = sensor_q
            output_euler = (yaw, pitch, roll)

        self._last_output_q = output_q
        return output_euler

    def _nlerp(self, a, b, t):
        """Normalized linear interpolation between quaternions.
        
        Handles the case where quaternions represent the same rotation
        but have opposite signs (q and -q represent the same rotation).
        """
        # Ensure we take the shorter path by checking dot product
        dot = a[0]*b[0] + a[1]*b[1] + a[2]*b[2] + a[3]*b[3]
        if dot < 0.0:
            b = (-b[0], -b[1], -b[2], -b[3])  # Flip to shorter path
        
        # Linear interpolation
        q = tuple((1.0 - t) * ai + t * bi for ai, bi in zip(a, b))
        return self._quat_normalize(q)

    def _calculate_drift_factor(self, dt, elapsed_time):
        """Integrate the engagement ramp into a sample-rate-independent gain."""
        def integrated_ramp(progress):
            ramp_progress = min(progress, 1.0)
            if self.drift_curve_type == 'exponential':
                integral = (
                    ramp_progress + math.expm1(-3.0 * ramp_progress) / 3.0
                ) / (-math.expm1(-3.0))
            elif self.drift_curve_type == 'cosine':
                integral = 0.5 * (
                    ramp_progress - math.sin(math.pi * ramp_progress) / math.pi
                )
            elif self.drift_curve_type == 'quadratic':
                integral = ramp_progress ** 3 / 3.0
            else:
                integral = 0.5 * ramp_progress ** 2
            # Once fully engaged, maintain attraction instead of ending it.
            return integral + max(progress - 1.0, 0.0)

        progress = max(elapsed_time, 0.0) / self.drift_smoothing_time
        previous_progress = max(elapsed_time - dt, 0.0) / self.drift_smoothing_time
        integrated_rate = max(
            integrated_ramp(progress) - integrated_ramp(previous_progress),
            0.0,
        )
        return -math.expm1(
            -integrated_rate * (self.drift_correction_strength / 0.3)
        )

    def calibrate_gyro_bias(self, samples):
        """Compatibility helper to replace gyro biases from raw gyro samples."""
        if not samples:
            raise ValueError("Gyro bias calibration requires stationary samples")
        biases = _estimate_gyro_bias(samples)
        self.gyro_bias_roll, self.gyro_bias_pitch, self.gyro_bias_yaw = biases
        self._yaw_bias_tracking_reference = self.gyro_bias_yaw
        self._reset_yaw_bias_tracking_window()
        self._reset_tilt_state()

    def calibrate_sensor_bias(self, samples):
        """Atomically replace gyro and level-reference accelerometer offsets."""
        if not samples:
            raise ValueError("Sensor bias calibration requires stationary samples")
        gyro_bias = _estimate_gyro_bias([sample[1] for sample in samples])
        accel_bias = _estimate_accel_bias([sample[0] for sample in samples])
        (
            self.gyro_bias_roll,
            self.gyro_bias_pitch,
            self.gyro_bias_yaw,
        ) = gyro_bias
        self.accel_bias_x, self.accel_bias_y, self.accel_bias_z = accel_bias
        self._yaw_bias_tracking_reference = self.gyro_bias_yaw
        self._reset_yaw_bias_tracking_window()
        self._reset_tilt_state()
        self.sensor_calibrated = True

    def reset_sensor_biases(self):
        """Clear all manually estimated gyro and accelerometer offsets."""
        self.gyro_bias_roll = 0.0
        self.gyro_bias_pitch = 0.0
        self.gyro_bias_yaw = 0.0
        self.accel_bias_x = 0.0
        self.accel_bias_y = 0.0
        self.accel_bias_z = 0.0
        self._yaw_bias_tracking_reference = 0.0
        self._reset_yaw_bias_tracking_window()
        self._reset_tilt_state(reset_bias=True)
        self.sensor_calibrated = False

    def restore_sensor_biases(self, gyro_bias, accel_bias):
        """Restore validated offsets from persisted calibration preferences."""
        if (
            not isinstance(gyro_bias, (list, tuple))
            or len(gyro_bias) != 3
            or not isinstance(accel_bias, (list, tuple))
            or len(accel_bias) != 3
        ):
            raise ValueError("Sensor biases must contain three gyro and accel values")

        gyro_values = tuple(float(value) for value in gyro_bias)
        accel_values = tuple(float(value) for value in accel_bias)
        if not all(math.isfinite(value) for value in gyro_values + accel_values):
            raise ValueError("Sensor biases must be finite")
        if any(abs(value) > 5.0 for value in gyro_values):
            raise ValueError("Persisted gyro bias exceeds calibration limits")
        if any(abs(value) > 1.0 for value in accel_values):
            raise ValueError("Persisted accelerometer bias exceeds calibration limits")

        (
            self.gyro_bias_roll,
            self.gyro_bias_pitch,
            self.gyro_bias_yaw,
        ) = gyro_values
        self.accel_bias_x, self.accel_bias_y, self.accel_bias_z = accel_values
        self._yaw_bias_tracking_reference = self.gyro_bias_yaw
        self._reset_yaw_bias_tracking_window()
        self._reset_tilt_state()
        self.sensor_calibrated = True

    def _reset_yaw_bias_tracking_window(self):
        self._yaw_bias_tracking_stationary_start = None
        self._yaw_bias_tracking_samples = []

    def set_yaw_bias_tracking(self, enabled):
        """Enable stationary-only, rate-limited adaptation of the yaw gyro bias."""
        self.yaw_bias_tracking_enabled = bool(enabled)
        self._reset_yaw_bias_tracking_window()

    def _update_yaw_bias_tracking(
        self, raw_gyro, corrected_gyro, accel_ok, is_stationary, timestamp
    ):
        if (
            not self.yaw_bias_tracking_enabled
            or not is_stationary
            or not accel_ok
        ):
            self._reset_yaw_bias_tracking_window()
            return

        if self._yaw_bias_tracking_stationary_start is None:
            self._yaw_bias_tracking_stationary_start = timestamp
            self._yaw_bias_tracking_samples = []

        self._yaw_bias_tracking_samples.append((corrected_gyro, raw_gyro))
        window_duration = (
            timestamp - self._yaw_bias_tracking_stationary_start
        )
        if window_duration < YAW_BIAS_TRACKING_DWELL_S:
            return

        samples = self._yaw_bias_tracking_samples
        self._reset_yaw_bias_tracking_window()
        if len(samples) < YAW_BIAS_TRACKING_MIN_SAMPLES:
            return

        mean_rate = tuple(
            math.fsum(sample[0][axis] for sample in samples) / len(samples)
            for axis in range(3)
        )
        mean_rate_magnitude = math.sqrt(sum(rate * rate for rate in mean_rate))
        mean_speed = math.fsum(
            math.sqrt(sum(rate * rate for rate in sample[0]))
            for sample in samples
        ) / len(samples)
        if (
            mean_rate_magnitude > YAW_BIAS_TRACKING_MEAN_RATE_DPS
            or mean_speed > YAW_BIAS_TRACKING_MEAN_SPEED_DPS
        ):
            return

        try:
            target_bias = _estimate_gyro_bias(
                [sample[1] for sample in samples]
            )[2]
        except ValueError:
            return

        max_offset = YAW_BIAS_TRACKING_MAX_OFFSET_DPS
        target_bias = max(
            self._yaw_bias_tracking_reference - max_offset,
            min(
                self._yaw_bias_tracking_reference + max_offset,
                target_bias,
            ),
        )
        alpha = -math.expm1(
            -window_duration / YAW_BIAS_TRACKING_TIME_CONSTANT_S
        )
        self.gyro_bias_yaw += alpha * (target_bias - self.gyro_bias_yaw)

    def _correct_gyro(self, gyro):
        return (
            gyro[0] - self.gyro_bias_roll,
            gyro[1] - self.gyro_bias_pitch,
            gyro[2] - self.gyro_bias_yaw,
        )

    def _correct_accel(self, accel):
        return (
            accel[0] - self.accel_bias_x,
            accel[1] - self.accel_bias_y,
            accel[2] - self.accel_bias_z,
        )

    def _accel_to_rp(self, accel):
        ax, ay, az = accel
        roll = math.atan2(ay, az) * 180.0 / math.pi
        pitch = math.atan2(-ax, math.sqrt(ay*ay + az*az)) * 180.0 / math.pi
        return roll, pitch

    def _reset_tilt_state(self, reset_bias=True):
        """Clear the tilt-error low-pass state (and optionally the online bias)."""
        self._tilt_err_lp1 = [0.0, 0.0, 0.0]
        self._tilt_rate_envelope = 0.0
        self._tilt_err_lp2 = [0.0, 0.0, 0.0]
        self._tilt_previous_error = None
        self._tilt_slew_error_lp = [0.0, 0.0, 0.0]
        self._tilt_slew_previous_error = None
        self._tilt_slew_envelope = 0.0
        self._tilt_bias_settle_s = 0.0
        if reset_bias or not hasattr(self, '_tilt_bias'):
            self._tilt_bias = [0.0, 0.0, 0.0]

    def _integrate_body_rates(self, q, wx, wy, wz, dt):
        """Rotate q by body rates (rad/s) over dt using the exact rotation."""
        rx, ry, rz = wx * dt, wy * dt, wz * dt
        angle = math.sqrt(rx*rx + ry*ry + rz*rz)
        if angle > 1e-12:
            s = math.sin(0.5 * angle) / angle
            dq = (math.cos(0.5 * angle), rx * s, ry * s, rz * s)
        else:
            dq = (1.0, 0.5 * rx, 0.5 * ry, 0.5 * rz)
        return self._quat_normalize(self._quat_mul(q, dq))

    def _tilt_correction_rates(self, accel, accel_mag, gyro_mag_dps, dt):
        """Return body-rate corrections (rad/s) that pull pitch/roll toward accel.

        The error between measured gravity and the gyro-predicted gravity
        direction is trust-gated before and after low-pass filtering. Bounded
        proportional feedback corrects tilt; a settled, small-error integral
        term tracks residual gyro bias.
        """
        if self.accel_trust_hold_s > 0:
            decay = math.exp(-dt / self.accel_trust_hold_s)
            self._tilt_rate_envelope = max(gyro_mag_dps, self._tilt_rate_envelope * decay)
        else:
            self._tilt_rate_envelope = gyro_mag_dps

        gain_roll = 0.0 if self.accel_tau_roll <= 0 else 1.0 / self.accel_tau_roll
        gain_pitch = 0.0 if self.accel_tau_pitch <= 0 else 1.0 / self.accel_tau_pitch
        if gain_roll == 0.0 and gain_pitch == 0.0:
            self._reset_tilt_state()
            return 0.0, 0.0, 0.0
        if accel_mag < 0.1:
            self._tilt_err_lp1 = [0.0, 0.0, 0.0]
            self._tilt_err_lp2 = [0.0, 0.0, 0.0]
            self._tilt_previous_error = None
            self._tilt_slew_error_lp = [0.0, 0.0, 0.0]
            self._tilt_slew_previous_error = None
            self._tilt_bias_settle_s = 0.0
            return 0.0, 0.0, 0.0

        w, x, y, z = self.q
        # Predicted gravity direction in the body frame: R^T * (0, 0, 1).
        vx = 2.0 * (x*z - w*y)
        vy = 2.0 * (w*x + y*z)
        vz = w*w - x*x - y*y + z*z
        ax, ay, az = (component / accel_mag for component in accel)
        error = (ay*vz - az*vy, az*vx - ax*vz, ax*vy - ay*vx)
        raw_error = error
        self._tilt_previous_error = raw_error
        # Differentiate a low-passed innovation so sensor noise is not
        # amplified into a false-motion signal at the IMU sample rate.
        previous_slew_error = self._tilt_slew_previous_error
        if previous_slew_error is None:
            self._tilt_slew_error_lp = list(raw_error)
        else:
            slew_k = -math.expm1(
                -2.0 * math.pi * ACCEL_INNOVATION_SLEW_CUTOFF_HZ * dt
            )
            for i in range(3):
                self._tilt_slew_error_lp[i] += slew_k * (
                    raw_error[i] - self._tilt_slew_error_lp[i]
                )
        filtered_slew_error = tuple(self._tilt_slew_error_lp)
        self._tilt_slew_previous_error = filtered_slew_error
        slew = 0.0 if previous_slew_error is None else math.sqrt(sum(
            ((current - previous) / dt) ** 2
            for current, previous in zip(
                filtered_slew_error, previous_slew_error
            )
        ))
        decay = math.exp(-dt / self.accel_trust_hold_s) if self.accel_trust_hold_s > 0 else 0.0
        self._tilt_slew_envelope = max(slew, self._tilt_slew_envelope * decay)

        deviation = (accel_mag - 1.0) / self.accel_trust_sigma_g
        rate = self._tilt_rate_envelope / self.accel_trust_gyro_rate_dps
        innovation_slew = self._tilt_slew_envelope / ACCEL_INNOVATION_SLEW_SIGMA
        weight = math.exp(-0.5 * (
            deviation * deviation + rate * rate + innovation_slew * innovation_slew
        ))

        if self.accel_innovation_cutoff_hz > 0:
            k = -math.expm1(-2.0 * math.pi * self.accel_innovation_cutoff_hz * dt)
            lp1, lp2 = self._tilt_err_lp1, self._tilt_err_lp2
            for i in range(3):
                # Never retain a correction pointing against the current
                # observation, or a larger error after a disturbance ends.
                for state in (lp1, lp2):
                    state[i] = math.copysign(
                        min(abs(state[i]), abs(raw_error[i])), raw_error[i]
                    ) if state[i] * raw_error[i] > 0.0 else 0.0
                lp1[i] += k * (weight * raw_error[i] - lp1[i])
                lp2[i] += k * (lp1[i] - lp2[i])
            error = lp2

        gains = (gain_roll, gain_pitch, 0.5 * (gain_roll + gain_pitch))
        error_mag = math.sqrt(sum(component * component for component in raw_error))
        learn_bias = (
            weight > 0.95
            and gyro_mag_dps < self._gyro_stationary_threshold
            and error_mag < self.gyro_bias_tracking_max_error
        )
        self._tilt_bias_settle_s = self._tilt_bias_settle_s + dt if learn_bias else 0.0
        learn_bias = learn_bias and self._tilt_bias_settle_s >= GYRO_BIAS_TRACKING_SETTLE_S
        ki = self.gyro_bias_tracking_gain * weight * dt if learn_bias else 0.0
        limit = self.gyro_bias_tracking_limit
        bias = self._tilt_bias
        for i in range(3):
            if gains[i] > 0.0:
                bias[i] = max(-limit, min(limit, bias[i] + ki * error[i]))
            else:
                bias[i] = 0.0
        proportional = [weight * gains[i] * error[i] for i in range(3)]
        magnitude = math.sqrt(sum(component * component for component in proportional))
        scale = min(1.0, math.radians(ACCEL_CORRECTION_MAX_DPS) / magnitude) if magnitude > 0 else 1.0
        return tuple(scale * proportional[i] + bias[i] for i in range(3))

    def update(self, gyro, accel, timestamp):
        # Gyro-derived Euler rates for output filters; zero unless integrated.
        self.euler_rates = (0.0, 0.0, 0.0)
        accel = self._correct_accel(accel)
        # Maintain same semantics as ComplementaryFilter.update
        if self.last_time is None:
            try:
                from src.util.log_utils import log_info
                log_info(self.logQueue, "Fusion", f"Initializing quaternion baseline at {timestamp}")
            except Exception:
                pass
            # Initialization logged via log_info; avoid console prints in worker
            self.last_time = timestamp
            self.q = (1.0, 0.0, 0.0, 0.0)
            accel_mag = math.sqrt(sum(component * component for component in accel))
            if abs(accel_mag - 1.0) < self.accel_threshold:
                # Start level with gravity so slow tilt correction need not converge.
                roll, pitch = self._accel_to_rp(accel)
                self.q = self._quat_normalize(self._quat_from_euler(0.0, pitch, roll))
            self._reset_tilt_state(reset_bias=False)
            return 0.0, 0.0, 0.0, False, False

        dt = timestamp - self.last_time
        if dt < 0:
            from src.util.log_utils import log_warning
            log_warning(self.logQueue, "Fusion", "Sensor clock moved backwards; restarting timing state")
            self.last_time = timestamp
            self._reset_tilt_state(reset_bias=False)
            if self._orientation_resume_started_at is not None:
                self._orientation_resume_started_at = timestamp
            self._stationary_start = None
            self._reset_yaw_bias_tracking_window()
            self._drift_correction_start = None
        if dt <= 0 or dt < DT_MIN:
            y, p, r = self._euler_from_quat(self.q)
            return y, p, r, False, False
        if dt > DT_MAX:
            try:
                from src.util.log_utils import log_warning
                log_warning(self.logQueue, "Fusion", f"Large dt: {dt:.3f}s, resetting quaternion baseline")
            except Exception:
                pass
            self.last_time = timestamp
            self._reset_tilt_state(reset_bias=False)
            self._stationary_start = None
            self._reset_yaw_bias_tracking_window()
            self._drift_correction_start = None
            y, p, r = self._euler_from_quat(self.q)
            return y, p, r, False, False

        self.last_time = timestamp

        gx, gy, gz = self._correct_gyro(gyro)

        # Body rates in rad/s, in the IMU frame (see _euler_from_quat).
        wx = math.radians(gx)
        wy = math.radians(gy)
        wz = math.radians(gz)

        ax, ay, az = accel
        accel_mag = math.sqrt(ax*ax + ay*ay + az*az)
        gyro_mag = math.sqrt(gx*gx + gy*gy + gz*gz)

        # Center assist deliberately pulls the pose away from the gyro/accel
        # solution; that disagreement must not be learned as gyro bias.
        self.q = self._integrate_body_rates(self.q, wx, wy, wz, dt)
        cx, cy, cz = self._tilt_correction_rates(
            accel, accel_mag, gyro_mag, dt
        )
        q = self._integrate_body_rates(
            self.q, cx, cy, cz, dt
        )

        accel_ok = False
        if accel_mag >= 0.01:
            accel_ok = abs(accel_mag - 1.0) < self.accel_threshold

        candidate_stationary = accel_ok and (gyro_mag < self._gyro_stationary_threshold)
        if candidate_stationary:
            if self._stationary_start is None:
                self._stationary_start = timestamp
            is_stationary = (timestamp - self._stationary_start) >= self._stationary_debounce_s
        else:
            self._stationary_start = None
            is_stationary = False

        self._update_yaw_bias_tracking(
            gyro, (gx, gy, gz), accel_ok, is_stationary, timestamp
        )

        # Helper function for angle differences
        def _angle_diff_fast(a, b):
            """Optimized smallest angle difference using modulo (O(1) vs O(n))."""
            diff = (a - b + 180.0) % 360.0 - 180.0
            return abs(diff)

        yaw_est, pitch_est, roll_est = self._euler_from_quat(q)

        # Center assistance follows the same output origin used by recentering.
        target_yaw, target_pitch, target_roll = self.output_origin
        is_near_center = (
            _angle_diff_fast(yaw_est, target_yaw) < self.center_threshold_yaw
            and _angle_diff_fast(pitch_est, target_pitch) < self.center_threshold_pitch
            and _angle_diff_fast(roll_est, target_roll) < self.center_threshold_roll
        )
        
        # Apply drift correction when stationary and near center
        drift_active = False
        if is_stationary and is_near_center:
            # Track drift correction start time
            if self._drift_correction_start is None:
                self._drift_correction_start = timestamp
            
            elapsed_time = timestamp - self._drift_correction_start
            
            correction_strength = self._calculate_drift_factor(dt, elapsed_time)
            
            # Extract current absolute angles (already known from above; q was
            # either just built from these or decomposed once already).
            current_yaw, current_pitch, current_roll = yaw_est, pitch_est, roll_est

            # Correct yaw only; accelerometer feedback owns pitch and roll.
            rel_yaw = normalize_angle(current_yaw - target_yaw)
            corrected_rel_yaw = rel_yaw * (1.0 - correction_strength)
            corrected_yaw = normalize_angle(corrected_rel_yaw + target_yaw)
            corrected_pitch = pitch_est
            corrected_roll = roll_est
            q = self._quat_from_euler(
                corrected_yaw, corrected_pitch, corrected_roll
            )
            drift_active = True
        else:
            # Reset drift correction timer when not correcting
            self._drift_correction_start = None

        q = self._quat_normalize(q)
        self.q = q

        # q above was built directly from corrected_*/yaw_est,pitch_est,roll_est
        # in every path, so reuse those angles instead of a 3rd trig decomposition.
        if drift_active:
            yaw, pitch, roll = corrected_yaw, corrected_pitch, corrected_roll
        else:
            yaw, pitch, roll = yaw_est, pitch_est, roll_est
        self.euler_rates = self._euler_rates_from_body(gx, gy, gz, pitch, roll)
        # Store angles for next frame's smoothness calculation
        self._last_yaw = yaw
        self._last_pitch = pitch
        self._last_roll = roll
        # normalize angles
        yaw = normalize_angle(yaw)
        pitch = normalize_angle(pitch)
        roll = normalize_angle(roll)

        yaw = normalize_angle(yaw - self.output_origin[0])
        pitch = normalize_angle(pitch - self.output_origin[1])
        roll = normalize_angle(roll - self.output_origin[2])

        return yaw, pitch, roll, drift_active, is_stationary

    def reset(self):
        """Reset quaternion orientation to identity (zero rotation)."""
        self.q = (1.0, 0.0, 0.0, 0.0)
        self.clear_output_origin()
        self._drift_correction_start = None
        self._reset_yaw_bias_tracking_window()
        self._reset_tilt_state(reset_bias=False)
    
    


def run_worker(
    serialQueue,
    outputQueue,
    eulerDisplayQueue,
    controlQueue,
    statusQueue,
    stop_event,
    logQueue=None,
    uiStatusQueue=None,
):
    """
    Read IMU data and publish poses to the currently selected output queue.
    """
    from src.util.log_utils import log_info, log_error, log_warning
    from src.util.timing_utils import enable_high_res_timer, disable_high_res_timer, raise_process_priority

    # Windows rounds time.sleep() up to the ~15.6ms system clock tick unless
    # this process requests higher resolution; must be set per-process.
    enable_high_res_timer()
    raise_process_priority()

    # All logging should use util.log_utils (log_info/log_error). Legacy
    # print() calls have been removed; no redirecting of builtins.print is used.

    log_info(logQueue, "Fusion Worker", "Starting complementary filter")
    
    # Initialize quaternion filter with defaults from config. The GUI can update
    # filter parameters at runtime via the controlQueue.
    def _create_filter():
        return QuaternionComplementaryFilter(accel_threshold=ACCEL_THRESHOLD,
                                            center_threshold=DEFAULT_CENTER_THRESHOLD,
                                            logQueue=logQueue)

    filter = _create_filter()
    # Calibration is triggered manually after the sensor is held level and still.
    try:
        filter.sensor_calibrated = False
        safe_queue_put(statusQueue, ('sensor_calibrated', False), timeout=QUEUE_PUT_TIMEOUT)
        safe_queue_put(statusQueue, ('processing', 'inactive'), timeout=QUEUE_PUT_TIMEOUT)
    except Exception:
        # Best-effort: don't block startup if statusQueue is unavailable
        pass

    # Track processing state to avoid spam
    processing_active = False

    # Track data flow timeout
    last_data_time = None
    data_timeout = 2.0  # Send inactive after 2 seconds without data

    # Track status values to only send updates when they change
    last_drift_active = None
    last_stationary = None
    sensor_calibration = None

    # Rate-limited counters for malformed/non-finite inputs to avoid log spam
    malformed_counter = 0
    nonfinite_euler_counter = 0
    output_timing_reset = False

    # Loop counter used to throttle low-frequency IPC work (control polling,
    # display-queue publishing) so it doesn't add per-frame overhead at 250Hz.
    # Each multiprocessing.Queue call carries real OS-level sync cost on
    # Windows; polling everything every frame was the main throughput cap.
    loop_iteration = 0
    CONTROL_POLL_EVERY_N = 4   # ~62Hz control responsiveness at 250Hz input
    DISPLAY_PUBLISH_EVERY_N = 4  # ~62Hz display updates; GUI only needs ~60Hz

    # --- Diagnostics: per-stage timing, logged periodically to find where
    # the loop actually spends time (queue wait vs parse vs filter vs publish).
    DIAG_INTERVAL_S = 2.0
    diag_last_report = time.perf_counter()
    diag_samples = 0
    diag_imu_samples = 0
    diag_wait_s = 0.0
    diag_parse_s = 0.0
    diag_update_s = 0.0
    diag_publish_s = 0.0
    diag_iter_s = 0.0

    try:
        while not stop_event.is_set():
            loop_iteration += 1
            # Check for control commands (non-blocking), throttled to reduce IPC overhead
            cmd = None
            if loop_iteration % CONTROL_POLL_EVERY_N == 0:
                cmd = safe_queue_get(controlQueue, timeout=0.0, default=None)
            if sensor_calibration is not None and sensor_calibration.has_timed_out(
                time.perf_counter()
            ):
                failure_reason = (
                    f"No stable sensor bias estimate was collected within "
                    f"{GYRO_BIAS_CAL_TIMEOUT_S:.0f} seconds. "
                    f"{sensor_calibration.timeout_message()}"
                )
                _finish_sensor_bias_calibration(
                    filter, statusQueue, logQueue, failure_reason=failure_reason
                )
                sensor_calibration = None
            if cmd is not None:
                # Support control commands: 'reset' and ('set_center_threshold', value)
                # Accept both bare string commands and tuple/list variants
                if isinstance(cmd, (list, tuple)) and len(cmd) >= 1 and cmd[0] == 'set_orientation_hold':
                    if len(cmd) >= 2 and isinstance(cmd[1], bool):
                        filter.set_orientation_hold(cmd[1], now=filter.last_time)
                        log_info(
                            logQueue,
                            "Fusion Worker",
                            f"Orientation hold {'enabled' if cmd[1] else 'disabled'}",
                        )
                    else:
                        log_warning(
                            logQueue,
                            "Fusion Worker",
                            f"Invalid orientation hold command: {cmd}",
                        )
                elif cmd in ('recenter_orientation_to_current', 'reset_orientation') or (
                    isinstance(cmd, (list, tuple))
                    and len(cmd) >= 1
                    and cmd[0] in ('recenter_orientation_to_current', 'reset_orientation')
                ):
                    try:
                        drained_samples = 0
                        while drained_samples < 50:
                            line = safe_queue_get(serialQueue, timeout=0.0, default=None)
                            if line is None:
                                break
                            drained_samples += 1
                            try:
                                timestamp, accel, gyro = parse_imu_line(line)
                            except ValueError:
                                continue
                            filter.update(gyro, accel, timestamp)

                        current_pose = filter.recenter_to_current()
                        output_timing_reset = True
                        log_info(
                            logQueue,
                            "Fusion Worker",
                            "Output level set to current look-forward pose "
                            f"(yaw={current_pose[0]:.3f}, "
                            f"pitch={current_pose[1]:.3f}, "
                            f"roll={current_pose[2]:.3f})",
                        )
                    except Exception as e:
                        log_warning(
                            logQueue,
                            "Fusion Worker",
                            f"Error recentering output to current pose: {e}",
                        )
                elif cmd == 'calibrate_level' or (
                    isinstance(cmd, (list, tuple))
                    and cmd
                    and cmd[0] == 'calibrate_level'
                ):
                    log_warning(
                        logQueue,
                        "Fusion Worker",
                        "Ignoring obsolete calibrate_level command; recenter now sets the output level",
                    )
                elif cmd == 'reset' or (isinstance(cmd, (list, tuple)) and len(cmd) >= 1 and cmd[0] == 'reset'):
                    # Full reset: reset orientation and clear runtime calibration
                    if sensor_calibration is not None:
                        log_info(
                            logQueue,
                            "Fusion Worker",
                            "Cancelling active sensor bias calibration for full reset",
                        )
                        if not safe_queue_put(
                            statusQueue,
                            ('sensor_calibrating', False),
                            timeout=QUEUE_PUT_TIMEOUT,
                        ):
                            log_warning(
                                logQueue,
                                "Fusion Worker",
                                "Could not publish sensor calibration cancellation",
                            )
                        sensor_calibration = None
                    filter.reset()
                    # Clear timing and stationary debounce state so the filter
                    # reinitializes cleanly when new data arrives after a stop/start.
                    try:
                        filter.last_time = None
                        filter._stationary_start = None
                        filter._last_stationary = False
                        # Log timing baseline clear for debugging
                        try:
                            from src.util.log_utils import log_info
                            log_info(logQueue, "Fusion Worker", "Cleared timing baseline and stationary debounce state on reset")
                        except Exception:
                            pass
                        # Cleared timing baseline logged above; skip console print to reduce noise
                    except Exception:
                        pass
                    filter.reset_sensor_biases()
                    for status in (
                        ('sensor_calibration_source', None),
                        ('sensor_calibrated', False),
                        ('sensor_calibration_data', None),
                    ):
                        if not safe_queue_put(
                            statusQueue, status, timeout=QUEUE_PUT_TIMEOUT
                        ):
                            log_warning(
                                logQueue,
                                "Fusion Worker",
                                f"Could not publish sensor reset status: {status[0]}",
                            )

                    # Also explicitly clear drift and stationary UI indicators so the
                    # front-end does not continue to show stale 'active' state after stop.
                    try:
                        safe_queue_put(statusQueue, ('drift_correction', False), timeout=QUEUE_PUT_TIMEOUT)
                    except Exception:
                        pass
                    try:
                        safe_queue_put(statusQueue, ('stationary', False), timeout=QUEUE_PUT_TIMEOUT)
                    except Exception:
                        pass
                    log_info(logQueue, "Fusion Worker", "Orientation reset to zero and calibration cleared")
                    # Skip console print; message logged via log_info
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_center_threshold':
                    try:
                        new_val = float(cmd[1])
                        if 0.0 <= new_val <= 180.0:  # Sanity check
                            filter.center_threshold = new_val
                            # Update all thresholds for backward compatibility
                            filter.center_threshold_yaw = new_val
                            filter.center_threshold_pitch = new_val
                            filter.center_threshold_roll = new_val
                            log_info(logQueue, "Fusion Worker", f"Center threshold updated to {new_val}")
                            # Logged above; no console print
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid center threshold: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting center threshold: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 4 and cmd[0] == 'set_threshold':
                    # Set all three thresholds at once: ('set_threshold', yaw, pitch, roll)
                    try:
                        yaw_val = float(cmd[1])
                        pitch_val = float(cmd[2])
                        roll_val = float(cmd[3])
                        if 0.0 <= yaw_val <= 180.0 and 0.0 <= pitch_val <= 180.0 and 0.0 <= roll_val <= 180.0:
                            filter.center_threshold_yaw = yaw_val
                            filter.center_threshold_pitch = pitch_val
                            filter.center_threshold_roll = roll_val
                            log_info(logQueue, "Fusion Worker", f"Center thresholds updated to Yaw={yaw_val}, Pitch={pitch_val}, Roll={roll_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid center thresholds: Yaw={yaw_val}, Pitch={pitch_val}, Roll={roll_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting center thresholds: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_center_threshold_yaw':
                    try:
                        new_val = float(cmd[1])
                        if 0.0 <= new_val <= 180.0:  # Sanity check
                            filter.center_threshold_yaw = new_val
                            log_info(logQueue, "Fusion Worker", f"Yaw center threshold updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid yaw center threshold: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting yaw center threshold: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_center_threshold_pitch':
                    try:
                        new_val = float(cmd[1])
                        if 0.0 <= new_val <= 180.0:  # Sanity check
                            filter.center_threshold_pitch = new_val
                            log_info(logQueue, "Fusion Worker", f"Pitch center threshold updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid pitch center threshold: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting pitch center threshold: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_center_threshold_roll':
                    try:
                        new_val = float(cmd[1])
                        if 0.0 <= new_val <= 180.0:  # Sanity check
                            filter.center_threshold_roll = new_val
                            log_info(logQueue, "Fusion Worker", f"Roll center threshold updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid roll center threshold: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting roll center threshold: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] in ('set_accel_tau_pitch', 'set_accel_tau_roll'):
                    axis = 'pitch' if cmd[0] == 'set_accel_tau_pitch' else 'roll'
                    try:
                        new_val = float(cmd[1])
                        if new_val > 0.0:
                            setattr(filter, f'accel_tau_{axis}', new_val)
                            log_info(logQueue, "Fusion Worker", f"Accel {axis} correction time constant updated to {new_val} s")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid accel {axis} time constant: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting accel {axis} time constant: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_drift_smoothing_time':
                    try:
                        new_val = float(cmd[1])
                        if new_val > 0.0:  # Drift smoothing time must be positive
                            filter.drift_smoothing_time = new_val
                            log_info(logQueue, "Fusion Worker", f"Drift smoothing time updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid drift smoothing time: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting drift smoothing time: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_drift_curve_type':
                    try:
                        new_val = str(cmd[1]).lower()
                        valid_curves = ['exponential', 'linear', 'cosine', 'quadratic']
                        if new_val in valid_curves:
                            filter.drift_curve_type = new_val
                            log_info(logQueue, "Fusion Worker", f"Drift curve type updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid drift curve type: {new_val}. Valid options: {valid_curves}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting drift curve type: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_drift_correction_strength':
                    try:
                        new_val = float(cmd[1])
                        if 0.0 < new_val <= 1.0:  # Must be between 0 and 1
                            filter.drift_correction_strength = new_val
                            log_info(logQueue, "Fusion Worker", f"Drift correction strength updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid drift correction strength: {new_val}. Must be between 0.0 and 1.0")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting drift correction strength: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_invert_yaw':
                    try:
                        filter.invert_yaw = bool(cmd[1])
                        log_info(logQueue, "Fusion Worker", f"Yaw inversion set to {filter.invert_yaw}")
                            # Logged above; skip console print
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting yaw inversion: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_invert_pitch':
                    try:
                        filter.invert_pitch = bool(cmd[1])
                        log_info(logQueue, "Fusion Worker", f"Pitch inversion set to {filter.invert_pitch}")
                            # Logged above; skip console print
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting pitch inversion: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_invert_roll':
                    try:
                        filter.invert_roll = bool(cmd[1])
                        log_info(logQueue, "Fusion Worker", f"Roll inversion set to {filter.invert_roll}")
                            # Logged above; skip console print
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting roll inversion: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_yaw_bias_tracking':
                    if isinstance(cmd[1], bool):
                        filter.set_yaw_bias_tracking(cmd[1])
                        log_info(
                            logQueue,
                            "Fusion Worker",
                            f"Stationary yaw-bias tracking "
                            f"{'enabled' if cmd[1] else 'disabled'}",
                        )
                    else:
                        log_warning(
                            logQueue,
                            "Fusion Worker",
                            f"Invalid stationary yaw-bias tracking value: {cmd[1]!r}",
                        )
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_stationary_gyro_threshold':
                    try:
                        new_val = float(cmd[1])
                        if new_val >= 0.0 and new_val <= 1000.0:
                            filter._gyro_stationary_threshold = new_val
                            log_info(logQueue, "Fusion Worker", f"Stationary gyro threshold updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid stationary gyro threshold: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting stationary gyro threshold: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_stationary_debounce':
                    try:
                        new_val = float(cmd[1])
                        if new_val >= 0.0 and new_val <= 10.0:
                            filter._stationary_debounce_s = new_val
                            log_info(logQueue, "Fusion Worker", f"Stationary debounce updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid stationary debounce: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting stationary debounce: {e}")
                elif (
                    isinstance(cmd, (list, tuple))
                    and len(cmd) == 3
                    and cmd[0] == 'set_sensor_biases'
                ):
                    try:
                        filter.restore_sensor_biases(cmd[1], cmd[2])
                        calibration = {
                            "gyro": (
                                filter.gyro_bias_roll,
                                filter.gyro_bias_pitch,
                                filter.gyro_bias_yaw,
                            ),
                            "accel": (
                                filter.accel_bias_x,
                                filter.accel_bias_y,
                                filter.accel_bias_z,
                            ),
                        }
                        for status in (
                            ('sensor_calibration_source', 'stored'),
                            ('sensor_calibrated', True),
                            ('sensor_calibration_data', calibration),
                        ):
                            if not safe_queue_put(
                                statusQueue, status, timeout=QUEUE_PUT_TIMEOUT
                            ):
                                log_warning(
                                    logQueue,
                                    "Fusion Worker",
                                    "Could not notify the GUI about restored "
                                    f"sensor calibration: {status[0]}",
                                )
                        log_info(
                            logQueue,
                            "Fusion Worker",
                            "Restored persisted gyro and accelerometer calibration",
                        )
                    except (TypeError, ValueError, OverflowError) as error:
                        log_warning(
                            logQueue,
                            "Fusion Worker",
                            f"Ignoring invalid persisted sensor calibration: {error}",
                        )
                elif (
                    (isinstance(cmd, (list, tuple)) and len(cmd) >= 1
                     and cmd[0] in ('recalibrate_sensor_bias', 'recalibrate_gyro_bias'))
                    or cmd in (('recalibrate_sensor_bias',), ('recalibrate_gyro_bias',))
                ):
                    # The old command name remains accepted for compatibility.
                    try:
                        n_samples = None
                        if isinstance(cmd, (list, tuple)) and len(cmd) >= 2:
                            try:
                                n_samples = int(cmd[1])
                            except Exception:
                                n_samples = None

                        if n_samples is None:
                            n_samples = GYRO_BIAS_CAL_SAMPLES

                        if not n_samples or n_samples <= 0:
                            log_warning(logQueue, "Fusion Worker", f"Sensor calibration requested with non-positive sample count: {n_samples}")
                        elif sensor_calibration is not None:
                            log_warning(
                                logQueue,
                                "Fusion Worker",
                                "Ignoring sensor calibration request because calibration is already active",
                            )
                        else:
                            sensor_calibration = SensorBiasCalibration(
                                n_samples, time.perf_counter()
                            )
                            log_info(
                                logQueue,
                                "Fusion Worker",
                                f"Collecting {n_samples} stable stationary samples for "
                                "gyro and known-level accelerometer bias calibration",
                            )
                            if not safe_queue_put(
                                statusQueue,
                                ('sensor_calibrating', True),
                                timeout=QUEUE_PUT_TIMEOUT,
                            ):
                                log_warning(
                                    logQueue,
                                    "Fusion Worker",
                                    "Could not publish sensor calibration start status",
                                )

                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error during runtime sensor bias calibration: {e}")
            
            # Get data from serial queue - wait for the next sample rather than
            # busy-polling with non-blocking gets + a separate sleep; this lets
            # the OS wait primitive do the waiting instead of spinning the loop.
            # A blocking get() returns as soon as data arrives (not after the
            # full timeout), so a longer timeout costs nothing when data is
            # flowing - it only matters as a bound on stop_event responsiveness.
            # A too-tight timeout here previously caused frequent unproductive
            # timeouts on ordinary serial arrival jitter, each wasting a full
            # timeout window before retrying and measurably capping throughput.
            _diag_t0 = time.perf_counter()
            line = safe_queue_get(serialQueue, timeout=0.05, default=None)
            lines = []
            if line is not None:
                # Drain the ENTIRE backlog, but integrate every sample rather
                # than only the latest: skipping samples makes gyro integration
                # hold one reading across the gap (amplifying noise/vibration)
                # and makes accel correction strength depend on CPU load.
                # Only the final pose of the batch is published, so a starved
                # process still catches up to real time in one iteration.
                # serialQueue's maxsize bounds this loop already.
                lines.append(line)
                while True:
                    newer = safe_queue_get(serialQueue, timeout=0.0, default=None)
                    if newer is None:
                        break
                    lines.append(newer)
            _diag_t1 = time.perf_counter()
            diag_wait_s += (_diag_t1 - _diag_t0)
            
            if not lines:
                # No data available - check if we should send inactive status
                current_time = time.time()
                
                # If we were previously active and haven't seen data for timeout period
                if (processing_active and last_data_time is not None and 
                    (current_time - last_data_time) > data_timeout):
                    try:
                        if uiStatusQueue:
                            safe_queue_put(uiStatusQueue, ('processing', 'inactive'), timeout=QUEUE_PUT_TIMEOUT)
                        processing_active = False
                        
                        # Also clear drift correction and stationary status when going inactive
                        try:
                            statusQueue.put_nowait(('drift_correction', False))
                            last_drift_active = False
                        except:
                            pass
                        try:
                            statusQueue.put_nowait(('stationary', False))
                            last_stationary = False
                        except:
                            pass
                            
                        log_info(logQueue, "Fusion Worker", "Processing inactive - no data received")
                        # Processing inactive state logged above; skip console print
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Failed to send UI inactive status: {e}")
                
                # Brief non-blocking delay to prevent CPU spinning
                time.sleep(0.001)  # 1ms delay instead of blocking timeout
                continue
            
            try:
                # Parse, validate and integrate every queued IMU sample.
                result = None
                output_samples = []
                for sample_line in lines:
                    _diag_t2 = time.perf_counter()
                    try:
                        timestamp, accel, gyro = parse_imu_line(sample_line)
                    except ValueError as e:
                        if sensor_calibration is not None:
                            sensor_calibration.note_malformed_sample()
                        malformed_counter += 1
                        if malformed_counter % 100 == 0:
                            log_warning(logQueue, "Fusion Worker", f"Skipping malformed IMU lines: {malformed_counter} total; last error: {e}")
                        continue
                    _diag_t3 = time.perf_counter()
                    diag_parse_s += (_diag_t3 - _diag_t2)
                    previous_time = filter.last_time
                    result = filter.update(gyro, accel, timestamp)
                    diag_update_s += (time.perf_counter() - _diag_t3)
                    diag_imu_samples += 1
                    if sensor_calibration is not None:
                        calibration_samples = sensor_calibration.add_sample(
                            timestamp, accel, gyro
                        )
                        if calibration_samples is not None:
                            _finish_sensor_bias_calibration(
                                filter,
                                statusQueue,
                                logQueue,
                                samples=calibration_samples,
                            )
                            sensor_calibration = None
                    if previous_time is not None and not (
                        DT_MIN <= timestamp - previous_time <= DT_MAX
                    ):
                        if timestamp < previous_time or timestamp - previous_time > DT_MAX:
                            output_timing_reset = True
                        continue
                    yaw, pitch, roll = result[:3]
                    live_euler_data = (
                        -yaw if filter.invert_yaw else yaw,
                        -pitch if filter.invert_pitch else pitch,
                        -roll if filter.invert_roll else roll,
                    )
                    if not all(math.isfinite(value) for value in live_euler_data):
                        log_error(logQueue, "Fusion Worker", "Dropping non-finite Euler sample")
                        continue
                    output_angles = filter.apply_orientation_hold(
                        *live_euler_data, now=timestamp
                    )
                    yaw_rate, pitch_rate, roll_rate = filter.euler_rates
                    output_rates = (0.0, 0.0, 0.0) if filter.orientation_held else (
                        -yaw_rate if filter.invert_yaw else yaw_rate,
                        -pitch_rate if filter.invert_pitch else pitch_rate,
                        -roll_rate if filter.invert_roll else roll_rate,
                    )
                    output_samples.append(TrackingSample(
                        timestamp, output_angles, output_rates, output_timing_reset
                    ))
                    output_timing_reset = False
                if result is None or not output_samples:
                    continue

                # Update data timestamp
                last_data_time = time.time()

                yaw, pitch, roll, drift_active, is_stationary = result

                # Send processing status to UI only when transitioning from inactive to active
                if not processing_active:
                    try:
                        if uiStatusQueue:
                            safe_queue_put(uiStatusQueue, ('processing', 'active'), timeout=QUEUE_PUT_TIMEOUT)
                        processing_active = True
                        log_info(logQueue, "Fusion Worker", "Processing active - data received")
                        # Processing active state logged above; skip console print
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Failed to send UI processing status: {e}")
                        pass
                
                # Send drift correction status to UI only when it changes (non-blocking)
                if drift_active != last_drift_active:
                    try:
                        statusQueue.put_nowait(('drift_correction', drift_active))
                        last_drift_active = drift_active
                    except:
                        pass
                        
                # Send stationarity status to UI only when it changes (non-blocking)
                if is_stationary != last_stationary:
                    try:
                        statusQueue.put_nowait(('stationary', is_stationary))
                        last_stationary = is_stationary
                    except:
                        pass
                

                output_yaw, output_pitch, output_roll = output_samples[-1].angles

                # Validate output is finite before publishing to either output.
                _diag_t5 = time.perf_counter()
                if not (
                    all(math.isfinite(value) for value in live_euler_data)
                    and math.isfinite(output_yaw)
                    and math.isfinite(output_pitch)
                    and math.isfinite(output_roll)
                ):
                    nonfinite_euler_counter += 1
                    if nonfinite_euler_counter % 50 == 0:
                        log_error(logQueue, "Fusion Worker", f"Dropping non-finite Euler output #{nonfinite_euler_counter}: yaw={output_yaw}, pitch={output_pitch}, roll={output_roll}")
                    # Skip publishing this frame
                else:
                    # Publish only to the selected output queue; multiprocessing
                    # queues distribute samples instead of broadcasting them.
                    if not _publish_output_pose(outputQueue, tuple(output_samples)):
                        output_timing_reset = True
                        log_warning(
                            logQueue, "Fusion Worker",
                            f"Output queue full; dropped {len(output_samples)} sensor-time samples",
                        )

                    # Send to display queue with queue health monitoring, throttled
                    # since the GUI only redraws at ~60Hz - no need to pay the IPC
                    # cost of qsize()+put() on every 250Hz fusion iteration.
                    if eulerDisplayQueue is not None and loop_iteration % DISPLAY_PUBLISH_EVERY_N == 0:
                        try:
                            # Check queue health and log if getting full
                            queue_size = eulerDisplayQueue.qsize()
                            max_size = getattr(eulerDisplayQueue, '_maxsize', 60)
                            
                            # Only apply sampling if queue is very full (>90%)
                            if max_size > 0 and queue_size / max_size > 0.9:
                                # Queue critically full - skip some frames and log warning
                                filter._frame_counter = getattr(filter, '_frame_counter', 0) + 1
                                if filter._frame_counter % 2 == 0:  # Send every 2nd frame
                                    eulerDisplayQueue.put_nowait(live_euler_data)
                                # Log critical queue state occasionally
                                if filter._frame_counter % 100 == 0:
                                    log_warning(logQueue, "Fusion", f"Display queue critical: {queue_size}/{max_size} ({queue_size/max_size:.1%})")
                            else:
                                # Queue not full - send all frames
                                eulerDisplayQueue.put_nowait(live_euler_data)
                                # Log warning if queue getting full
                                if max_size > 0 and queue_size / max_size > 0.7:
                                    filter._warning_counter = getattr(filter, '_warning_counter', 0) + 1
                                    if filter._warning_counter % 200 == 0:  # Log every 200 frames when >70%
                                        log_info(logQueue, "Fusion", f"Display queue warning: {queue_size}/{max_size} ({queue_size/max_size:.1%})")
                        except Exception as e:
                            # Track queue errors 
                            filter._error_counter = getattr(filter, '_error_counter', 0) + 1
                            if filter._error_counter % 50 == 0:  # Log every 50 errors
                                log_error(logQueue, "Fusion", f"Display queue error #{filter._error_counter}: {e}")
                            pass
                
                _diag_t6 = time.perf_counter()
                diag_publish_s += (_diag_t6 - _diag_t5)
                diag_iter_s += (_diag_t6 - _diag_t0)
                diag_samples += 1

                if _diag_t6 - diag_last_report >= DIAG_INTERVAL_S and diag_samples > 0:
                    n = diag_samples
                    m = max(diag_imu_samples, 1)
                    log_info(
                        logQueue, "Fusion Timing",
                        f"{diag_imu_samples / (_diag_t6 - diag_last_report):.1f} IMU samples/s in "
                        f"{n / (_diag_t6 - diag_last_report):.1f} batches/s - "
                        f"avg ms per batch: wait={1000*diag_wait_s/n:.3f} "
                        f"publish={1000*diag_publish_s/n:.3f} iter_total={1000*diag_iter_s/n:.3f}; "
                        f"per sample: parse={1000*diag_parse_s/m:.3f} update={1000*diag_update_s/m:.3f}"
                    )
                    diag_last_report = _diag_t6
                    diag_samples = 0
                    diag_imu_samples = 0
                    diag_wait_s = diag_parse_s = diag_update_s = diag_publish_s = diag_iter_s = 0.0
                
            except ValueError as e:
                # Skip malformed/invalid lines (parse_imu_line raises ValueError)
                malformed_counter += 1
                if malformed_counter % 100 == 0:
                    log_warning(logQueue, "Fusion Worker", f"Skipping malformed IMU lines: {malformed_counter} total; last error: {e}")
                continue
            except Exception as e:
                log_error(logQueue, "Fusion Worker", f"Unexpected error processing data: {e}")
                continue
    
    except KeyboardInterrupt:
        pass
    finally:
        # Send processing inactive status when stopping
        try:
            if uiStatusQueue:
                safe_queue_put(uiStatusQueue, ('processing', 'inactive'), timeout=QUEUE_PUT_TIMEOUT)
            processing_active = False
            
            # Also clear drift correction and stationary status when stopping
            try:
                safe_queue_put(statusQueue, ('drift_correction', False), timeout=QUEUE_PUT_TIMEOUT)
            except Exception:
                pass
            try:
                safe_queue_put(statusQueue, ('stationary', False), timeout=QUEUE_PUT_TIMEOUT)
            except Exception:
                pass
        except Exception:
            pass
        log_info(logQueue, "Fusion Worker", "Stopped")
        # Stopped logged via log_info; avoid console print
        disable_high_res_timer()
