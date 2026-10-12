"""Tests for native-output pose mapping, filtering, and preferences."""

import math
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent, QPalette
from PyQt5.QtWidgets import QApplication, QDialog, QFrame, QSlider

from src.filters import FILTER_REGISTRY, available_filters, get_filter_definition
from src.filters.base import FilterDefinition, FilterParameter
from src.workers.gui_qt.panels.native_output_settings_dialog import (
    NativeOutputSettingsDialog,
    ResponseCurveWidget,
    native_output_preferences_from_section,
    native_output_section_from_settings,
)
from src.workers.gui_qt.panels.input_limit_calibration import (
    InputMagnitudeCalibration,
    symmetric_limits_from_samples,
)
from src.outputs.native_output_processing import (
    NativeOutputProcessor,
    default_native_output_settings,
    default_response_curve,
    map_response_curve,
    normalize_native_output_settings,
    validate_curve,
)


class NativeOutputProcessingTests(unittest.TestCase):
    def test_one_euro_filter_is_registered_with_its_settings(self):
        settings = default_native_output_settings()
        normalized = normalize_native_output_settings(settings)
        self.assertEqual(normalized["filter_type"], "one_euro")
        self.assertEqual(
            set(normalized["filter_settings"]["one_euro"]),
            {"min_cutoff_hz", "beta", "derivative_cutoff_hz"},
        )
        self.assertEqual(
            [definition.identifier for definition in available_filters()],
            [
                "one_euro",
                "accela_style",
                "critically_damped",
                "ema",
                "hysteresis",
            ],
        )
        self.assertEqual(
            normalized["filter_settings"]["accela_style"],
            {
                "response_hz": 2.0,
                "acceleration_gain": 3.0,
                "acceleration_scale_deg": 2.0,
                "deadzone_deg": 0.03,
            },
        )
        self.assertEqual(
            normalized["filter_settings"]["critically_damped"],
            {"response_hz": 5.0},
        )
        self.assertEqual(
            normalized["filter_settings"]["ema"],
            {"cutoff_hz": 2.0},
        )
        self.assertEqual(
            normalized["filter_settings"]["hysteresis"],
            {"threshold_deg": 0.25, "release_percent": 50.0},
        )

    def test_ema_filter_smooths_pose_and_handles_yaw_wraparound(self):
        ema = get_filter_definition("ema").create({"cutoff_hz": 2.0})
        self.assertEqual(ema.process((0.0, 0.0, 0.0), 1.0), (0.0, 0.0, 0.0))
        smoothed = ema.process((60.0, 0.0, 0.0), 1.01)
        self.assertGreater(smoothed[0], 0.0)
        self.assertLess(smoothed[0], 60.0)

        ema.reset()
        ema.process((179.0, 0.0, 0.0), 2.0)
        wrapped = ema.process((-179.0, 0.0, 0.0), 2.01)[0]
        self.assertGreater(wrapped, 179.0)
        self.assertLess(wrapped, 181.0)

    def test_ema_filter_resets_on_invalid_sample_interval(self):
        ema = get_filter_definition("ema").create({"cutoff_hz": 2.0})
        ema.process((0.0, 0.0, 0.0), 1.0)
        self.assertEqual(
            ema.process((30.0, 0.0, 0.0), 1.0),
            (30.0, 0.0, 0.0),
        )
        self.assertEqual(
            ema.process((45.0, 0.0, 0.0), 2.5),
            (45.0, 0.0, 0.0),
        )

    def test_ema_filter_settings_round_trip(self):
        settings = default_native_output_settings()
        settings["filter_type"] = "ema"
        settings["filter_settings"]["ema"] = {"cutoff_hz": 3.4}
        normalized = normalize_native_output_settings(settings)
        restored = native_output_preferences_from_section(
            native_output_section_from_settings(normalized)
        )
        self.assertEqual(restored["filter_type"], "ema")
        self.assertEqual(
            restored["filter_settings"]["ema"],
            {"cutoff_hz": 3.4},
        )
        processor = NativeOutputProcessor(normalized)
        processor.process((0.0, 0.0, 0.0), 1.0)
        smoothed_pose = processor.process((60.0, 0.0, 0.0), 1.01)
        self.assertGreater(smoothed_pose[0], 0.0)
        self.assertLess(smoothed_pose[0], 120.0)

    def test_hysteresis_filter_suppresses_jitter_and_tracks_threshold_crossings(self):
        hysteresis = get_filter_definition("hysteresis").create({
            "threshold_deg": 0.2,
            "release_percent": 50.0,
        })
        self.assertEqual(
            hysteresis.process((0.0, 0.0, 0.0), 1.0),
            (0.0, 0.0, 0.0),
        )
        self.assertEqual(
            hysteresis.process((0.1, -0.1, 0.0), 1.01),
            (0.0, 0.0, 0.0),
        )

        first_crossing = hysteresis.process((0.21, 0.0, 0.0), 1.02)
        self.assertAlmostEqual(first_crossing[0], 0.11)
        held_inside_hysteresis = hysteresis.process((0.25, 0.0, 0.0), 1.03)
        self.assertAlmostEqual(held_inside_hysteresis[0], 0.11)
        second_crossing = hysteresis.process((0.32, 0.0, 0.0), 1.04)
        self.assertAlmostEqual(second_crossing[0], 0.22)

        hysteresis.reset()
        hysteresis.process((179.0, 0.0, 0.0), 2.0)
        wrapped = hysteresis.process((-179.0, 0.0, 0.0), 2.01)[0]
        self.assertGreater(wrapped, 179.0)
        self.assertLess(wrapped, 181.0)

    def test_hysteresis_filter_settings_round_trip(self):
        settings = default_native_output_settings()
        settings["filter_type"] = "hysteresis"
        settings["filter_settings"]["hysteresis"] = {
            "threshold_deg": 0.4,
            "release_percent": 65.0,
        }
        normalized = normalize_native_output_settings(settings)
        restored = native_output_preferences_from_section(
            native_output_section_from_settings(normalized)
        )
        self.assertEqual(restored["filter_type"], "hysteresis")
        self.assertEqual(
            restored["filter_settings"]["hysteresis"],
            {"threshold_deg": 0.4, "release_percent": 65.0},
        )

    def test_critically_damped_filter_tracks_steps_without_overshoot(self):
        smoother = get_filter_definition("critically_damped").create({
            "response_hz": 5.0,
        })
        self.assertEqual(
            smoother.process((0.0, 0.0, 0.0), 1.0),
            (0.0, 0.0, 0.0),
        )
        values = [
            smoother.process((60.0, 0.0, 0.0), 1.0 + index / 120.0)[0]
            for index in range(1, 61)
        ]
        self.assertTrue(all(
            previous <= current
            for previous, current in zip(values, values[1:])
        ))
        self.assertGreater(values[0], 0.0)
        self.assertGreater(values[-1], 59.99)
        self.assertLessEqual(values[-1], 60.0 + 1e-9)

        smoother.reset()
        smoother.process((179.0, 0.0, 0.0), 2.0)
        wrapped = smoother.process((-179.0, 0.0, 0.0), 2.1)[0]
        self.assertGreater(wrapped, 179.0)
        self.assertLess(wrapped, 181.0)

    def test_critically_damped_filter_settings_round_trip(self):
        settings = default_native_output_settings()
        settings["filter_type"] = "critically_damped"
        settings["filter_settings"]["critically_damped"] = {
            "response_hz": 6.5,
        }
        normalized = normalize_native_output_settings(settings)
        restored = native_output_preferences_from_section(
            native_output_section_from_settings(normalized)
        )
        self.assertEqual(restored["filter_type"], "critically_damped")
        self.assertEqual(
            restored["filter_settings"]["critically_damped"],
            {"response_hz": 6.5},
        )

    def test_accela_style_filter_suppresses_jitter_and_adapts_to_pose_error(self):
        accela = get_filter_definition("accela_style").create({
            "response_hz": 2.0,
            "acceleration_gain": 3.0,
            "acceleration_scale_deg": 2.0,
            "deadzone_deg": 0.1,
        })
        first = accela.process((0.0, 0.0, 0.0), 1.0)
        jitter = accela.process((0.05, -0.05, 0.08), 1.01)
        self.assertEqual(jitter, first)

        moved = accela.process((20.0, 20.0, 0.0), 1.02)
        self.assertGreater(moved[0], 0.0)
        self.assertLess(moved[0], 20.0)
        self.assertAlmostEqual(moved[0], moved[1])

        accela.reset()
        accela.process((179.0, 0.0, 0.0), 2.0)
        wrapped = accela.process((-179.0, 0.0, 0.0), 2.01)[0]
        self.assertGreater(wrapped, 179.0)
        self.assertLess(wrapped, 181.0)

    def test_accela_style_filter_settings_round_trip(self):
        settings = default_native_output_settings()
        settings["filter_type"] = "accela_style"
        settings["filter_settings"]["accela_style"] = {
            "response_hz": 3.5,
            "acceleration_gain": 2.0,
            "acceleration_scale_deg": 1.25,
            "deadzone_deg": 0.05,
        }
        normalized = normalize_native_output_settings(settings)
        restored = native_output_preferences_from_section(
            native_output_section_from_settings(normalized)
        )
        self.assertEqual(restored["filter_type"], "accela_style")
        self.assertEqual(
            restored["filter_settings"]["accela_style"],
            settings["filter_settings"]["accela_style"],
        )

    def test_legacy_filter_values_migrate_to_filter_settings_map(self):
        settings = native_output_preferences_from_section({
            "filter_enabled": "true",
            "filter_min_cutoff_hz": "1.7",
            "filter_beta": "0.42",
            "filter_derivative_cutoff_hz": "2.3",
        })
        self.assertEqual(settings["filter_type"], "one_euro")
        self.assertEqual(
            settings["filter_settings"]["one_euro"],
            {
                "min_cutoff_hz": 1.7,
                "beta": 0.42,
                "derivative_cutoff_hz": 2.3,
            },
        )

    def test_curve_spans_selected_input_limit_and_interpolates(self):
        curve = (
            (0, 0),
            (30, 120),
            (90, 180),
        )
        self.assertAlmostEqual(map_response_curve(curve, 30, 90), 120)
        mapped_midpoint = map_response_curve(curve, 15, 90)
        self.assertGreater(mapped_midpoint, 60)
        self.assertLess(mapped_midpoint, 120)
        self.assertEqual(map_response_curve(curve, 200, 90), 180)

    def test_curve_is_smooth_at_control_points(self):
        curve = ((0, 0), (30, 120), (90, 180))
        step = 0.001
        left_slope = (
            120.0 - map_response_curve(curve, 30.0 - step, 90)
        ) / step
        right_slope = (
            map_response_curve(curve, 30.0 + step, 90) - 120.0
        ) / step
        self.assertAlmostEqual(left_slope, right_slope, places=2)

    def test_smooth_curve_stays_within_control_point_ranges(self):
        curve = ((0, 0), (30, 15), (60, 170), (90, 180))
        values = [
            map_response_curve(curve, input_value, 90)
            for input_value in range(91)
        ]
        self.assertTrue(all(0.0 <= value <= 180.0 for value in values))
        self.assertTrue(all(
            previous <= current
            for previous, current in zip(values, values[1:])
        ))

    def test_invalid_curves_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            validate_curve(((0, 0), (45, 90), (45, 100), (90, 180)), 90)
        with self.assertRaisesRegex(ValueError, "between 0 and 180"):
            validate_curve(((0, 0), (45, 90), (90, 181)), 90)

    def test_curve_midpoint_can_move_on_magnitude_curve(self):
        points = validate_curve(((0, 0), (15, 90), (30, 180)), 30)
        self.assertEqual(points[1], (15.0, 90.0))
        self.assertEqual(map_response_curve(points, 0, 30), 0.0)
        self.assertEqual(map_response_curve(points, 15, 30), 90.0)

    def test_processor_applies_curves_offsets_and_zero_translation(self):
        settings = default_native_output_settings()
        settings["filter_enabled"] = False
        settings["curves"]["yaw"] = [
            [0, 0],
            [15, 90],
            [30, 120],
            [90, 180],
        ]
        settings["offsets"].update({
            "yaw_deg": 2.0,
            "pitch_deg": -3.0,
            "roll_deg": 4.0,
            "x_mm": 25.0,
            "y_mm": -15.0,
            "z_mm": 100.0,
        })
        processor = NativeOutputProcessor(settings)
        actual = processor.process((30, 5, -10), 1.0)
        expected = (122.0, 7.0, -16.0, 25.0, -15.0, 100.0)
        for value, target in zip(actual, expected):
            self.assertAlmostEqual(value, target)

    def test_response_curve_is_applied_symmetrically(self):
        settings = default_native_output_settings()
        settings["filter_enabled"] = False
        settings["input_limits"]["yaw"] = 30.0
        settings["curves"]["yaw"] = default_response_curve(30)
        processor = NativeOutputProcessor(settings)
        self.assertEqual(processor.process((30, 0, 0), 1.0)[0], 180.0)
        self.assertEqual(processor.process((-30, 0, 0), 1.1)[0], -180.0)

    def test_filter_runs_before_the_response_curve(self):
        settings = default_native_output_settings()
        settings["curves"]["yaw"] = [
            [0, 0],
            [15, 20],
            [30, 120],
            [90, 180],
        ]
        processor = NativeOutputProcessor(settings)

        class HalfAngleFilter:
            @staticmethod
            def process(angles, timestamp):
                return tuple(value / 2.0 for value in angles)

        processor._filter = HalfAngleFilter()
        self.assertEqual(processor.process((30, 0, 0), 1.0)[0], 20.0)

    def test_lowered_curve_endpoint_is_used_and_persisted(self):
        settings = default_native_output_settings()
        settings["curves"]["yaw"][-1][1] = 120.0
        processor = NativeOutputProcessor(settings)
        self.assertEqual(processor.process((90, 0, 0), 1.0)[0], 120.0)
        restored = native_output_preferences_from_section(
            native_output_section_from_settings(settings)
        )
        self.assertEqual(
            restored["curves"]["yaw"][-1],
            (90.0, 120.0),
        )

    def test_each_axis_has_an_independent_input_magnitude_limit(self):
        settings = default_native_output_settings()
        settings["filter_enabled"] = False
        settings["input_limits"] = {
            "yaw": 30.0,
            "pitch": 60.0,
            "roll": 75.0,
        }
        settings["curves"] = {
            axis: default_response_curve(limit)
            for axis, limit in settings["input_limits"].items()
        }
        processor = NativeOutputProcessor(settings)
        actual = processor.process((45, -90, 50), 1.0)[:3]
        for value, target in zip(actual, (180.0, -180.0, 120.0)):
            self.assertAlmostEqual(value, target)

    def test_final_rotation_is_limited_to_180_degrees_including_offsets(self):
        settings = default_native_output_settings()
        settings["filter_enabled"] = False
        settings["curves"]["yaw"] = default_response_curve(90)
        settings["offsets"]["yaw_deg"] = 30.0
        processor = NativeOutputProcessor(settings)
        self.assertEqual(processor.process((180, 0, 0), 1.0)[0], 180.0)
        settings["offsets"]["yaw_deg"] = -30.0
        processor.set_settings(settings)
        self.assertEqual(processor.process((-180, 0, 0), 1.0)[0], -180.0)

    def test_invalid_pose_and_settings_are_rejected(self):
        processor = NativeOutputProcessor()
        with self.assertRaisesRegex(ValueError, "finite"):
            processor.process((0, float("nan"), 0), 1.0)
        settings = default_native_output_settings()
        settings["filter_settings"]["one_euro"]["beta"] = float("inf")
        with self.assertRaisesRegex(ValueError, "finite"):
            normalize_native_output_settings(settings)

    def test_adaptive_filter_reduces_low_amplitude_jitter(self):
        settings = default_native_output_settings()
        processor = NativeOutputProcessor(settings)
        source = []
        filtered = []
        sample_rate = 120.0
        for index in range(240):
            timestamp = index / sample_rate
            jitter = 0.35 * math.sin(2 * math.pi * 13.0 * timestamp)
            source.append(2.0 * jitter)
            filtered.append(
                processor.process((jitter, 0, 0), timestamp)[0]
            )

        source_tail = source[60:]
        filtered_tail = filtered[60:]
        source_rms = math.sqrt(
            sum(value * value for value in source_tail) / len(source_tail)
        )
        filtered_rms = math.sqrt(
            sum(value * value for value in filtered_tail) / len(filtered_tail)
        )
        self.assertLess(filtered_rms, source_rms * 0.5)

    def test_adaptive_filter_tracks_a_fast_step_with_bounded_lag(self):
        processor = NativeOutputProcessor()
        values = []
        for index in range(73):
            timestamp = index / 120.0
            input_yaw = 0.0 if index < 60 else 60.0
            values.append(
                processor.process((input_yaw, 0, 0), timestamp)[0]
            )
        self.assertGreaterEqual(values[72], 114.0)
        self.assertLessEqual(values[72], 120.0)

    def test_filter_is_wrap_safe_for_yaw(self):
        processor = NativeOutputProcessor()
        first = processor.process((179, 0, 0), 1.0)[0]
        wrapped = processor.process((-179, 0, 0), 1.01)[0]
        self.assertLess(abs(wrapped - first), 2.0)

    def test_settings_update_resets_filter_history(self):
        processor = NativeOutputProcessor()
        processor.process((0, 0, 0), 1.0)
        processor.process((60, 0, 0), 1.01)
        processor.set_settings(default_native_output_settings())
        self.assertAlmostEqual(processor.process((20, 0, 0), 1.02)[0], 40.0)

    def test_settings_round_trip_and_invalid_values_fall_back(self):
        settings = default_native_output_settings()
        settings["input_limits"]["yaw"] = 30.0
        settings["input_limit_custom"]["yaw"] = True
        settings["curves"]["yaw"] = [
            [0, 0],
            [15, 90],
            [30, 180],
        ]
        settings["offsets"]["z_mm"] = 40.5
        serialized = native_output_section_from_settings(settings)
        loaded = native_output_preferences_from_section(serialized)
        self.assertEqual(loaded, normalize_native_output_settings(settings))

        warnings = []
        loaded = native_output_preferences_from_section(
            {
                "filter_enabled": "maybe",
                "filter_beta": "nan",
                "offset_z_mm": "999",
                "input_limit_yaw_deg": "0.5",
                "curves": "not-json",
            },
            warnings.append,
        )
        self.assertEqual(
            loaded,
            normalize_native_output_settings(default_native_output_settings()),
        )
        self.assertEqual(len(warnings), 5)

    def test_legacy_signed_curve_preferences_migrate_to_magnitude_curve(self):
        section = {
            "curves": (
                '{"yaw":[[-180,-180],[0,0],[30,120],[180,300]],'
                '"pitch":[[-180,-180],[0,0],[180,180]],'
                '"roll":[[-180,-180],[0,0],[180,180]]}'
            )
        }
        migrated = native_output_preferences_from_section(section)
        self.assertEqual(
            migrated["curves"]["yaw"],
            ((0.0, 0.0), (15.0, 120.0), (90.0, 180.0)),
        )
        processor = NativeOutputProcessor(migrated)
        self.assertEqual(processor.process((-15, 0, 0), 1.0)[0], -120.0)

    def test_screen_edge_limit_uses_the_smaller_opposite_capture(self):
        self.assertEqual(
            symmetric_limits_from_samples({
                "left": -35.0,
                "right": 30.5,
                "top": -28.0,
                "bottom": 27.4,
            }),
            (30.5, 27.4),
        )

    def test_previous_fixed_domain_curve_rescales_to_selected_input_limit(self):
        loaded = native_output_preferences_from_section({
            "input_limit_yaw_deg": "30",
            "curves": (
                '{"yaw":[[0,0],[90,90],[180,180]],'
                '"pitch":[[0,0],[90,90],[180,180]],'
                '"roll":[[0,0],[90,90],[180,180]]}'
            ),
        })
        self.assertEqual(
            loaded["curves"]["yaw"],
            ((0.0, 0.0), (15.0, 90.0), (30.0, 180.0)),
        )
        processor = NativeOutputProcessor(loaded)
        self.assertEqual(processor.process((30, 0, 0), 1.0)[0], 180.0)


class NativeOutputSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_registered_filter_adds_a_dropdown_option_and_its_controls(self):
        from queue import Queue

        class ExampleFilter:
            def reset(self):
                pass

            def process(self, angles, timestamp):
                return tuple(angles)

        definition = FilterDefinition(
            identifier="test_filter",
            label="Test Filter",
            parameters=(
                FilterParameter(
                    key="gain",
                    label="Test gain",
                    tooltip="Test filter parameter.",
                    minimum=0.0,
                    maximum=2.0,
                    step=0.1,
                    default=1.0,
                    decimals=1,
                ),
            ),
            factory=lambda settings: ExampleFilter(),
        )
        FILTER_REGISTRY[definition.identifier] = definition
        panel = None
        try:
            queue = Queue()
            panel = NativeOutputSettingsDialog(queue)
            self.addCleanup(panel.close)
            index = panel.filter_type_combo.findData(definition.identifier)
            self.assertGreaterEqual(index, 0)
            panel.filter_type_combo.setCurrentIndex(index)
            self.assertEqual(set(panel.filter_controls), {"gain"})
            self.assertEqual(panel.filter_value_labels["gain"].text(), "1.0")

            panel._save_timer.stop()
            panel._apply_pending_settings()
            command, settings = queue.get_nowait()
            self.assertEqual(command, "set_native_processing")
            self.assertEqual(settings["filter_type"], "test_filter")
            self.assertEqual(
                settings["filter_settings"]["test_filter"]["gain"],
                1.0,
            )
        finally:
            FILTER_REGISTRY.pop(definition.identifier, None)

    def test_filter_choice_and_per_filter_settings_round_trip(self):
        from queue import Queue

        panel = NativeOutputSettingsDialog(Queue())
        self.addCleanup(panel.close)
        self.assertEqual(panel.filter_type_combo.currentData(), "one_euro")
        panel.filter_controls["beta"].setValue(42)
        panel._save_timer.stop()
        settings = panel.get_settings()
        section = panel.get_preferences()
        restored = native_output_preferences_from_section(section)
        self.assertEqual(restored["filter_type"], "one_euro")
        self.assertEqual(
            restored["filter_settings"],
            settings["filter_settings"],
        )

    def test_ema_filter_can_be_selected_and_its_setting_persisted(self):
        from queue import Queue

        panel = NativeOutputSettingsDialog(Queue())
        self.addCleanup(panel.close)
        index = panel.filter_type_combo.findData("ema")
        self.assertGreaterEqual(index, 0)
        panel.filter_type_combo.setCurrentIndex(index)
        panel.filter_controls["cutoff_hz"].setValue(33)
        panel._save_timer.stop()

        settings = panel.get_settings()
        restored = native_output_preferences_from_section(
            panel.get_preferences()
        )
        self.assertEqual(settings["filter_type"], "ema")
        self.assertEqual(
            settings["filter_settings"]["ema"]["cutoff_hz"],
            3.4,
        )
        self.assertEqual(restored["filter_type"], "ema")
        self.assertEqual(
            restored["filter_settings"]["ema"]["cutoff_hz"],
            3.4,
        )

    def test_hysteresis_filter_can_be_selected_and_its_settings_persisted(self):
        from queue import Queue

        panel = NativeOutputSettingsDialog(Queue())
        self.addCleanup(panel.close)
        index = panel.filter_type_combo.findData("hysteresis")
        self.assertGreaterEqual(index, 0)
        panel.filter_type_combo.setCurrentIndex(index)
        panel.filter_controls["threshold_deg"].setValue(7)
        panel.filter_controls["release_percent"].setValue(11)
        panel._save_timer.stop()

        settings = panel.get_settings()
        restored = native_output_preferences_from_section(
            panel.get_preferences()
        )
        self.assertEqual(settings["filter_type"], "hysteresis")
        self.assertEqual(
            settings["filter_settings"]["hysteresis"],
            {"threshold_deg": 0.4, "release_percent": 65.0},
        )
        self.assertEqual(
            restored["filter_settings"]["hysteresis"],
            settings["filter_settings"]["hysteresis"],
        )

    def test_critically_damped_filter_can_be_selected_and_persisted(self):
        from queue import Queue

        panel = NativeOutputSettingsDialog(Queue())
        self.addCleanup(panel.close)
        index = panel.filter_type_combo.findData("critically_damped")
        self.assertGreaterEqual(index, 0)
        panel.filter_type_combo.setCurrentIndex(index)
        panel.filter_controls["response_hz"].setValue(12)
        panel._save_timer.stop()

        settings = panel.get_settings()
        restored = native_output_preferences_from_section(
            panel.get_preferences()
        )
        self.assertEqual(settings["filter_type"], "critically_damped")
        self.assertEqual(
            settings["filter_settings"]["critically_damped"],
            {"response_hz": 6.5},
        )
        self.assertEqual(
            restored["filter_settings"]["critically_damped"],
            settings["filter_settings"]["critically_damped"],
        )

    def test_accela_style_filter_can_be_selected_and_settings_persisted(self):
        from queue import Queue

        panel = NativeOutputSettingsDialog(Queue())
        self.addCleanup(panel.close)
        index = panel.filter_type_combo.findData("accela_style")
        self.assertGreaterEqual(index, 0)
        panel.filter_type_combo.setCurrentIndex(index)
        panel.filter_controls["response_hz"].setValue(6)
        panel.filter_controls["acceleration_gain"].setValue(20)
        panel.filter_controls["acceleration_scale_deg"].setValue(4)
        panel.filter_controls["deadzone_deg"].setValue(5)
        panel._save_timer.stop()

        settings = panel.get_settings()
        restored = native_output_preferences_from_section(
            panel.get_preferences()
        )
        expected = {
            "response_hz": 3.5,
            "acceleration_gain": 2.0,
            "acceleration_scale_deg": 1.25,
            "deadzone_deg": 0.05,
        }
        self.assertEqual(settings["filter_type"], "accela_style")
        self.assertEqual(
            settings["filter_settings"]["accela_style"],
            expected,
        )
        self.assertEqual(
            restored["filter_settings"]["accela_style"],
            expected,
        )

    def test_gui_startup_loads_all_preferences_with_custom_curve_limits(self):
        from queue import Queue

        from src.managers.preferences_manager import PreferencesManager
        from src.workers.gui_wrk import TabbedGUIWorker

        with tempfile.TemporaryDirectory() as config_dir:
            manager = PreferencesManager(config_dir=config_dir)
            native_settings = default_native_output_settings()
            custom_limit = 89.10000000000005
            native_settings["input_limits"]["yaw"] = custom_limit
            native_settings["input_limit_custom"]["yaw"] = True
            native_settings["curves"]["yaw"] = [
                list(point)
                for point in default_response_curve(custom_limit)
            ]
            preferences = {
                "serial": {"com_port": "COM7", "baud_rate": "115200"},
                "network": {
                    "udp_ip": "127.0.0.1",
                    "udp_port": "4243",
                    "output_protocol": "udp",
                },
                "orientation": {"alpha_pitch": "0.98"},
                "native_output": native_output_section_from_settings(
                    native_settings
                ),
                "gui": {"selected_tab": "0", "theme": "dark"},
            }
            self.assertTrue(manager.save(preferences))

            output_dialog = NativeOutputSettingsDialog(Queue())
            self.addCleanup(output_dialog.close)
            gui = SimpleNamespace(
                preferences_manager=manager,
                log_queue=Queue(),
                connection_panel=Mock(),
                output_panel=Mock(),
                native_output_dialog=output_dialog,
                orientation_panel=Mock(),
                preferences_panel=SimpleNamespace(
                    load_preferences=Mock()
                ),
                _apply_theme=Mock(),
            )
            TabbedGUIWorker.load_preferences(gui)

            gui.connection_panel.set_prefs.assert_called_once_with(
                preferences["serial"]
            )
            gui.output_panel.set_prefs.assert_called_once_with(
                preferences["network"]
            )
            gui.orientation_panel.set_prefs.assert_called_once_with(
                preferences["orientation"]
            )
            gui.preferences_panel.load_preferences.assert_called_once_with()
            gui._apply_theme.assert_called_once_with("dark")
            self.assertEqual(
                output_dialog.get_settings()["input_limits"]["yaw"],
                89.1,
            )
            self.assertEqual(
                output_dialog.curve_widgets["yaw"].points[-1][0],
                89.1,
            )

    def test_native_settings_button_visibility_tracks_protocol(self):
        from queue import Queue

        from src.config.config import OUTPUT_PROTOCOL_FREETRACK, OUTPUT_PROTOCOL_UDP
        from src.workers.gui_qt.panels.output_panel import OutputPanelQt

        panel = OutputPanelQt(None, Queue(), lambda message: None)
        self.addCleanup(panel.close)
        self.assertTrue(panel.native_output_settings_btn.isHidden())
        panel.output_protocol_combo.setCurrentIndex(
            panel.output_protocol_combo.findData(OUTPUT_PROTOCOL_FREETRACK)
        )
        self.assertFalse(panel.native_output_settings_btn.isHidden())
        self.assertIsInstance(panel.native_output_dialog, QDialog)
        self.assertTrue(panel.native_output_dialog.isWindow())
        panel.native_output_settings_btn.click()
        self.assertTrue(panel.native_output_dialog.isVisible())
        self.assertEqual(panel.native_output_dialog.width(), 720)
        self.assertEqual(panel.native_output_dialog.minimumSize().width(), 720)
        self.assertEqual(panel.native_output_dialog.minimumSize().height(), 830)
        panel.native_output_dialog.close()
        panel.output_protocol_combo.setCurrentIndex(
            panel.output_protocol_combo.findData(OUTPUT_PROTOCOL_UDP)
        )
        self.assertTrue(panel.native_output_settings_btn.isHidden())

    def test_connection_panel_contains_only_serial_settings(self):
        from queue import Queue

        from src.workers.gui_qt.panels.base_panel import BasePanelQt
        from src.workers.gui_qt.panels.connection_panel import ConnectionPanelQt
        from src.workers.gui_qt.panels.output_panel import OutputPanelQt

        messages = []
        serial_panel = ConnectionPanelQt(None, Queue(), messages.append)
        output_panel = OutputPanelQt(None, Queue(), messages.append)
        self.addCleanup(serial_panel.close)
        self.addCleanup(output_panel.close)

        self.assertIsInstance(serial_panel, BasePanelQt)
        self.assertEqual(serial_panel.title(), "Serial Settings")
        self.assertTrue(hasattr(serial_panel, "port_combo"))
        self.assertTrue(hasattr(serial_panel, "baud_combo"))
        self.assertFalse(hasattr(serial_panel, "output_protocol_combo"))
        self.assertIsInstance(output_panel, BasePanelQt)
        self.assertEqual(output_panel.title(), "Output")
        self.assertEqual(
            set(serial_panel.get_prefs()),
            {"com_port", "baud_rate"},
        )
        self.assertEqual(
            set(output_panel.get_prefs()),
            {"udp_ip", "udp_port", "output_protocol"},
        )

    def test_start_controls_are_bottom_aligned_with_orientation_panel_margins(self):
        from queue import Queue

        from src.workers.gui_qt.panels.base_panel import (
            CONTENT_MARGINS,
            DEFAULT_SPACING,
        )
        from src.workers.gui_qt.panels.connection_panel import ConnectionPanelQt
        from src.workers.gui_qt.panels.output_panel import OutputPanelQt
        from src.workers.gui_qt.panels.orientation_panel import OrientationPanelQt

        serial_panel = ConnectionPanelQt(None, Queue(), lambda message: None)
        output_panel = OutputPanelQt(None, Queue(), lambda message: None)
        orientation_panel = OrientationPanelQt(
            None,
            Queue(),
            lambda message: None,
        )
        self.addCleanup(serial_panel.close)
        self.addCleanup(output_panel.close)
        self.addCleanup(orientation_panel.close)

        self.assertIs(
            serial_panel.content_layout.itemAt(
                serial_panel.content_layout.count() - 1
            ).widget(),
            serial_panel.toggle_button,
        )
        self.assertIs(
            output_panel.content_layout.itemAt(
                output_panel.content_layout.count() - 1
            ).widget(),
            output_panel.output_toggle_btn,
        )
        for panel in (serial_panel, output_panel, orientation_panel):
            self.assertEqual(panel.layout().contentsMargins().left(), CONTENT_MARGINS[0])
            self.assertEqual(panel.layout().contentsMargins().top(), CONTENT_MARGINS[1])
            self.assertEqual(panel.layout().contentsMargins().right(), CONTENT_MARGINS[2])
            self.assertEqual(panel.layout().contentsMargins().bottom(), CONTENT_MARGINS[3])
            self.assertEqual(panel.layout().spacing(), DEFAULT_SPACING)
        for panel in (serial_panel, output_panel):
            inner_layout = panel.content_layout
            self.assertEqual(
                tuple(
                    getattr(inner_layout.contentsMargins(), side)()
                    for side in ("left", "top", "right", "bottom")
                ),
                CONTENT_MARGINS,
            )
            self.assertEqual(inner_layout.spacing(), DEFAULT_SPACING)

    def test_serial_and_output_panels_keep_one_height_across_protocols(self):
        from queue import Queue

        from src.config.config import OUTPUT_PROTOCOL_FREETRACK, OUTPUT_PROTOCOL_UDP
        from src.workers.gui_qt.panels.connection_panel import ConnectionPanelQt
        from src.workers.gui_qt.panels.output_panel import OutputPanelQt

        messages = []
        serial_panel = ConnectionPanelQt(None, Queue(), messages.append)
        output_panel = OutputPanelQt(None, Queue(), messages.append)
        self.addCleanup(serial_panel.close)
        self.addCleanup(output_panel.close)

        fixed_height = max(
            serial_panel.sizeHint().height(),
            serial_panel.minimumSizeHint().height(),
            output_panel.stable_height_hint(),
            output_panel.sizeHint().height(),
            output_panel.minimumSizeHint().height(),
        )
        serial_panel.setFixedHeight(fixed_height)
        output_panel.setFixedHeight(fixed_height)

        for protocol in (OUTPUT_PROTOCOL_UDP, OUTPUT_PROTOCOL_FREETRACK):
            output_panel.output_protocol_combo.setCurrentIndex(
                output_panel.output_protocol_combo.findData(protocol)
            )
            self.assertEqual(serial_panel.height(), fixed_height)
            self.assertEqual(output_panel.height(), fixed_height)
            self.assertLessEqual(
                output_panel.sizeHint().height(),
                fixed_height,
            )

        self.assertGreaterEqual(
            fixed_height,
            output_panel.stable_height_hint(),
        )

    def test_settings_panel_applies_live_settings_and_preferences(self):
        from queue import Queue

        control_queue = Queue()
        panel = NativeOutputSettingsDialog(control_queue)
        self.addCleanup(panel.close)
        panel.set_preferences({
            "filter_enabled": "false",
            "offset_x_mm": "12.5",
            "input_limit_yaw_deg": "30",
        })
        command, settings = control_queue.get_nowait()
        self.assertEqual(command, "set_native_processing")
        self.assertFalse(settings["filter_enabled"])
        self.assertEqual(settings["offsets"]["x_mm"], 12.5)
        self.assertEqual(settings["input_limits"]["yaw"], 30.0)
        self.assertEqual(panel.curve_widgets["yaw"].input_limit_deg, 30.0)
        self.assertEqual(panel.curve_widgets["yaw"].points[-1], (30.0, 180.0))
        saved = panel.get_preferences()
        self.assertEqual(saved["filter_enabled"], "false")
        self.assertEqual(saved["offset_x_mm"], "12.5")
        self.assertEqual(saved["input_limit_yaw_deg"], "30")

    def test_custom_input_magnitudes_are_editable_and_persisted(self):
        from queue import Queue

        control_queue = Queue()
        panel = NativeOutputSettingsDialog(control_queue)
        self.addCleanup(panel.close)
        panel.set_preferences({
            "input_limit_yaw_deg": "37.5",
            "input_limit_custom_yaw": "true",
        })
        command, settings = control_queue.get_nowait()
        self.assertEqual(command, "set_native_processing")
        self.assertTrue(settings["input_limit_custom"]["yaw"])
        self.assertAlmostEqual(settings["input_limits"]["yaw"], 37.5)
        self.assertTrue(panel.input_limit_custom_spins["yaw"].isEnabled())
        self.assertFalse(panel.input_limit_controls["yaw"].isEnabled())
        saved = panel.get_preferences()
        self.assertEqual(saved["input_limit_custom_yaw"], "true")
        self.assertEqual(saved["input_limit_yaw_deg"], "37.5")

    def test_custom_limit_precision_matches_the_displayed_curve_domain(self):
        from queue import Queue

        settings = default_native_output_settings()
        limit = 89.10000000000005
        settings["input_limits"]["yaw"] = limit
        settings["input_limit_custom"]["yaw"] = True
        settings["curves"]["yaw"] = default_response_curve(limit)
        section = native_output_section_from_settings(settings)
        panel = NativeOutputSettingsDialog(Queue())
        self.addCleanup(panel.close)
        panel.set_preferences(section)
        saved = panel.get_preferences()
        saved_curves = json.loads(saved["curves"])
        self.assertEqual(saved["input_limit_yaw_deg"], "89.1")
        self.assertEqual(saved_curves["yaw"][-1][0], 89.1)

    def test_input_limit_dropdown_updates_only_its_curve(self):
        from queue import Queue

        control_queue = Queue()
        panel = NativeOutputSettingsDialog(control_queue)
        self.addCleanup(panel.close)
        yaw_combo = panel.input_limit_controls["yaw"]
        yaw_combo.setCurrentIndex(yaw_combo.findData(45.0))
        self.assertEqual(panel.curve_widgets["yaw"].input_limit_deg, 45.0)
        self.assertEqual(panel.curve_widgets["yaw"].points[-1], (45.0, 180.0))
        self.assertEqual(panel.curve_widgets["pitch"].input_limit_deg, 90.0)

        panel._apply_pending_settings()
        command, settings = control_queue.get_nowait()
        self.assertEqual(command, "set_native_processing")
        self.assertEqual(settings["input_limits"]["yaw"], 45.0)
        self.assertEqual(settings["input_limits"]["pitch"], 90.0)

    def test_automatic_calibration_updates_custom_yaw_and_pitch_limits(self):
        panel = NativeOutputSettingsDialog()
        self.addCleanup(panel.close)
        panel.set_current_pose((0.0, 0.0, 0.0))
        panel._apply_calibrated_limits(30.5, 27.4)
        settings = panel.get_settings()
        self.assertEqual(settings["input_limit_custom"]["yaw"], True)
        self.assertEqual(settings["input_limit_custom"]["pitch"], True)
        self.assertAlmostEqual(settings["input_limits"]["yaw"], 30.5)
        self.assertAlmostEqual(settings["input_limits"]["pitch"], 27.4)
        self.assertFalse(settings["input_limit_custom"]["roll"])

    def test_screen_edge_calibration_completes_and_closes_popouts(self):
        current_pose = [-35.0, 0.0, 0.0]
        results = []
        calibration = InputMagnitudeCalibration(
            lambda: tuple(current_pose),
            lambda yaw, pitch: results.append((yaw, pitch)),
        )
        self.addCleanup(calibration.cancel)
        calibration.start()
        self.assertEqual(calibration._intro.width(), calibration._intro.height())
        self.assertEqual(
            calibration._intro.container.objectName(),
            "visualizationPopup",
        )
        self.assertIn(
            "border-radius",
            calibration._intro.container.styleSheet(),
        )
        calibration._intro.start_button.click()
        self.assertIsNotNone(calibration._target)
        self.assertEqual(calibration._target.width(), calibration._target.height())
        self.assertEqual(calibration._target.container.objectName(), "visualizationPopup")
        self.assertIsInstance(calibration._prompt.container, QFrame)
        self.assertEqual(calibration._prompt.width(), calibration._prompt.height())
        self.assertIn("border-radius", calibration._prompt.container.styleSheet())
        self.assertEqual(calibration._prompt.windowTitle(), "Left edge calibration")
        calibration._prompt.set_button.click()
        self.assertEqual(calibration._prompt.windowTitle(), "Right edge calibration")

        for pose, expected_next_title in (
            ((30.5, 0.0, 0.0), "Upper edge calibration"),
            ((0.0, -28.0, 0.0), "Lower edge calibration"),
        ):
            current_pose[:] = pose
            calibration._prompt.set_button.click()
            self.assertEqual(calibration._prompt.windowTitle(), expected_next_title)

        current_pose[:] = (0.0, 27.4, 0.0)
        calibration._prompt.set_button.click()
        self.assertEqual(results, [(30.5, 27.4)])
        self.assertFalse(calibration.active)
        self.assertIsNone(calibration._target)
        self.assertIsNone(calibration._prompt)

    def test_filter_sliders_use_configured_ranges_and_values(self):
        from queue import Queue

        from src.config.config import (
            FILTER_BETA_RANGE,
            FILTER_BETA_STEP,
            FILTER_MIN_CUTOFF_HZ_RANGE,
            FILTER_MIN_CUTOFF_HZ_STEP,
        )

        control_queue = Queue()
        panel = NativeOutputSettingsDialog(control_queue)
        self.addCleanup(panel.close)
        beta_slider = panel.filter_controls["beta"]
        minimum_slider = panel.filter_controls["min_cutoff_hz"]
        self.assertIsInstance(beta_slider, QSlider)
        self.assertEqual(
            beta_slider.maximum(),
            round((FILTER_BETA_RANGE[1] - FILTER_BETA_RANGE[0]) / FILTER_BETA_STEP),
        )

        beta_slider.setValue(round(0.55 / FILTER_BETA_STEP))
        minimum_position = round(
            (1.5 - FILTER_MIN_CUTOFF_HZ_RANGE[0])
            / FILTER_MIN_CUTOFF_HZ_STEP
        )
        minimum_slider.setValue(minimum_position)
        self.assertEqual(panel.filter_value_labels["beta"].text(), "0.55")
        self.assertEqual(
            panel.filter_value_labels["min_cutoff_hz"].text(),
            "1.5 Hz",
        )
        self.assertIn("One Euro beta", beta_slider.toolTip())

        panel._save_timer.stop()
        panel._apply_pending_settings()
        command, settings = control_queue.get_nowait()
        self.assertEqual(command, "set_native_processing")
        self.assertAlmostEqual(
            settings["filter_settings"]["one_euro"]["beta"],
            0.55,
        )
        self.assertAlmostEqual(
            settings["filter_settings"]["one_euro"]["min_cutoff_hz"],
            1.5,
        )

    def test_curve_paint_uses_integer_draw_text_coordinates(self):
        class Painter:
            def __getattr__(self, name):
                if name == "drawText":
                    return self.draw_text
                return lambda *args: None

            @staticmethod
            def draw_text(*args):
                if not isinstance(args[0], int):
                    raise TypeError("drawText coordinates must be integers")

        widget = ResponseCurveWidget("yaw")
        painter = Painter()
        with patch(
            "src.workers.gui_qt.panels.native_output_settings_dialog.QPainter",
            return_value=painter,
        ):
            widget.paintEvent(None)

    def test_curve_midpoint_drag_changes_input_and_output(self):
        widget = ResponseCurveWidget("yaw")
        widget.resize(500, 300)
        x, y = widget._to_position(25.0, 45.0)
        widget._set_point(1, QPoint(round(x), round(y)))
        self.assertGreater(widget.points[1][0], 0.0)
        self.assertGreater(widget.points[1][1], 0.0)

    def test_curve_endpoint_can_move_vertically(self):
        widget = ResponseCurveWidget("yaw")
        widget.resize(500, 300)
        x, y = widget._to_position(widget.input_limit_deg, 120.0)
        widget._set_point(
            len(widget.points) - 1,
            QPoint(round(x), round(y)),
        )
        self.assertEqual(widget.points[-1][0], widget.input_limit_deg)
        self.assertAlmostEqual(widget.points[-1][1], 120.0, delta=2.0)
        self.assertAlmostEqual(
            map_response_curve(
                widget.points,
                widget.input_limit_deg,
                widget.input_limit_deg,
            ),
            widget.points[-1][1],
        )

    def test_hovering_over_a_point_shows_its_current_values(self):
        widget = ResponseCurveWidget("yaw")
        widget.resize(500, 300)
        x, y = widget._to_position(45.0, 90.0)
        event = QMouseEvent(
            QEvent.MouseMove,
            QPointF(x, y),
            Qt.NoButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
        with patch(
            "src.workers.gui_qt.panels.native_output_settings_dialog.QToolTip.showText"
        ) as show_tooltip:
            widget.mouseMoveEvent(event)
        self.assertEqual(
            show_tooltip.call_args.args[1],
            "Input magnitude: 45.0°\nOutput magnitude: 90.0°",
        )

    def test_settings_popout_follows_light_and_dark_themes(self):
        app = QApplication.instance()
        previous_stylesheet = app.styleSheet()
        theme_dir = Path(__file__).resolve().parents[1] / "themes"
        panel = NativeOutputSettingsDialog()
        try:
            for theme, dialog_color, curve_color, slider_color in (
                ("dark", "#3c3c3c", "#2b2b2b", "#2b2b2b"),
                ("light", "#ffffff", "#ffffff", "#e1e1e1"),
            ):
                app.setStyleSheet(
                    (theme_dir / f"{theme}.qss").read_text(encoding="utf-8")
                )
                panel.open_window()
                app.processEvents()
                dialog = panel
                curve = panel.curve_widgets["yaw"]
                control = panel.filter_controls["beta"]
                for widget in (dialog, curve, control):
                    widget.ensurePolished()
                self.assertIsInstance(control, QSlider)
                self.assertIn("QSlider::groove:horizontal", app.styleSheet())
                self.assertIn(slider_color, app.styleSheet())
                self.assertEqual(
                    dialog.palette().color(QPalette.Window).name(),
                    dialog_color,
                )
                self.assertEqual(
                    curve.palette().color(QPalette.Window).name(),
                    curve_color,
                )
        finally:
            panel.close()
            app.setStyleSheet(previous_stylesheet)


if __name__ == "__main__":
    unittest.main()
