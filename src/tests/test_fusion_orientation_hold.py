"""Regression tests for quaternion-based orientation hold and resume."""

import math
import unittest

from src.workers.fusion_wrk import QuaternionComplementaryFilter


class OrientationHoldTests(unittest.TestCase):
    def setUp(self):
        self.filter = QuaternionComplementaryFilter()

    def test_orientation_remains_held_while_sensor_pose_changes(self):
        held_pose = self.filter.apply_orientation_hold(25.0, -8.0, 12.0, now=0.0)
        self.filter.set_orientation_hold(True, now=0.0)

        output_pose = self.filter.apply_orientation_hold(
            135.0, 35.0, -40.0, now=0.5
        )

        self.assertAlmostEqual(output_pose[0], held_pose[0])
        self.assertAlmostEqual(output_pose[1], held_pose[1])
        self.assertAlmostEqual(output_pose[2], held_pose[2])

    def test_tracking_without_hold_preserves_existing_euler_values(self):
        sensor_pose = (20.0, 110.0, -30.0)

        self.assertEqual(
            self.filter.apply_orientation_hold(*sensor_pose, now=0.0),
            sensor_pose,
        )

    def test_recenter_to_current_sets_full_output_pose_as_zero(self):
        self.filter.center_offset_yaw = 4.0
        self.filter.center_offset_pitch = -3.0
        self.filter.center_offset_roll = 2.0
        self.filter.q = self.filter._quat_from_euler(37.0, 14.0, -12.0)
        self.filter.last_time = 0.0

        self.filter.recenter_to_current()
        centered_pose = self.filter.update(
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.01
        )[:3]
        self.assertAlmostEqual(centered_pose[0], 0.0)
        self.assertAlmostEqual(centered_pose[1], 0.0)
        self.assertAlmostEqual(centered_pose[2], 0.0)

        self.filter.q = self.filter._quat_from_euler(47.0, 20.0, -10.0)
        moved_pose = self.filter.update(
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.02
        )[:3]
        self.assertAlmostEqual(moved_pose[0], 10.0)
        self.assertAlmostEqual(moved_pose[1], 6.0)
        self.assertAlmostEqual(moved_pose[2], 2.0)
        self.assertEqual(self.filter.center_offset_yaw, 4.0)
        self.assertEqual(self.filter.center_offset_pitch, -3.0)
        self.assertEqual(self.filter.center_offset_roll, 2.0)

    def test_normal_filter_reset_clears_full_pose_recenter_offsets(self):
        self.filter.q = self.filter._quat_from_euler(30.0, 10.0, -5.0)
        self.filter.recenter_to_current()

        self.filter.reset()

        self.assertEqual(self.filter.recenter_offset_yaw, 0.0)
        self.assertEqual(self.filter.recenter_offset_pitch, 0.0)
        self.assertEqual(self.filter.recenter_offset_roll, 0.0)

    def test_resume_blends_smoothly_to_current_sensor_pose(self):
        held_pose = self.filter.apply_orientation_hold(0.0, 0.0, 0.0, now=0.0)
        self.filter.set_orientation_hold(True, now=0.0)
        self.filter.apply_orientation_hold(0.0, 0.0, 0.0, now=0.1)
        self.filter.set_orientation_hold(False, now=1.0)

        first_pose = self.filter.apply_orientation_hold(90.0, 0.0, 0.0, now=1.0)
        middle_pose = self.filter.apply_orientation_hold(90.0, 0.0, 0.0, now=1.5)
        resumed_pose = self.filter.apply_orientation_hold(90.0, 0.0, 0.0, now=3.0)

        self.assertAlmostEqual(first_pose[0], held_pose[0])
        self.assertGreater(middle_pose[0], first_pose[0])
        self.assertLess(middle_pose[0], resumed_pose[0])
        self.assertAlmostEqual(resumed_pose[0], 90.0)
        self.assertIsNone(self.filter._held_output_q)

    def test_resume_uses_configured_curve_smoothing_time_and_strength(self):
        self.filter.drift_smoothing_time = 2.0
        self.filter.drift_correction_strength = 0.3
        self.filter.drift_curve_type = 'linear'
        self.filter.apply_orientation_hold(0.0, 0.0, 0.0, now=0.0)
        self.filter.set_orientation_hold(True, now=0.0)
        self.filter.apply_orientation_hold(0.0, 0.0, 0.0, now=0.1)
        self.filter.set_orientation_hold(False, now=1.0)

        halfway_pose = self.filter.apply_orientation_hold(
            90.0, 0.0, 0.0, now=2.0
        )
        completed_pose = self.filter.apply_orientation_hold(
            90.0, 0.0, 0.0, now=3.0
        )

        self.assertAlmostEqual(halfway_pose[0], 45.0)
        self.assertAlmostEqual(completed_pose[0], 90.0)

    def test_stronger_correction_strength_shortens_resume_time(self):
        self.filter.drift_smoothing_time = 2.0
        self.filter.drift_correction_strength = 0.6
        self.filter.drift_curve_type = 'linear'
        self.filter.apply_orientation_hold(0.0, 0.0, 0.0, now=0.0)
        self.filter.set_orientation_hold(True, now=0.0)
        self.filter.apply_orientation_hold(0.0, 0.0, 0.0, now=0.1)
        self.filter.set_orientation_hold(False, now=1.0)

        halfway_pose = self.filter.apply_orientation_hold(
            90.0, 0.0, 0.0, now=1.5
        )
        completed_pose = self.filter.apply_orientation_hold(
            90.0, 0.0, 0.0, now=2.0
        )

        self.assertAlmostEqual(halfway_pose[0], 45.0)
        self.assertAlmostEqual(completed_pose[0], 90.0)

    def test_resume_uses_each_configured_transition_curve(self):
        expected_midpoints = {
            'linear': 0.5,
            'cosine': 0.5,
            'quadratic': 0.25,
            'exponential': (1.0 - math.exp(-1.5))
            / (1.0 - math.exp(-3.0)),
        }
        for curve, expected_progress in expected_midpoints.items():
            with self.subTest(curve=curve):
                filter_instance = QuaternionComplementaryFilter()
                filter_instance.drift_smoothing_time = 2.0
                filter_instance.drift_correction_strength = 0.3
                filter_instance.drift_curve_type = curve
                filter_instance.apply_orientation_hold(0.0, 0.0, 0.0, now=0.0)
                filter_instance.set_orientation_hold(True, now=0.0)
                filter_instance.apply_orientation_hold(0.0, 0.0, 0.0, now=0.1)
                filter_instance.set_orientation_hold(False, now=1.0)

                midpoint = filter_instance.apply_orientation_hold(
                    90.0, 0.0, 0.0, now=2.0
                )

                self.assertAlmostEqual(
                    midpoint[0], 90.0 * expected_progress, places=4
                )

    def test_reholding_during_resume_freezes_the_current_blended_pose(self):
        self.filter.apply_orientation_hold(0.0, 0.0, 0.0, now=0.0)
        self.filter.set_orientation_hold(True, now=0.0)
        self.filter.apply_orientation_hold(0.0, 0.0, 0.0, now=0.1)
        self.filter.set_orientation_hold(False, now=1.0)
        blended_pose = self.filter.apply_orientation_hold(
            90.0, 0.0, 0.0, now=1.5
        )
        self.filter.set_orientation_hold(True, now=1.5)

        held_pose = self.filter.apply_orientation_hold(
            150.0, -20.0, 30.0, now=1.8
        )

        for actual, expected in zip(held_pose, blended_pose):
            self.assertAlmostEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
