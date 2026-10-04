"""
Sensor fusion worker using complementary filter for orientation estimation.
Reads IMU data from serialQueue and outputs Euler angles to eulerQueue.
"""
import time
from queue import Empty
import threading
import math

from src.config.config import (
     ACCEL_THRESHOLD,
     DEFAULT_CENTER_THRESHOLD,
     ALPHA_YAW,
     ALPHA_PITCH,
     ALPHA_ROLL,
     ALPHA_DRIFT_CORRECTION,
        GYRO_BIAS_CAL_SAMPLES,
    LEVEL_CAL_SAMPLES,
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


class QuaternionComplementaryFilter:
    """Complementary filter implemented with quaternion integration.

    This provides the same external API as `ComplementaryFilter` but uses
    quaternions for orientation integration and accel-based roll/pitch
    correction.
    """

    def __init__(self, accel_threshold=ACCEL_THRESHOLD, center_threshold=DEFAULT_CENTER_THRESHOLD, center_threshold_yaw=None, center_threshold_pitch=None, center_threshold_roll=None, logQueue=None):
        self.alpha_roll = ALPHA_ROLL
        self.alpha_pitch = ALPHA_PITCH
        self.alpha_yaw = ALPHA_YAW
        self.alpha_drift = ALPHA_DRIFT_CORRECTION
        self.drift_smoothing_time = DRIFT_SMOOTHING_TIME
        self.drift_curve_type = DRIFT_TRANSITION_CURVE  # Load from config
        self.drift_correction_strength = 0.3  # Max correction strength per frame (configurable)
        self.accel_threshold = accel_threshold
        
        # Use separate thresholds if provided, otherwise use default for all
        self.center_threshold_yaw = center_threshold_yaw if center_threshold_yaw is not None else center_threshold
        self.center_threshold_pitch = center_threshold_pitch if center_threshold_pitch is not None else center_threshold
        self.center_threshold_roll = center_threshold_roll if center_threshold_roll is not None else center_threshold
        
        # Keep backward compatibility
        self.center_threshold = center_threshold
        self.gyro_bias_yaw = 0.0
        # Center offsets (degrees) applied to pitch/roll to correct non-level rest position
        self.center_offset_pitch = 0.0
        self.center_offset_roll = 0.0
        # Allow yaw offset placeholder for completeness
        self.center_offset_yaw = 0.0
        # mark if a center offset has been set
        self.center_calibrated = False
        self.gyro_calibrated = False
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

    def _euler_from_quat(self, q):
        # returns (yaw, pitch, roll) in degrees
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
        yaw = math.degrees(math.atan2(t3, t4))

        return yaw, pitch, roll

    def _quat_from_euler(self, yaw, pitch, roll):
        # input degrees
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
        """Calculate drift correction factor based on curve type.
        
        Args:
            dt: Time delta for this update
            elapsed_time: Total time since drift correction started
            
        Returns:
            float: Correction factor between 0.0 and 1.0 for this frame
        """
        # Calculate progress through the smoothing period
        time_progress = min(elapsed_time / self.drift_smoothing_time, 1.0)
        
        if self.drift_curve_type == 'exponential':
            # Exponential approach - fast start, slow finish
            # Use cumulative approach for consistency
            target_progress = 1.0 - math.exp(-elapsed_time / self.drift_smoothing_time)
            if elapsed_time > dt:
                prev_progress = 1.0 - math.exp(-(elapsed_time - dt) / self.drift_smoothing_time)
                factor = min(target_progress - prev_progress, 0.4)
            else:
                factor = min(target_progress, 0.4)
        elif self.drift_curve_type == 'linear':
            # Linear progress - constant rate
            rate_per_second = 1.0 / self.drift_smoothing_time
            factor = min(rate_per_second * dt, 0.1)
        elif self.drift_curve_type == 'cosine':
            # Cosine ease-in-out - smooth start and finish
            target_progress = 0.5 * (1.0 - math.cos(math.pi * time_progress))
            if elapsed_time > dt:
                prev_time_progress = min((elapsed_time - dt) / self.drift_smoothing_time, 1.0)
                prev_progress = 0.5 * (1.0 - math.cos(math.pi * prev_time_progress))
                factor = min(target_progress - prev_progress, 0.4)
            else:
                factor = min(target_progress, 0.4)
        elif self.drift_curve_type == 'quadratic':
            # Quadratic ease-in - slow start, fast finish
            target_progress = time_progress * time_progress
            if elapsed_time > dt:
                prev_time_progress = min((elapsed_time - dt) / self.drift_smoothing_time, 1.0)
                prev_progress = prev_time_progress * prev_time_progress
                factor = min(target_progress - prev_progress, 0.4)
            else:
                factor = min(target_progress, 0.4)
        else:
            # Fallback to exponential
            target_progress = 1.0 - math.exp(-elapsed_time / self.drift_smoothing_time)
            if elapsed_time > dt:
                prev_progress = 1.0 - math.exp(-(elapsed_time - dt) / self.drift_smoothing_time)
                factor = min(target_progress - prev_progress, 0.4)
            else:
                factor = min(target_progress, 0.4)
        
        # Removed angle_magnitude scaling - we want consistent correction rate
        # regardless of how close to zero we are. Per-axis correction handles
        # small angles properly without needing to slow down.
        
        return factor

    def _accel_to_rp(self, accel):
        ax, ay, az = accel
        roll = math.atan2(ay, az) * 180.0 / math.pi
        pitch = math.atan2(-ax, math.sqrt(ay*ay + az*az)) * 180.0 / math.pi
        return roll, pitch

    def update(self, gyro, accel, timestamp):
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
            return 0.0, 0.0, 0.0, False, False

        dt = timestamp - self.last_time
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
            y, p, r = self._euler_from_quat(self.q)
            return y, p, r, False, False

        self.last_time = timestamp

        gx, gy, gz = gyro
        # apply bias to gz
        gz_corr = gz - self.gyro_bias_yaw

        # Integrate quaternion using gyro (deg/s -> rad/s).
        # gz is negated because the IMU's yaw axis points opposite the
        # application's yaw convention in the shipped sensor mounting.
        omega = (0.0, math.radians(gx), math.radians(gy), math.radians(-gz_corr))
        q = self.q
        qd = self._quat_mul(q, omega)
        q = (
            q[0] + 0.5 * qd[0] * dt,
            q[1] + 0.5 * qd[1] * dt,
            q[2] + 0.5 * qd[2] * dt,
            q[3] + 0.5 * qd[3] * dt,
        )
        q = self._quat_normalize(q)

        # accel-based roll/pitch correction (if accel valid)
        ax, ay, az = accel
        accel_mag = math.sqrt(ax*ax + ay*ay + az*az)
        gyro_mag = math.sqrt(gx*gx + gy*gy + gz*gz)

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

        # Helper function for angle differences
        def _angle_diff_fast(a, b):
            """Optimized smallest angle difference using modulo (O(1) vs O(n))."""
            diff = (a - b + 180.0) % 360.0 - 180.0
            return abs(diff)
        
        # Apply normal accelerometer blending first (when not in drift correction)
        if accel_ok:
            accel_roll, accel_pitch = self._accel_to_rp((ax, ay, az))
            
            # Get current orientation from gyro-integrated quaternion
            current_yaw, gyro_pitch, gyro_roll = self._euler_from_quat(q)
            
            # Blend roll and pitch separately using their respective alpha values
            # Higher alpha = more gyro, lower alpha = more accel
            blended_roll = self.alpha_roll * gyro_roll + (1.0 - self.alpha_roll) * accel_roll
            blended_pitch = self.alpha_pitch * gyro_pitch + (1.0 - self.alpha_pitch) * accel_pitch
            
            # Yaw stays from gyro integration (no magnetometer available)
            # Convert back to quaternion with blended roll/pitch and gyro yaw
            q = self._quat_from_euler(current_yaw, blended_pitch, blended_roll)
            # q was just built from these exact angles; reuse them instead of
            # re-decomposing q with another round of trig calls.
            yaw_est, pitch_est, roll_est = current_yaw, blended_pitch, blended_roll
        else:
            # q wasn't reconstructed from known angles here, so it must be decomposed.
            yaw_est, pitch_est, roll_est = self._euler_from_quat(q)

        # Check if we're looking approximately straight ahead AFTER accel correction
        # Compare against configured center offsets so drift correction can operate
        # when a user-defined rest offset exists.
        is_near_center = (_angle_diff_fast(yaw_est, self.center_offset_yaw) < self.center_threshold_yaw and 
                 _angle_diff_fast(pitch_est, self.center_offset_pitch) < self.center_threshold_pitch and
                 _angle_diff_fast(roll_est, self.center_offset_roll) < self.center_threshold_roll)
        
        # Apply drift correction when stationary and near center
        drift_active = False
        if is_stationary and is_near_center:
            # Track drift correction start time
            if self._drift_correction_start is None:
                self._drift_correction_start = timestamp
            
            elapsed_time = timestamp - self._drift_correction_start
            
            # Calculate per-frame correction strength based on curve type and elapsed time
            progress = min(elapsed_time / self.drift_smoothing_time, 1.0)
            
            if self.drift_curve_type == 'exponential':
                # Exponential: stronger correction as time progresses
                # Use derivative to get per-frame strength
                base_strength = (1.0 - math.exp(-3.0 * progress)) / max(progress, 0.01)
                base_rate = base_strength * dt / self.drift_smoothing_time
                correction_strength = min(base_rate * (self.drift_correction_strength / 0.3), 1.0)
            elif self.drift_curve_type == 'linear':
                # Linear: constant correction rate
                base_rate = dt / self.drift_smoothing_time
                correction_strength = min(base_rate * (self.drift_correction_strength / 0.3), 1.0)
            elif self.drift_curve_type == 'cosine':
                # Cosine ease: smooth variable rate
                rate = 0.5 * math.pi * math.sin(math.pi * progress) / self.drift_smoothing_time
                base_rate = rate * dt
                correction_strength = min(base_rate * (self.drift_correction_strength / 0.3), 1.0)
            elif self.drift_curve_type == 'quadratic':
                # Quadratic: accelerating correction
                rate = 2.0 * progress / self.drift_smoothing_time
                base_rate = rate * dt
                correction_strength = min(base_rate * (self.drift_correction_strength / 0.3), 1.0)
            else:
                # Fallback to linear
                base_rate = dt / self.drift_smoothing_time
                correction_strength = min(base_rate * (self.drift_correction_strength / 0.3), 1.0)
            
            # Extract current absolute angles (already known from above; q was
            # either just built from these or decomposed once already).
            current_yaw, current_pitch, current_roll = yaw_est, pitch_est, roll_est

            # Work in the *relative* frame defined by center offsets so drift
            # correction brings the sensor toward the user-defined rest pose.
            rel_yaw = normalize_angle(current_yaw - self.center_offset_yaw)
            rel_pitch = normalize_angle(current_pitch - self.center_offset_pitch)
            rel_roll = normalize_angle(current_roll - self.center_offset_roll)

            # Apply gentle per-frame correction toward zero in relative frame
            corrected_rel_yaw = rel_yaw * (1.0 - correction_strength)
            corrected_rel_pitch = rel_pitch * (1.0 - correction_strength)
            corrected_rel_roll = rel_roll * (1.0 - correction_strength)

            # Convert back to absolute angles by re-applying center offsets
            corrected_yaw = normalize_angle(corrected_rel_yaw + self.center_offset_yaw)
            corrected_pitch = normalize_angle(corrected_rel_pitch + self.center_offset_pitch)
            corrected_roll = normalize_angle(corrected_rel_roll + self.center_offset_roll)

            # Convert corrected Euler angles back to quaternion
            q = self._quat_from_euler(corrected_yaw, corrected_pitch, corrected_roll)
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
        # Store angles for next frame's smoothness calculation
        self._last_yaw = yaw
        self._last_pitch = pitch
        self._last_roll = roll
        # normalize angles
        yaw = normalize_angle(yaw)
        pitch = normalize_angle(pitch)
        roll = normalize_angle(roll)

        # Apply user/mount center offsets (subtract stored rest offsets)
        try:
            yaw = normalize_angle(yaw - self.center_offset_yaw)
            pitch = normalize_angle(pitch - self.center_offset_pitch)
            roll = normalize_angle(roll - self.center_offset_roll)
        except Exception:
            # In case offsets are not numeric for some reason, ignore
            pass

        return yaw, pitch, roll, drift_active, is_stationary

    def reset(self):
        """Reset quaternion orientation to identity (zero rotation)."""
        self.q = (1.0, 0.0, 0.0, 0.0)
        self._drift_correction_start = None
    
    


def run_worker(serialQueue, eulerQueue, eulerDisplayQueue, controlQueue, statusQueue, stop_event, logQueue=None, uiStatusQueue=None):
    """
    Fusion worker that reads IMU data from serialQueue and outputs Euler angles to eulerQueue.
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
    # Startup calibration disabled: calibration must be triggered manually
    # by the GUI (recalibrate_gyro_bias). Inform GUI of current calibration
    # state (not calibrated) so the UI can reflect it.
    try:
        filter.gyro_calibrated = False
        safe_queue_put(statusQueue, ('gyro_calibrated', False), timeout=QUEUE_PUT_TIMEOUT)
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

    # Rate-limited counters for malformed/non-finite inputs to avoid log spam
    malformed_counter = 0
    nonfinite_euler_counter = 0

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
            if cmd is not None:
                # Support control commands: 'reset' and ('set_center_threshold', value)
                # Accept both bare string commands and tuple/list variants
                if cmd == 'reset_orientation' or (isinstance(cmd, (list, tuple)) and len(cmd) >= 1 and cmd[0] == 'reset_orientation'):
                    # Reset orientation state but preserve calibration/bias.
                    # Minimal fix: seed filter quaternion from latest accel sample so
                    # we don't emit a forced one-frame zero that causes a visible pitch jump.
                    try:
                        seeded = False
                        latest_line = None
                        # Drain a few IMU lines non-blocking to find the freshest sample
                        try:
                            sc = 0
                            while sc < 50:
                                l = safe_queue_get(serialQueue, timeout=0.0, default=None)
                                if l is None:
                                    break
                                latest_line = l
                                sc += 1
                        except Exception:
                            latest_line = None

                        if latest_line is not None:
                            try:
                                ts, accel, gyro = parse_imu_line(latest_line)
                                ax, ay, az = accel
                                # Compute accel-derived roll/pitch
                                accel_roll, accel_pitch = filter._accel_to_rp((ax, ay, az))
                                # Use current center_offset_yaw as yaw baseline (keeps yaw behavior consistent)
                                yaw0 = getattr(filter, 'center_offset_yaw', 0.0)
                                q = filter._quat_from_euler(yaw0, accel_pitch, accel_roll)
                                filter.q = filter._quat_normalize(q)
                                filter.last_time = ts
                                seeded = True
                                try:
                                    log_info(logQueue, "Fusion Worker", f"Orientation reset seeded from accel sample at {ts}: pitch={accel_pitch:.3f} roll={accel_roll:.3f}")
                                except Exception:
                                    pass
                            except Exception:
                                seeded = False

                        if not seeded:
                            # Fallback to original behavior if no IMU sample available
                            try:
                                filter.reset()
                                # Clear timing baseline so the next update returns a zeroed orientation
                                filter.last_time = None
                            except Exception:
                                try:
                                    filter.reset()
                                except Exception:
                                    pass

                    except Exception:
                        try:
                            filter.reset()
                        except Exception:
                            pass
                    log_info(logQueue, "Fusion Worker", "Orientation reset to seeded accel baseline (preserving calibration)")
                    # Schedule a center-level recalibration to run after reset when the sensor is stationary
                    try:
                        filter._pending_center_cal = LEVEL_CAL_SAMPLES
                        log_info(logQueue, "Fusion Worker", f"Scheduled center recalibration for {LEVEL_CAL_SAMPLES} samples (after recenter)")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Failed to schedule center recalibration: {e}")
                elif cmd == 'reset' or (isinstance(cmd, (list, tuple)) and len(cmd) >= 1 and cmd[0] == 'reset'):
                    # Full reset: reset orientation and clear runtime calibration
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
                    try:
                        filter.gyro_bias_yaw = 0.0
                        filter.gyro_calibrated = False
                        safe_queue_put(statusQueue, ('gyro_calibrated', False), timeout=QUEUE_PUT_TIMEOUT)
                    except Exception:
                        pass

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
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_alpha_yaw':
                    try:
                        new_val = float(cmd[1])
                        if 0.0 <= new_val <= 1.0:  # Alpha values must be between 0 and 1
                            filter.alpha_yaw = new_val
                            log_info(logQueue, "Fusion Worker", f"Alpha yaw updated to {new_val}")
                            # Alpha yaw update logged; skip console print
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid alpha yaw: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting alpha yaw: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_alpha_pitch':
                    try:
                        new_val = float(cmd[1])
                        if 0.0 <= new_val <= 1.0:  # Alpha values must be between 0 and 1
                            filter.alpha_pitch = new_val
                            log_info(logQueue, "Fusion Worker", f"Alpha pitch updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid alpha pitch: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting alpha pitch: {e}")
                elif isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 'set_alpha_roll':
                    try:
                        new_val = float(cmd[1])
                        if 0.0 <= new_val <= 1.0:  # Alpha values must be between 0 and 1
                            filter.alpha_roll = new_val
                            log_info(logQueue, "Fusion Worker", f"Alpha roll updated to {new_val}")
                        else:
                            log_warning(logQueue, "Fusion Worker", f"Invalid alpha roll: {new_val}")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error setting alpha roll: {e}")
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
                elif (isinstance(cmd, (list, tuple)) and len(cmd) >= 1 and cmd[0] == 'recalibrate_gyro_bias') or cmd == ('recalibrate_gyro_bias',):
                    # Runtime recalibration request. Optional second element: number of samples
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
                            log_warning(logQueue, "Fusion Worker", f"Recalibration requested with non-positive sample count: {n_samples}")
                        else:
                            log_info(logQueue, "Fusion Worker", f"Recalibrating gyro yaw bias with {n_samples} samples")
                            # Runtime recalibration started; skip console print
                            
                            # Notify GUI that calibration is starting
                            try:
                                safe_queue_put(statusQueue, ('gyro_calibrating', True), timeout=QUEUE_PUT_TIMEOUT)
                            except Exception:
                                pass
                            
                            samples = []
                            last_ts = None
                            while len(samples) < n_samples and not stop_event.is_set():
                                line = safe_queue_get(serialQueue, timeout=QUEUE_GET_TIMEOUT, default=None)
                                if line is None:
                                    continue
                                try:
                                    ts, accel, gyro = parse_imu_line(line)
                                    # Only accept stationary samples: accel near 1g and gyro quiet
                                    ax, ay, az = accel
                                    mag = math.sqrt(ax * ax + ay * ay + az * az)
                                    gyro_mag = math.sqrt(gyro[0] * gyro[0] + gyro[1] * gyro[1] + gyro[2] * gyro[2])
                                    if mag >= 0.01 and abs(mag - 1.0) < ACCEL_THRESHOLD and gyro_mag < STATIONARY_GYRO_THRESHOLD:
                                        samples.append(float(gyro[2]))
                                        last_ts = ts
                                except ValueError:
                                    continue

                            if len(samples) > 0:
                                bias = sum(samples) / float(len(samples))
                                filter.gyro_bias_yaw = bias
                                if last_ts is not None:
                                    filter.last_time = last_ts
                                # Mark filter as calibrated and notify GUI
                                filter.gyro_calibrated = True
                                try:
                                    safe_queue_put(statusQueue, ('gyro_calibrating', False), timeout=QUEUE_PUT_TIMEOUT)
                                    safe_queue_put(statusQueue, ('gyro_calibrated', True), timeout=QUEUE_PUT_TIMEOUT)
                                except Exception:
                                    pass
                                log_info(logQueue, "Fusion Worker", f"Runtime gyro yaw bias recalibrated from {len(samples)} samples: {bias:.6f} deg/s")
                            else:
                                filter.gyro_calibrated = False
                                try:
                                    safe_queue_put(statusQueue, ('gyro_calibrating', False), timeout=QUEUE_PUT_TIMEOUT)
                                    safe_queue_put(statusQueue, ('gyro_calibrated', False), timeout=QUEUE_PUT_TIMEOUT)
                                except Exception:
                                    pass
                                log_warning(logQueue, "Fusion Worker", "Runtime gyro yaw bias recalibration collected 0 samples")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error during runtime gyro bias recalibration: {e}")
                elif (isinstance(cmd, (list, tuple)) and len(cmd) >= 1 and cmd[0] == 'calibrate_level') or cmd == ('calibrate_level',):
                    # Runtime center-level recalibration request. Optional second element: number of samples
                    try:
                        n_samples = None
                        if isinstance(cmd, (list, tuple)) and len(cmd) >= 2:
                            try:
                                n_samples = int(cmd[1])
                            except Exception:
                                n_samples = None

                        if n_samples is None:
                            n_samples = LEVEL_CAL_SAMPLES

                        if not n_samples or n_samples <= 0:
                            log_warning(logQueue, "Fusion Worker", f"Level recalibration requested with non-positive sample count: {n_samples}")
                        else:
                            log_info(logQueue, "Fusion Worker", f"Recalibrating center level with {n_samples} samples")
                            # Runtime center recalibration started; skip console print
                            # Notify GUI that calibration is starting
                            try:
                                safe_queue_put(statusQueue, ('center_calibrating', True), timeout=QUEUE_PUT_TIMEOUT)
                            except Exception:
                                pass

                            roll_samples = []
                            pitch_samples = []
                            last_ts = None
                            while len(roll_samples) < n_samples and not stop_event.is_set():
                                line = safe_queue_get(serialQueue, timeout=QUEUE_GET_TIMEOUT, default=None)
                                if line is None:
                                    continue
                                try:
                                    ts, accel, gyro = parse_imu_line(line)
                                    # Only accept stationary samples: accel near 1g and gyro quiet
                                    ax, ay, az = accel
                                    mag = math.sqrt(ax * ax + ay * ay + az * az)
                                    gyro_mag = math.sqrt(gyro[0] * gyro[0] + gyro[1] * gyro[1] + gyro[2] * gyro[2])
                                    if mag >= 0.01 and abs(mag - 1.0) < ACCEL_THRESHOLD and gyro_mag < STATIONARY_GYRO_THRESHOLD:
                                        r, p = filter._accel_to_rp((ax, ay, az))
                                        roll_samples.append(float(r))
                                        pitch_samples.append(float(p))
                                        last_ts = ts
                                except ValueError:
                                    continue

                            if len(roll_samples) > 0:
                                avg_roll = sum(roll_samples) / float(len(roll_samples))
                                avg_pitch = sum(pitch_samples) / float(len(pitch_samples))
                                filter.center_offset_roll = avg_roll
                                filter.center_offset_pitch = avg_pitch
                                if last_ts is not None:
                                    filter.last_time = last_ts
                                # Mark filter as calibrated and notify GUI
                                filter.center_calibrated = True
                                try:
                                    safe_queue_put(statusQueue, ('center_calibrating', False), timeout=QUEUE_PUT_TIMEOUT)
                                    safe_queue_put(statusQueue, ('center_calibrated', True), timeout=QUEUE_PUT_TIMEOUT)
                                except Exception:
                                    pass
                                log_info(logQueue, "Fusion Worker", f"Runtime center level recalibrated from {len(roll_samples)} samples: roll={avg_roll:.3f} pitch={avg_pitch:.3f}")
                            else:
                                filter.center_calibrated = False
                                try:
                                    safe_queue_put(statusQueue, ('center_calibrating', False), timeout=QUEUE_PUT_TIMEOUT)
                                    safe_queue_put(statusQueue, ('center_calibrated', False), timeout=QUEUE_PUT_TIMEOUT)
                                except Exception:
                                    pass
                                log_warning(logQueue, "Fusion Worker", "Runtime center level recalibration collected 0 samples")
                    except Exception as e:
                        log_warning(logQueue, "Fusion Worker", f"Error during runtime center level recalibration: {e}")
            
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
            if line is not None:
                # Drain the ENTIRE backlog to the true latest sample, not just a
                # few items. If this process was starved of CPU time (e.g. by a
                # demanding game) while data kept arriving, a small capped drain
                # would only claw back a handful of samples per iteration,
                # forcing many iterations to grind through a stale backlog
                # before catching up to real time - this is the "desync"/replay
                # lag under load. serialQueue's maxsize bounds this loop already.
                while True:
                    newer = safe_queue_get(serialQueue, timeout=0.0, default=None)
                    if newer is None:
                        break
                    line = newer
            _diag_t1 = time.perf_counter()
            diag_wait_s += (_diag_t1 - _diag_t0)
            
            if line is None:
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
                # Parse and validate IMU data using error_utils
                _diag_t2 = time.perf_counter()
                timestamp, accel, gyro = parse_imu_line(line)
                _diag_t3 = time.perf_counter()
                diag_parse_s += (_diag_t3 - _diag_t2)
                
                # Update data timestamp
                last_data_time = time.time()
                
                # Update filter
                yaw, pitch, roll, drift_active, is_stationary = filter.update(gyro, accel, timestamp)
                _diag_t4 = time.perf_counter()
                diag_update_s += (_diag_t4 - _diag_t3)

                # If a center recalibration was scheduled by a recent recenter, run it now
                if getattr(filter, '_pending_center_cal', None):
                    try:
                        n_samples = int(filter._pending_center_cal)
                    except Exception:
                        n_samples = None

                    # Only start calibration when we are stationary to avoid bad samples
                    if n_samples and is_stationary:
                        try:
                            safe_queue_put(statusQueue, ('center_calibrating', True), timeout=QUEUE_PUT_TIMEOUT)
                        except Exception:
                            pass

                        roll_samples = []
                        pitch_samples = []
                        last_ts = None
                        while len(roll_samples) < n_samples and not stop_event.is_set():
                            sline = safe_queue_get(serialQueue, timeout=QUEUE_GET_TIMEOUT, default=None)
                            if sline is None:
                                continue
                            try:
                                ts, a2, g2 = parse_imu_line(sline)
                                ax, ay, az = a2
                                mag = math.sqrt(ax * ax + ay * ay + az * az)
                                gyro_mag = math.sqrt(g2[0] * g2[0] + g2[1] * g2[1] + g2[2] * g2[2])
                                if mag >= 0.01 and abs(mag - 1.0) < ACCEL_THRESHOLD and gyro_mag < STATIONARY_GYRO_THRESHOLD:
                                    r, p = filter._accel_to_rp((ax, ay, az))
                                    roll_samples.append(float(r))
                                    pitch_samples.append(float(p))
                                    last_ts = ts
                            except ValueError:
                                continue

                        if len(roll_samples) > 0:
                            avg_roll = sum(roll_samples) / float(len(roll_samples))
                            avg_pitch = sum(pitch_samples) / float(len(pitch_samples))
                            filter.center_offset_roll = avg_roll
                            filter.center_offset_pitch = avg_pitch
                            if last_ts is not None:
                                filter.last_time = last_ts
                            filter.center_calibrated = True
                            try:
                                safe_queue_put(statusQueue, ('center_calibrating', False), timeout=QUEUE_PUT_TIMEOUT)
                                safe_queue_put(statusQueue, ('center_calibrated', True), timeout=QUEUE_PUT_TIMEOUT)
                            except Exception:
                                pass
                            log_info(logQueue, "Fusion Worker", f"Runtime center level recalibrated from {len(roll_samples)} samples (scheduled): roll={avg_roll:.3f} pitch={avg_pitch:.3f}")
                        else:
                            filter.center_calibrated = False
                            try:
                                safe_queue_put(statusQueue, ('center_calibrating', False), timeout=QUEUE_PUT_TIMEOUT)
                                safe_queue_put(statusQueue, ('center_calibrated', False), timeout=QUEUE_PUT_TIMEOUT)
                            except Exception:
                                pass
                            log_warning(logQueue, "Fusion Worker", "Scheduled center level recalibration collected 0 samples")

                        # Clear pending flag regardless of success so we don't retry
                        try:
                            delattr = setattr  # local alias to avoid lint
                            delattr(filter, '_pending_center_cal', None)
                        except Exception:
                            try:
                                if hasattr(filter, '_pending_center_cal'):
                                    del filter._pending_center_cal
                            except Exception:
                                pass
                
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
                

                # Apply axis inversions if configured
                output_yaw = -yaw if filter.invert_yaw else yaw
                output_pitch = -pitch if filter.invert_pitch else pitch
                output_roll = -roll if filter.invert_roll else roll
                
                # Put Euler angles into output queues
                # Format: [Yaw, Pitch, Roll]
                euler_data = [output_yaw, output_pitch, output_roll]

                # Validate output is finite before publishing to avoid contaminating UDP
                _diag_t5 = time.perf_counter()
                if not (math.isfinite(output_yaw) and math.isfinite(output_pitch) and math.isfinite(output_roll)):
                    nonfinite_euler_counter += 1
                    if nonfinite_euler_counter % 50 == 0:
                        log_error(logQueue, "Fusion Worker", f"Dropping non-finite Euler output #{nonfinite_euler_counter}: yaw={output_yaw}, pitch={output_pitch}, roll={output_roll}")
                    # Skip publishing this frame
                else:
                    # Publish to main euler queue (for UDP) - non-blocking for real-time
                    try:
                        eulerQueue.put_nowait(euler_data)
                    except:
                        # Drop frame if queue full rather than blocking
                        pass

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
                                    eulerDisplayQueue.put_nowait(euler_data)
                                # Log critical queue state occasionally
                                if filter._frame_counter % 100 == 0:
                                    log_warning(logQueue, "Fusion", f"Display queue critical: {queue_size}/{max_size} ({queue_size/max_size:.1%})")
                            else:
                                # Queue not full - send all frames
                                eulerDisplayQueue.put_nowait(euler_data)
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
                    log_info(
                        logQueue, "Fusion Timing",
                        f"{n / (_diag_t6 - diag_last_report):.1f} samples/s over {n} samples - "
                        f"avg ms: wait={1000*diag_wait_s/n:.3f} parse={1000*diag_parse_s/n:.3f} "
                        f"update={1000*diag_update_s/n:.3f} publish={1000*diag_publish_s/n:.3f} "
                        f"iter_total={1000*diag_iter_s/n:.3f}"
                    )
                    diag_last_report = _diag_t6
                    diag_samples = 0
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
