"""Regression tests for accelerometer tilt correction and per-sample fusion."""

import math
from queue import Queue
import threading
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication

from src.workers.fusion_wrk import QuaternionComplementaryFilter, run_worker
from src.workers.gui_qt.panels.preferences_panel import PreferencesPanel


def _level_filter(**attributes):
    filter_instance = QuaternionComplementaryFilter(center_threshold=0.0)
    filter_instance.last_time = 0.0
    for name, value in attributes.items():
        setattr(filter_instance, name, value)
    return filter_instance


def _run(filter_instance, seconds, rate_hz, gyro, accel_at):
    output = None
    for index in range(1, int(seconds * rate_hz) + 1):
        timestamp = index / rate_hz
        output = filter_instance.update(gyro, accel_at(timestamp), timestamp)
    return output


def _qmul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return (
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
    )


def _axis_quat(axis, degrees):
    half = math.radians(degrees) / 2
    return (math.cos(half),) + tuple(math.sin(half) * c for c in axis)


def _rotate(q, v, inverse=False):
    conj = (q[0], -q[1], -q[2], -q[3])
    if inverse:
        q, conj = conj, q
    return _qmul(_qmul(q, (0.0,) + tuple(v)), conj)[1:]


def _worn_yaw_sweep(filter_instance, yaw_at, tilt_pitch, tilt_roll, lever_m, seconds):
    """Feed physically consistent IMU data (right-handed MPU frame) for a head
    tilted by (pitch, roll) turning about the world vertical; return the worst
    pitch/roll error after the still lead-in."""
    head_tilt = _qmul(_axis_quat((0, 1, 0), tilt_pitch), _axis_quat((1, 0, 0), tilt_roll))
    worst = 0.0
    rate_hz = 250
    for index in range(int(seconds * rate_hz) + 1):
        t = index / rate_hz
        psi, rate, accel_rate = yaw_at(t)
        q = _qmul(_axis_quat((0, 0, 1), math.degrees(psi)), head_tilt)
        gyro = tuple(math.degrees(c) for c in _rotate(q, (0, 0, rate), inverse=True))
        rx, ry, _ = _rotate(q, lever_m)
        lever_accel = (
            (-accel_rate * ry - rate * rate * rx) / 9.81,
            (accel_rate * rx - rate * rate * ry) / 9.81,
            1.0,
        )
        accel = _rotate(q, lever_accel, inverse=True)
        _, pitch, roll, _, _ = filter_instance.update(gyro, accel, t)
        if t > 0.5:
            worst = max(worst, abs(pitch - tilt_pitch), abs(roll - tilt_roll))
    return worst


def _sine_yaw(peak_dps, freq_hz, start_s):
    omega = 2 * math.pi * freq_hz
    amplitude = math.radians(peak_dps) / omega

    def yaw_at(t):
        t -= start_s
        if t < 0:
            return 0.0, 0.0, 0.0
        return (
            amplitude * math.sin(omega * t),
            amplitude * omega * math.cos(omega * t),
            -amplitude * omega * omega * math.sin(omega * t),
        )
    return yaw_at


def _snap_yaw(angle_deg, duration_s, hold_s, start_s):
    """Alternating minimum-jerk head turns with still holds in between."""
    segment = duration_s + hold_s

    def yaw_at(t):
        t -= start_s
        if t < 0:
            return 0.0, 0.0, 0.0
        index = int(t // segment)
        start = 0.0 if index == 0 else (-angle_deg if index % 2 == 0 else angle_deg)
        target = angle_deg if index % 2 == 0 else -angle_deg
        u = (t - index * segment) / duration_s
        delta = math.radians(target - start)
        if u >= 1.0:
            return math.radians(target), 0.0, 0.0
        s = 10*u**3 - 15*u**4 + 6*u**5
        ds = (30*u**2 - 60*u**3 + 30*u**4) / duration_s
        dds = (60*u - 180*u**2 + 120*u**3) / duration_s**2
        return math.radians(start) + delta * s, delta * ds, delta * dds
    return yaw_at


class TiltCorrectionTests(unittest.TestCase):
    def test_translation_pulse_is_rejected_without_delayed_correction_tail(self):
        for rate_hz in (125, 250, 500):
            for axis in (0, 1):
                with self.subTest(rate_hz=rate_hz, axis=axis):
                    flt = _level_filter()
                    outputs = []
                    for index in range(1, rate_hz * 5 + 1):
                        t = index / rate_hz
                        accel = [0.0, 0.0, 1.0]
                        if 1.0 <= t < 1.25:
                            accel[axis] = 0.05
                        outputs.append(flt.update((0, 0, 0), accel, t))
                    peak = max(abs(out[axis + 1]) for out in outputs)
                    self.assertLess(peak, 0.1)
                    for component in flt._tilt_bias:
                        self.assertLess(abs(math.degrees(component)), 0.001)

    def test_filtered_innovation_cannot_push_against_current_gravity_error(self):
        flt = _level_filter(gyro_bias_tracking_gain=0.0)
        flt._tilt_err_lp1 = [0.0, 0.05, 0.0]
        flt._tilt_err_lp2 = [0.0, 0.05, 0.0]
        flt.q = flt._quat_from_euler(0.0, 0.2, 0.0)
        correction = flt._tilt_correction_rates((0, 0, 1), 1.0, 0.0, 0.004)
        self.assertLessEqual(correction[1], 0.0)
        self.assertLessEqual(flt._tilt_err_lp1[1], 0.0)
        self.assertLessEqual(flt._tilt_err_lp2[1], 0.0)

    def test_smooth_translation_pulse_has_small_error_and_no_delayed_peak(self):
        peaks = []
        for rate_hz in (125, 250, 500):
            flt = _level_filter()
            during, after = [], []
            for index in range(1, rate_hz * 5 + 1):
                t = index / rate_hz
                lateral = 0.05 * math.sin(math.pi * (t - 1.0) / 0.5) ** 2 if 1.0 < t < 1.5 else 0.0
                out = flt.update((0, 0, 0), (-lateral, 0, 1), t)
                (during if t < 1.5 else after).append(abs(out[1]))
            peaks.append(max(during + after))
            self.assertLess(peaks[-1], 0.1)
            self.assertLessEqual(max(after), max(during) + 0.01)
        self.assertLess(max(peaks) - min(peaks), 0.02)

    def test_real_pitch_turn_remains_gyro_led(self):
        flt = _level_filter()
        errors = []
        for index in range(1, 1251):
            t = index / 250
            pitch = 20.0 * (1.0 - math.cos(2.0 * math.pi * 0.5 * t))
            midpoint = t - 0.002
            gyro_pitch = 20.0 * math.pi * math.sin(math.pi * midpoint)
            angle = math.radians(pitch)
            out = flt.update(
                (0, gyro_pitch, 0), (-math.sin(angle), 0, math.cos(angle)), t
            )
            errors.append(abs(out[1] - pitch))
        self.assertLess(max(errors), 0.02)

    def test_untrusted_turn_does_not_charge_innovation_filters(self):
        flt = _level_filter()
        for _ in range(250):
            flt._tilt_correction_rates((-0.05, 0, 1), math.sqrt(1.0025), 300.0, 0.004)
        self.assertLess(abs(flt._tilt_err_lp2[1]), 1e-10)
        self.assertEqual(flt._tilt_bias, [0.0, 0.0, 0.0])

    def test_invalid_acceleration_interrupts_bias_settling_and_clears_error(self):
        flt = _level_filter()
        for _ in range(100):
            flt._tilt_correction_rates((-0.005, 0, 1), math.sqrt(1.000025), 0.0, 0.004)
        self.assertGreater(flt._tilt_bias_settle_s, 0.3)
        flt._tilt_correction_rates((0, 0, 0), 0.0, 0.0, 0.004)
        self.assertEqual(flt._tilt_bias_settle_s, 0.0)
        self.assertEqual(flt._tilt_err_lp2, [0.0, 0.0, 0.0])
        self.assertIsNone(flt._tilt_previous_error)

    def test_learned_bias_is_still_applied_during_distrusted_motion(self):
        flt = _level_filter()
        bias = [math.radians(0.1), math.radians(-0.2), 0.0]
        flt._tilt_bias = bias.copy()
        correction = flt._tilt_correction_rates((-0.05, 0, 1), math.sqrt(1.0025), 300.0, 0.004)
        for actual, expected in zip(correction, bias):
            self.assertAlmostEqual(actual, expected, places=10)
        self.assertEqual(flt._tilt_bias, bias)

    def test_proportional_correction_has_a_vector_rate_limit(self):
        flt = _level_filter(
            gyro_bias_tracking_gain=0.0,
            accel_tau_pitch=0.2,
            accel_tau_roll=0.2,
        )
        tilt = math.radians(20.0)
        accel = (-math.sin(tilt), 0, math.cos(tilt))
        for _ in range(500):
            correction = flt._tilt_correction_rates(accel, 1.0, 0.0, 0.004)
            self.assertLessEqual(
                math.degrees(math.sqrt(sum(c * c for c in correction))), 2.0 + 1e-9
            )

    def test_sensor_clock_restart_resumes_integration_and_clears_transients(self):
        flt = _level_filter(accel_tau_pitch=math.inf, accel_tau_roll=math.inf)
        flt.update((0, 0, 10), (0, 0, 1), 0.004)
        before = flt._euler_from_quat(flt.q)[0]
        flt._tilt_err_lp2 = [0.1, 0.1, 0.1]
        flt.update((0, 0, 10), (0, 0, 1), 0.0)
        self.assertEqual(flt._tilt_err_lp2, [0.0, 0.0, 0.0])
        after = flt.update((0, 0, 10), (0, 0, 1), 0.004)[0]
        self.assertAlmostEqual(after - before, -0.04)

    def test_lateral_vibration_does_not_leak_into_pitch_and_roll(self):
        filter_instance = _level_filter()

        def vibrating_accel(t):
            # 0.05 g sideways head vibration: ~2.9 deg of apparent tilt.
            return (
                0.05 * math.sin(2 * math.pi * 6.0 * t),
                0.05 * math.cos(2 * math.pi * 4.0 * t),
                1.0,
            )

        peak = 0.0
        for index in range(1, 250 * 6 + 1):
            timestamp = index / 250
            _, pitch, roll, _, _ = filter_instance.update(
                (0.0, 0.0, 0.0), vibrating_accel(timestamp), timestamp
            )
            if timestamp > 2.0:
                peak = max(peak, abs(pitch), abs(roll))
        self.assertLess(peak, 0.05)

    def test_converges_to_static_tilt_independent_of_sample_rate(self):
        tilt = math.radians(5.0)

        def tilted(_):
            return (-math.sin(tilt), 0.0, math.cos(tilt))

        results = []
        for rate_hz in (125, 250, 500):
            filter_instance = _level_filter(gyro_bias_tracking_gain=0.0)
            results.append(_run(filter_instance, 1.0, rate_hz, (0, 0, 0), tilted)[1])
            final_pitch = _run(filter_instance, 7.0, rate_hz, (0, 0, 0), tilted)[1]
            self.assertAlmostEqual(final_pitch, 5.0, delta=0.05)
        self.assertGreater(results[0], 1.0)
        self.assertLess(max(results) - min(results), 0.1)

    def test_online_bias_tracking_removes_residual_tilt_error(self):
        def level(_):
            return (0.0, 0.0, 1.0)

        residual_bias = (0.2, -0.2, 0.0)
        untracked = _level_filter(gyro_bias_tracking_gain=0.0)
        tracked = _level_filter()
        untracked_output = _run(untracked, 60.0, 250, residual_bias, level)
        tracked_output = _run(tracked, 60.0, 250, residual_bias, level)

        self.assertGreater(abs(untracked_output[2]), 0.15)
        self.assertLess(abs(tracked_output[1]), 0.02)
        self.assertLess(abs(tracked_output[2]), 0.02)

    def test_infinite_time_constant_disables_accel_correction(self):
        filter_instance = _level_filter(
            accel_tau_pitch=math.inf, accel_tau_roll=math.inf
        )
        output = _run(filter_instance, 2.0, 250, (0, 0, 0), lambda _: (0.3, 0.3, 0.9))
        for angle in output[:3]:
            self.assertAlmostEqual(angle, 0.0, places=9)

    def test_resting_off_center_under_center_assist_does_not_learn_bias(self):
        # Center assist pulls toward 0 deg while gravity says 3 deg; that
        # disagreement is not gyro bias and must not wind up the integrator.
        filter_instance = QuaternionComplementaryFilter()
        tilt = math.radians(3.0)
        rested = (-math.sin(tilt), 0.0, math.cos(tilt))
        _run(filter_instance, 30.0, 250, (0.0, 0.0, 0.0), lambda _: rested)
        self.assertIsNotNone(filter_instance._drift_correction_start)
        for component in filter_instance._tilt_bias:
            self.assertLess(abs(math.degrees(component)), 0.01)

    def test_disabling_one_axis_drops_its_learned_bias(self):
        filter_instance = _level_filter()
        _run(filter_instance, 30.0, 250, (0.5, 0.0, 0.0), lambda _: (0.0, 0.0, 1.0))
        self.assertNotAlmostEqual(filter_instance._tilt_bias[0], 0.0, places=4)

        filter_instance.accel_tau_roll = math.inf
        correction = filter_instance._tilt_correction_rates((0.0, 0.0, 1.0), 1.0, 0.0, 0.004)
        self.assertEqual(correction[0], 0.0)

    def test_first_sample_seeds_tilt_from_accelerometer(self):
        filter_instance = QuaternionComplementaryFilter(center_threshold=0.0)
        tilt = math.radians(10.0)
        accel = (0.0, math.sin(tilt), math.cos(tilt))
        self.assertEqual(filter_instance.update((0, 0, 0), accel, 0.0)[:3], (0.0, 0.0, 0.0))
        _, pitch, roll, _, _ = filter_instance.update((0, 0, 0), accel, 0.004)
        self.assertAlmostEqual(roll, 10.0, places=3)
        self.assertAlmostEqual(pitch, 0.0, places=3)


class YawCrossCouplingTests(unittest.TestCase):
    def test_yaw_on_tilted_head_does_not_leak_into_pitch_or_roll(self):
        for tau in (math.inf, 1.0):
            with self.subTest(accel_tau=tau):
                filter_instance = QuaternionComplementaryFilter(center_threshold=0.0)
                filter_instance.accel_tau_pitch = filter_instance.accel_tau_roll = tau
                worst = _worn_yaw_sweep(
                    filter_instance, _sine_yaw(300.0, 0.5, 0.5),
                    tilt_pitch=15.0, tilt_roll=8.0, lever_m=(0.0, 0.0, 0.0), seconds=5.0,
                )
                self.assertLess(worst, 0.01)

    def test_off_axis_sensor_turns_do_not_create_false_tilt(self):
        for name, profile in (
            ('snap', _snap_yaw(80.0, 0.35, 0.8, 0.5)),
            ('sine', _sine_yaw(300.0, 0.5, 0.5)),
        ):
            with self.subTest(profile=name):
                filter_instance = QuaternionComplementaryFilter(center_threshold=0.0)
                worst = _worn_yaw_sweep(
                    filter_instance, profile, tilt_pitch=15.0, tilt_roll=8.0,
                    lever_m=(0.12, 0.02, 0.06), seconds=10.0,
                )
                self.assertLess(worst, 0.25)

    def test_level_yaw_keeps_application_sign_convention(self):
        filter_instance = _level_filter()
        yaw = _run(filter_instance, 1.0, 250, (0.0, 0.0, 10.0), lambda _: (0.0, 0.0, 1.0))[0]
        self.assertAlmostEqual(yaw, -10.0, places=6)


class WorkerBatchTests(unittest.TestCase):
    def test_fusion_publishes_identical_sample_history_across_batches(self):
        lines = []
        for index in range(100):
            if index < 40:
                timestamp = index / 250
            elif index < 70:
                timestamp = (index - 40) / 250
            else:
                timestamp = 1.0 + (index - 70) / 250
            lines.append(f"{timestamp},0,0,1,0,0,{20 * math.sin(index / 30)}")
        lines.insert(26, lines[25])
        histories = []
        for batch_size in (1, 7, 100):
            serial_queue = Queue()
            output_queue = Queue()
            stop_event = threading.Event()
            pending = []
            for index in range(0, len(lines), batch_size):
                pending.extend(lines[index:index + batch_size])
                pending.append(None)

            def get_sample(queue, timeout=0.0, default=None):
                if queue is serial_queue:
                    if pending:
                        return pending.pop(0)
                    stop_event.set()
                return default

            flt = QuaternionComplementaryFilter(center_threshold=0.0)
            with patch('src.workers.fusion_wrk.QuaternionComplementaryFilter', return_value=flt), \
                    patch('src.workers.fusion_wrk.safe_queue_get', side_effect=get_sample), \
                    patch('src.workers.fusion_wrk.time.sleep'), \
                    patch('src.util.timing_utils.enable_high_res_timer'), \
                    patch('src.util.timing_utils.disable_high_res_timer'), \
                    patch('src.util.timing_utils.raise_process_priority'):
                run_worker(
                    serial_queue, output_queue, Queue(), Queue(), Queue(),
                    stop_event, Queue(),
                )
            histories.append([
                sample for batch in output_queue.queue for sample in batch
            ])
        self.assertEqual(len(histories[0]), 98)
        self.assertEqual(
            [s.timestamp for s in histories[0] if s.reset_filter],
            [0.004, 1.004],
        )
        self.assertEqual(histories[0], histories[1])
        self.assertEqual(histories[0], histories[2])

    def test_worker_integrates_every_queued_sample(self):
        filter_instance = QuaternionComplementaryFilter()
        filter_instance.update = Mock(wraps=filter_instance.update)
        serial_queue = Queue()
        stop_event = threading.Event()
        lines = [f"{index * 0.004:.3f},0,0,1,0,0,10" for index in range(1, 9)]
        # Appended magnetometer XYZ is ignored; legacy seven-field rows remain valid.
        lines[3] += ",42.0,-10.0,5.0"
        lines.insert(4, "garbage")
        pending = list(lines)

        def get_sample(queue, timeout=0.0, default=None):
            if queue is serial_queue:
                if pending:
                    return pending.pop(0)
                stop_event.set()
            return default

        output_queue = Queue()
        with patch('src.workers.fusion_wrk.QuaternionComplementaryFilter', return_value=filter_instance), \
                patch('src.workers.fusion_wrk.safe_queue_get', side_effect=get_sample), \
                patch('src.workers.fusion_wrk.time.sleep'), \
                patch('src.util.timing_utils.enable_high_res_timer'), \
                patch('src.util.timing_utils.disable_high_res_timer'), \
                patch('src.util.timing_utils.raise_process_priority'):
            run_worker(
                serial_queue, output_queue, Queue(), Queue(), Queue(),
                stop_event, Queue(),
            )

        self.assertEqual(filter_instance.update.call_count, 8)
        published = list(output_queue.queue)
        self.assertEqual(len(published), 1)
        batch = published[0]
        self.assertEqual(len(batch), 8)
        self.assertEqual([sample.timestamp for sample in batch], [i * 0.004 for i in range(1, 9)])
        # 7 integrated steps of 4 ms at 10 deg/s (negated yaw axis).
        self.assertAlmostEqual(batch[-1].angles[0], -0.28, places=6)
        self.assertEqual(batch[-1].rates, (-10.0, 0.0, 0.0))


class EulerRateTests(unittest.TestCase):
    def test_level_rates_match_application_axes(self):
        filter_instance = _level_filter()
        _run(filter_instance, 0.1, 250, (3.0, 5.0, 10.0), lambda _: (0.0, 0.0, 1.0))
        yaw_rate, pitch_rate, roll_rate = filter_instance.euler_rates
        self.assertAlmostEqual(yaw_rate, -10.0, delta=0.5)
        self.assertAlmostEqual(pitch_rate, 5.0, delta=0.5)
        self.assertAlmostEqual(roll_rate, 3.0, delta=0.5)

    def test_rates_match_angle_derivative_on_tilted_head(self):
        filter_instance = QuaternionComplementaryFilter(center_threshold=0.0)
        filter_instance.accel_tau_pitch = filter_instance.accel_tau_roll = math.inf
        head_tilt = _qmul(_axis_quat((0, 1, 0), 20.0), _axis_quat((1, 0, 0), 10.0))
        rate = math.radians(60.0)
        previous = None
        worst = 0.0
        for index in range(300):
            t = index / 250
            q = _qmul(_axis_quat((0, 0, 1), math.degrees(rate * t)), head_tilt)
            gyro = tuple(math.degrees(c) for c in _rotate(q, (0, 0, rate), inverse=True))
            output = filter_instance.update(gyro, _rotate(q, (0, 0, 1), inverse=True), t)
            if previous is not None and index > 10:
                derivative = [
                    (output[axis] - previous[axis]) * 250 for axis in range(3)
                ]
                worst = max(
                    worst,
                    *(abs(d - r) for d, r in zip(derivative, filter_instance.euler_rates)),
                )
            previous = output
        self.assertLess(worst, 1.0)

    def test_early_return_publishes_zero_rates(self):
        filter_instance = _level_filter()
        filter_instance.update((0.0, 0.0, 10.0), (0.0, 0.0, 1.0), 0.004)
        filter_instance.update((0.0, 0.0, 10.0), (0.0, 0.0, 1.0), 0.004)
        self.assertEqual(filter_instance.euler_rates, (0.0, 0.0, 0.0))


class AccelTauPreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_time_constants_persist_and_legacy_alpha_is_ignored(self):
        control_queue = Queue()
        preferences = PreferencesPanel(
            preferences_manager=Mock(), control_queue=control_queue
        )
        self.addCleanup(preferences.close)

        preferences._load_accel_tau_settings(
            {'alpha_pitch': '0.950', 'accel_tau_roll': '2.5', 'accel_tau_pitch': '99'}
        )
        self.assertEqual(preferences.accel_tau_roll, 2.5)
        self.assertEqual(preferences.accel_tau_pitch, 10.0)
        tuning = preferences.get_tuning_preferences()
        self.assertEqual(tuning['accel_tau_roll'], '2.5')
        self.assertNotIn('alpha_pitch', tuning)

        preferences._pending_accel_tau_roll = None
        preferences.accel_tau_roll_slider.setValue(30)
        preferences._apply_accel_tau_roll()
        self.assertIn(('set_accel_tau_roll', 3.0), list(control_queue.queue))


if __name__ == "__main__":
    unittest.main()
