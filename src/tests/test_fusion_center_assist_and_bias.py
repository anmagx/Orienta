"""Regression tests for continuous center assistance and three-axis gyro bias."""

from collections import deque
import math
from queue import Queue
import threading
import unittest
from unittest.mock import patch

from src.workers.fusion_wrk import QuaternionComplementaryFilter, run_worker


class CenterAssistTests(unittest.TestCase):
    def make_filter(self, curve='cosine'):
        filter_instance = QuaternionComplementaryFilter()
        filter_instance.alpha_pitch = 1.0
        filter_instance.alpha_roll = 1.0
        filter_instance.drift_curve_type = curve
        filter_instance._stationary_debounce_s = 0.0
        filter_instance.last_time = 0.0
        filter_instance._stationary_start = 0.0
        filter_instance._drift_correction_start = 0.0
        filter_instance.q = filter_instance._quat_from_euler(4.0, 0.0, 0.0)
        return filter_instance

    def run_stationary(self, filter_instance, duration, steps=(0.004,), gyro=(0, 0, 0)):
        start_time = filter_instance.last_time
        timestamp = start_time
        end_time = start_time + duration
        cycle_time = math.fsum(steps)
        index = 0
        outputs = []
        while timestamp < end_time - 1e-12:
            elapsed = (
                (index // len(steps)) * cycle_time
                + math.fsum(steps[:index % len(steps) + 1])
            )
            timestamp = min(start_time + elapsed, end_time)
            outputs.append(
                filter_instance.update(gyro, (0, 0, 1), timestamp)
            )
            index += 1
        return outputs

    def test_all_curves_continue_correcting_after_engagement(self):
        for curve in ('cosine', 'linear', 'quadratic', 'exponential'):
            with self.subTest(curve=curve):
                filter_instance = self.make_filter(curve)
                at_engagement = self.run_stationary(filter_instance, 2.0)[-1][0]
                outputs = self.run_stationary(filter_instance, 12.0)
                self.assertLess(outputs[-1][0], 0.01)
                self.assertLess(outputs[-1][0], at_engagement / 100)
                self.assertTrue(outputs[-1][3])
                self.assertTrue(outputs[-1][4])
                self.assertTrue(all(0.0 <= output[0] < at_engagement for output in outputs))

    def test_center_assist_is_sample_rate_independent(self):
        for curve in ('cosine', 'linear', 'quadratic', 'exponential'):
            with self.subTest(curve=curve):
                results = [
                    self.run_stationary(self.make_filter(curve), 6.0, steps)[-1][0]
                    for steps in ((0.02,), (0.01,), (0.004,), (0.013, 0.017))
                ]
                for result in results[1:]:
                    self.assertAlmostEqual(result, results[0], places=9)

    def test_engaged_cosine_gain_stays_positive_and_time_based(self):
        filter_instance = self.make_filter()
        gain = filter_instance._calculate_drift_factor(0.004, 20.0)
        self.assertAlmostEqual(gain, 1.0 - math.exp(-0.004 / 2.0))
        self.assertEqual(filter_instance._calculate_drift_factor(0.004, 0.0), 0.0)

    def test_strength_and_smoothing_time_control_attraction_rate(self):
        normal = self.run_stationary(self.make_filter(), 6.0)[-1][0]
        stronger = self.make_filter()
        stronger.drift_correction_strength = 0.6
        stronger_output = self.run_stationary(stronger, 6.0)[-1][0]
        slower = self.make_filter()
        slower.drift_smoothing_time = 4.0
        slower_output = self.run_stationary(slower, 6.0)[-1][0]
        self.assertLess(stronger_output, normal)
        self.assertGreater(slower_output, normal)

    def test_assist_stops_on_motion_and_reengages_from_zero_rate(self):
        filter_instance = self.make_filter()
        self.run_stationary(filter_instance, 3.0)
        moving = filter_instance.update((0, 0, 10), (0, 0, 1), 3.004)
        self.assertFalse(moving[3])
        self.assertFalse(moving[4])
        self.assertIsNone(filter_instance._drift_correction_start)
        resumed = filter_instance.update((0, 0, 0), (0, 0, 1), 3.008)
        self.assertTrue(resumed[3])
        self.assertAlmostEqual(resumed[0], moving[0])

    def test_assist_waits_for_default_stationary_debounce(self):
        filter_instance = QuaternionComplementaryFilter()
        filter_instance.last_time = 0.0
        filter_instance.q = filter_instance._quat_from_euler(4.0, 0.0, 0.0)
        for index in range(1, 15):
            output = filter_instance.update((0, 0, 0), (0, 0, 1), index / 100)
            self.assertFalse(output[3])
            self.assertFalse(output[4])
            self.assertAlmostEqual(output[0], 4.0)
        for index in range(15, 21):
            output = filter_instance.update((0, 0, 0), (0, 0, 1), index / 100)
        self.assertTrue(output[3])
        self.assertTrue(output[4])
        self.assertLess(output[0], 4.0)

    def test_assist_is_gated_by_center_and_valid_acceleration(self):
        for pose, accel in (
            ((10, 0, 0), (0, 0, 1)),
            ((0, 10, 0), (0, 0, 1)),
            ((0, 0, 10), (0, 0, 1)),
            ((4, 0, 0), (0, 0, 2)),
        ):
            with self.subTest(pose=pose, accel=accel):
                filter_instance = self.make_filter()
                filter_instance.q = filter_instance._quat_from_euler(*pose)
                output = filter_instance.update((0, 0, 0), accel, 0.004)
                self.assertFalse(output[3])
                self.assertIsNone(filter_instance._drift_correction_start)
                for actual, expected in zip(output[:3], pose):
                    self.assertAlmostEqual(actual, expected)

    def test_center_assist_respects_offsets_and_yaw_wrap(self):
        filter_instance = self.make_filter()
        filter_instance.center_offset_yaw = 179.0
        filter_instance.center_offset_pitch = 10.0
        filter_instance.center_offset_roll = -8.0
        filter_instance.q = filter_instance._quat_from_euler(-179.0, 12.0, -6.0)
        output = self.run_stationary(filter_instance, 14.0)[-1]
        for angle in output[:3]:
            self.assertLess(abs(angle), 0.01)

    def test_residual_gyro_bias_is_still_corrected_after_engagement(self):
        filter_instance = self.make_filter()
        self.run_stationary(filter_instance, 6.0)
        output = self.run_stationary(
            filter_instance, 20.0, gyro=(0, 0, -0.1)
        )[-1]
        self.assertAlmostEqual(output[0], 0.2, delta=0.005)


class GyroBiasTests(unittest.TestCase):
    def test_calibration_averages_each_raw_axis_and_replaces_previous_bias(self):
        filter_instance = QuaternionComplementaryFilter()
        filter_instance.gyro_bias_roll = 10.0
        filter_instance.gyro_bias_pitch = 20.0
        filter_instance.gyro_bias_yaw = 30.0
        samples = [(0.2, -0.4, 0.6), (0.4, -0.2, 0.8)]
        for _ in range(2):
            filter_instance.calibrate_gyro_bias(samples)
            for actual, expected in zip(
                (filter_instance.gyro_bias_roll, filter_instance.gyro_bias_pitch,
                 filter_instance.gyro_bias_yaw),
                (0.3, -0.3, 0.7),
            ):
                self.assertAlmostEqual(actual, expected)

    def test_empty_calibration_does_not_replace_bias(self):
        filter_instance = QuaternionComplementaryFilter()
        filter_instance.gyro_bias_yaw = 0.5
        with self.assertRaises(ValueError):
            filter_instance.calibrate_gyro_bias([])
        self.assertEqual(filter_instance.gyro_bias_yaw, 0.5)

    def test_bias_is_removed_before_mixed_axis_integration(self):
        bias = (0.3, -0.4, 0.5)
        actual = QuaternionComplementaryFilter(center_threshold=0.0)
        reference = QuaternionComplementaryFilter(center_threshold=0.0)
        actual.calibrate_gyro_bias([bias])
        for filter_instance in (actual, reference):
            filter_instance.alpha_pitch = filter_instance.alpha_roll = 1.0
            filter_instance.last_time = 0.0
            filter_instance.q = filter_instance._quat_from_euler(20, 15, 30)
        for index in range(1, 251):
            motion = (12.0, -8.0, 15.0)
            actual_output = actual.update(
                tuple(rate + offset for rate, offset in zip(motion, bias)),
                (0, 0, 1), index / 250,
            )
            reference_output = reference.update(motion, (0, 0, 1), index / 250)
        for angle, expected in zip(actual_output[:3], reference_output[:3]):
            self.assertAlmostEqual(angle, expected, places=9)

    def test_stationarity_and_assist_use_bias_corrected_gyro(self):
        filter_instance = QuaternionComplementaryFilter()
        filter_instance.last_time = 0.0
        bias = (4.0, -4.0, 1.0)
        filter_instance.calibrate_gyro_bias([bias])
        for index in range(1, 251):
            output = filter_instance.update(bias, (0, 0, 1), index / 250)
        self.assertTrue(output[4])
        self.assertTrue(output[3])
        for angle in output[:3]:
            self.assertAlmostEqual(angle, 0.0)


class GyroBiasWorkerTests(unittest.TestCase):
    def run_commands(self, commands, calibration_batches):
        filter_instance = QuaternionComplementaryFilter()
        serial_queue = Queue()
        control_queue = Queue()
        status_queue = Queue()
        log_queue = Queue()
        stop_event = threading.Event()
        commands = deque(commands)
        calibration_batches = deque(calibration_batches)
        samples = deque()
        calls = 0

        def get_sample(queue, timeout=0.0, default=None):
            nonlocal calls
            calls += 1
            self.assertLess(calls, 200, "Worker did not finish scripted commands")
            if queue is control_queue:
                if commands:
                    command = commands.popleft()
                    if isinstance(command, tuple) and command[0] == 'recalibrate_gyro_bias':
                        samples.extend(calibration_batches.popleft())
                    return command
            elif queue is serial_queue:
                if samples:
                    return samples.popleft()
                if not commands:
                    stop_event.set()
            return default

        with patch('src.workers.fusion_wrk.QuaternionComplementaryFilter', return_value=filter_instance), \
                patch('src.workers.fusion_wrk.safe_queue_get', side_effect=get_sample), \
                patch('src.workers.fusion_wrk.time.sleep'), \
                patch('src.util.timing_utils.enable_high_res_timer'), \
                patch('src.util.timing_utils.disable_high_res_timer'), \
                patch('src.util.timing_utils.raise_process_priority'):
            run_worker(
                serial_queue, Queue(), Queue(), control_queue, status_queue,
                stop_event, log_queue,
            )
        return filter_instance, list(status_queue.queue), list(log_queue.queue)

    def test_worker_calibrates_all_axes_from_valid_raw_stationary_samples(self):
        samples = [
            '0.01,0,0,1,0.2,-0.4,0.6',
            '0.02,0,0,1,6,0,0',
            '0.03,0,0,2,0.4,-0.2,0.8',
            '0.04,0,0,1,nan,0,0',
            '0.05,0,0,1,0.4,-0.2,0.8',
        ]
        filter_instance, statuses, logs = self.run_commands(
            [('recalibrate_gyro_bias', 2), ('recalibrate_gyro_bias', 2)],
            [samples, samples],
        )
        self.assertAlmostEqual(filter_instance.gyro_bias_roll, 0.3)
        self.assertAlmostEqual(filter_instance.gyro_bias_pitch, -0.3)
        self.assertAlmostEqual(filter_instance.gyro_bias_yaw, 0.7)
        self.assertEqual(filter_instance.last_time, 0.05)
        self.assertTrue(filter_instance.gyro_calibrated)
        self.assertEqual(statuses.count(('gyro_calibrated', True)), 2)
        self.assertTrue(any('X=0.300000, Y=-0.300000, Z=0.700000' in log[2] for log in logs))

    def test_recenter_preserves_all_biases_and_full_reset_clears_them(self):
        for command in ('reset_orientation', 'reset'):
            with self.subTest(command=command):
                filter_instance, statuses, _ = self.run_commands(
                    [('recalibrate_gyro_bias', 1), command],
                    [['0.01,0,0,1,0.3,-0.4,0.5']],
                )
                expected = (0.3, -0.4, 0.5) if command == 'reset_orientation' else (0, 0, 0)
                self.assertEqual(
                    (filter_instance.gyro_bias_roll, filter_instance.gyro_bias_pitch,
                     filter_instance.gyro_bias_yaw),
                    expected,
                )
                self.assertEqual(filter_instance.gyro_calibrated, command == 'reset_orientation')
                self.assertIn(('gyro_calibrated', command == 'reset_orientation'), statuses)


if __name__ == "__main__":
    unittest.main()
