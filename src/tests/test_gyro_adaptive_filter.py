"""Tests for the gyro-assisted adaptive pose filter."""

import math
import random
import unittest

from src.filters import get_filter_definition
from src.filters.gyro_adaptive import GyroAdaptivePoseFilter
from src.outputs.native_output_processing import (
    NativeOutputProcessor,
    default_native_output_settings,
)


DT = 0.01


def make_filter(**overrides):
    definition = get_filter_definition("gyro_adaptive")
    return definition.create({**definition.default_settings(), **overrides})


class GyroAdaptiveFilterTests(unittest.TestCase):
    def test_suppresses_tremor_at_rest(self):
        flt = make_filter()
        rng = random.Random(1)
        outputs = []
        for step in range(1000):
            t = step * DT
            angle = 0.2 * math.sin(2 * math.pi * 6 * t) + rng.gauss(0, 0.05)
            rate = 0.2 * 2 * math.pi * 6 * math.cos(2 * math.pi * 6 * t)
            outputs.append(flt.process((angle, angle, angle), t, (rate,) * 3))
        tail = [pose[0] for pose in outputs[500:]]
        self.assertLess(max(tail) - min(tail), 0.1)

    def test_follows_fast_turn_with_little_lag(self):
        flt = make_filter()
        for step in range(100):
            flt.process((0.0, 0.0, 0.0), step * DT, (0.0, 0.0, 0.0))
        rate = 200.0
        angle = 0.0
        for step in range(100, 130):
            angle += rate * DT
            pose = flt.process((angle, 0.0, 0.0), step * DT, (rate, 0.0, 0.0))
        self.assertLess(abs(angle - pose[0]), 2.0)
        self.assertEqual(pose[1:], (0.0, 0.0))

    def test_small_reverse_twitch_releases_previous_snap(self):
        flt = make_filter()
        timestamp = 0.0
        angle = 0.0
        pose = flt.process((angle, 0.0, 0.0), timestamp, (0.0, 0.0, 0.0))

        for _ in range(15):
            timestamp += DT
            angle += 100.0 * DT
            pose = flt.process(
                (angle, 0.0, 0.0), timestamp, (100.0, 0.0, 0.0)
            )
        turn_pose = pose[0]

        for _ in range(8):
            timestamp += DT
            angle -= 6.0 * DT
            pose = flt.process(
                (angle, 0.0, 0.0), timestamp, (-6.0, 0.0, 0.0)
            )

        self.assertLess(abs(pose[0] - turn_pose), 0.15)

    def test_fast_counterturn_still_reopens_snap_response(self):
        flt = make_filter()
        timestamp = 0.0
        angle = 0.0
        flt.process((angle, 0.0, 0.0), timestamp, (0.0, 0.0, 0.0))
        for _ in range(15):
            timestamp += DT
            angle += 100.0 * DT
            flt.process((angle, 0.0, 0.0), timestamp, (100.0, 0.0, 0.0))

        for _ in range(8):
            timestamp += DT
            angle -= 80.0 * DT
            pose = flt.process(
                (angle, 0.0, 0.0), timestamp, (-80.0, 0.0, 0.0)
            )

        self.assertLess(abs(pose[0] - angle), 2.0)

    def test_slow_counterturn_catches_up_after_direction_reversal(self):
        flt = make_filter()
        timestamp = 0.0
        angle = 0.0
        flt.process((angle, 0.0, 0.0), timestamp, (0.0, 0.0, 0.0))
        for _ in range(15):
            timestamp += DT
            angle += 100.0 * DT
            flt.process((angle, 0.0, 0.0), timestamp, (100.0, 0.0, 0.0))

        for _ in range(50):
            timestamp += DT
            angle -= 5.0 * DT
            pose = flt.process(
                (angle, 0.0, 0.0), timestamp, (-5.0, 0.0, 0.0)
            )

        self.assertLess(abs(pose[0] - angle), 0.5)

    def test_follows_slow_sustained_pan(self):
        flt = make_filter()
        angle = 0.0
        for step in range(300):
            angle += 5.0 * DT
            pose = flt.process((angle, 0.0, 0.0), step * DT, (5.0, 0.0, 0.0))
        self.assertLess(abs(angle - pose[0]), 0.3)

    def test_minor_axis_of_diagonal_turn_follows(self):
        def pitch_lag(yaw_rate):
            flt = make_filter()
            for step in range(100):
                flt.process((0.0, 0.0, 0.0), step * DT, (0.0, 0.0, 0.0))
            yaw = pitch = 0.0
            for step in range(100, 200):
                yaw += yaw_rate * DT
                pitch += 2.5 * DT
                pose = flt.process(
                    (yaw, pitch, 0.0), step * DT, (yaw_rate, 2.5, 0.0)
                )
            return pitch - pose[1]

        self.assertGreater(pitch_lag(0.0), 0.2)
        self.assertLess(pitch_lag(30.0), 0.1)

    def test_idle_axis_stays_smoothed_during_fast_yaw_turn(self):
        flt = make_filter()
        pitches = []
        yaw = 0.0
        for step in range(400):
            t = step * DT
            moving = step >= 100
            yaw += 100.0 * DT if moving else 0.0
            pitch = 0.1 * math.sin(2 * math.pi * 8 * t)
            pitch_rate = 0.1 * 2 * math.pi * 8 * math.cos(2 * math.pi * 8 * t)
            pose = flt.process(
                (yaw, pitch, 0.0), t, (100.0 if moving else 0.0, pitch_rate, 0.0)
            )
            if step >= 200:
                pitches.append(pose[1])
        self.assertLess(max(pitches) - min(pitches), 0.05)

    def test_falls_back_to_pose_derivative_without_rates(self):
        flt = make_filter()
        angle = 0.0
        for step in range(130):
            if step >= 100:
                angle += 200.0 * DT
            pose = flt.process((angle, 0.0, 0.0), step * DT)
        self.assertLess(abs(angle - pose[0]), 2.0)

    def test_invalid_rates_use_fallback(self):
        flt = make_filter()
        flt.process((0.0, 0.0, 0.0), 0.0, (0.0, 0.0, 0.0))
        pose = flt.process((0.01, 0.0, 0.0), DT, (float("nan"), 0.0, 0.0))
        self.assertTrue(all(math.isfinite(value) for value in pose))

    def test_large_step_is_caught_up_quickly(self):
        flt = make_filter()
        flt.process((0.0, 0.0, 0.0), 0.0, (0.0, 0.0, 0.0))
        for step in range(1, 21):
            pose = flt.process((0.0, 10.0, 0.0), step * DT, (0.0, 0.0, 0.0))
        self.assertGreater(pose[1], 9.0)

    def test_yaw_wraps_without_spinning(self):
        flt = make_filter()
        flt.process((179.5, 0.0, 0.0), 0.0, (0.0, 0.0, 0.0))
        pose = flt.process((-179.5, 0.0, 0.0), DT, (100.0, 0.0, 0.0))
        self.assertGreaterEqual(pose[0], 179.5)
        self.assertLess(pose[0], 181.0)

    def test_reset_clears_learned_tremor(self):
        flt = make_filter()
        for step in range(300):
            rate = 30.0 * (1 if step % 2 else -1)
            flt.process((0.0, 0.0, 0.0), step * DT, (rate, 0.0, 0.0))
        self.assertGreater(flt._tremor_var[0], 0.0)
        flt.process((0.0, 0.0, 0.0), 100.0, (0.0, 0.0, 0.0))
        self.assertGreater(flt._tremor_var[0], 0.0)
        flt.reset()
        self.assertEqual(flt._tremor_var, [0.0, 0.0, 0.0])

    def test_rejects_non_finite_input(self):
        flt = make_filter()
        with self.assertRaises(ValueError):
            flt.process((float("nan"), 0.0, 0.0), 0.0)


class GyroAdaptiveProcessorTests(unittest.TestCase):
    def _processor(self, filter_type):
        settings = default_native_output_settings()
        settings["filter_type"] = filter_type
        settings["filter_enabled"] = True
        return NativeOutputProcessor(settings)

    def test_processor_passes_rates_to_rate_aware_filter(self):
        processor = self._processor("gyro_adaptive")
        self.assertIsInstance(processor._filter, GyroAdaptivePoseFilter)
        calls = []
        original = processor._filter.process

        def spy(angles, timestamp, rates=None):
            calls.append(rates)
            return original(angles, timestamp, rates)

        processor._filter.process = spy
        processor.process((0.0, 0.0, 0.0), 0.0, (1.0, 2.0, 3.0))
        self.assertEqual(calls, [(1.0, 2.0, 3.0)])

    def test_processor_ignores_rates_for_angle_only_filters(self):
        processor = self._processor("accela_style")
        processor.process((0.0, 0.0, 0.0), 0.0, (1.0, 2.0, 3.0))
        processor.process((1.0, 0.0, 0.0), DT, (1.0, 2.0, 3.0))


if __name__ == "__main__":
    unittest.main()
