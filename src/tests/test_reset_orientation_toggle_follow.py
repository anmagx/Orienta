"""Tests for immediate and release-triggered Reset Orientation behavior."""

import os
import unittest
from queue import Queue
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox

from src.workers.fusion_wrk import QuaternionComplementaryFilter, run_worker
from src.workers.gui_qt.panels.orientation_panel import OrientationPanelQt
from src.workers.gui_qt.panels.preferences_panel import PreferencesPanel


class ResetOrientationToggleFollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.control_queue = Queue()
        self.input_response_queue = Queue()
        self.panel = OrientationPanelQt(control_queue=self.control_queue)
        self.panel.input_response_queue = self.input_response_queue
        self.panel.update_processing_status(True)

    def tearDown(self):
        self.panel.close()

    def test_default_button_and_shortcut_recenter_on_release(self):
        self.assertFalse(self.panel.reset_orientation_instantaneous)

        self.panel.reset_button.pressed.emit()
        self.assertTrue(self.control_queue.empty())
        self.panel.reset_button.released.emit()
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recenter_orientation_to_current',),
        )

        self.input_response_queue.put(
            ('shortcut_pressed', 'f10', 'reset_orientation')
        )
        self.panel._process_input_responses()
        self.assertTrue(self.control_queue.empty())

        self.input_response_queue.put(
            ('shortcut_released', 'f10', 'reset_orientation')
        )
        self.panel._process_input_responses()
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recenter_orientation_to_current',),
        )

        self.assertTrue(self.control_queue.empty())

    def test_button_release_recenter_command_is_sent_only_after_release(self):
        self.panel.reset_button.pressed.emit()
        self.assertTrue(self.control_queue.empty())
        self.assertEqual(self.panel.reset_button.property('status'), 'warning')
        self.assertTrue(self.panel.visualization_widget.reset_follow_active)

        self.panel.reset_button.released.emit()
        self.assertEqual(self.panel.reset_button.property('status'), '')
        self.assertFalse(self.panel.visualization_widget.reset_follow_active)
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recenter_orientation_to_current',),
        )

    def test_button_click_sends_one_recenter_command_by_default(self):
        self.panel.reset_button.click()

        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recenter_orientation_to_current',),
        )
        self.assertTrue(self.control_queue.empty())

    def test_instantaneous_preference_recenters_on_click_and_key_press(self):
        self.panel.set_reset_orientation_instantaneous(True)

        self.panel.reset_button.click()
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recenter_orientation_to_current',),
        )

        self.input_response_queue.put(
            ('shortcut_pressed', 'f10', 'reset_orientation')
        )
        self.panel._process_input_responses()
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recenter_orientation_to_current',),
        )
        self.input_response_queue.put(
            ('shortcut_released', 'f10', 'reset_orientation')
        )
        self.assertEqual(self.panel.reset_button.property('status'), 'warning')
        self.panel._process_input_responses()
        self.assertTrue(self.control_queue.empty())
        self.assertEqual(self.panel.reset_button.property('status'), '')

    def test_shortcut_release_recenter_command_is_sent_only_after_release(self):
        self.input_response_queue.put(
            ('shortcut_pressed', 'f10', 'reset_orientation')
        )
        self.panel._process_input_responses()
        self.assertTrue(self.control_queue.empty())
        self.assertTrue(self.panel.visualization_widget.reset_follow_active)

        self.input_response_queue.put(
            ('shortcut_released', 'f10', 'reset_orientation')
        )
        self.panel._process_input_responses()
        self.assertFalse(self.panel.visualization_widget.reset_follow_active)
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recenter_orientation_to_current',),
        )

    def test_multiple_held_inputs_recenter_after_the_last_release(self):
        self.panel.reset_button.pressed.emit()
        self.input_response_queue.put(
            ('shortcut_pressed', 'f10', 'reset_orientation')
        )
        self.panel._process_input_responses()
        self.assertEqual(self.panel.reset_button.property('status'), 'warning')

        self.panel.reset_button.released.emit()
        self.assertEqual(self.panel.reset_button.property('status'), 'warning')
        self.assertTrue(self.control_queue.empty())

        self.input_response_queue.put(
            ('shortcut_released', 'f10', 'reset_orientation')
        )
        self.panel._process_input_responses()
        self.assertEqual(self.panel.reset_button.property('status'), '')
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recenter_orientation_to_current',),
        )

    def test_preferences_persist_and_apply_instantaneous_recenter(self):
        preferences = PreferencesPanel(preferences_manager=Mock())
        self.addCleanup(preferences.close)
        preferences.orientation_panel = self.panel

        self.assertFalse(
            preferences.reset_orientation_instantaneous_checkbox.isChecked()
        )
        self.assertIs(
            preferences.disengage_toggle_checkbox.parentWidget(),
            preferences.orientation_hold_toggle_checkbox.parentWidget(),
        )
        self.assertIs(
            preferences.orientation_hold_toggle_checkbox.parentWidget(),
            preferences.reset_orientation_instantaneous_checkbox.parentWidget(),
        )
        self.assertIn(
            "Reset Orientation",
            preferences.reset_orientation_instantaneous_checkbox.text(),
        )
        self.assertIn(
            "Hold Orientation",
            preferences.orientation_hold_toggle_checkbox.text(),
        )
        self.assertIn(
            "Center Assist Disengage",
            preferences.disengage_toggle_checkbox.text(),
        )
        preferences.reset_orientation_instantaneous_checkbox.setChecked(True)

        self.assertTrue(self.panel.reset_orientation_instantaneous)
        self.assertTrue(
            preferences.get_tuning_preferences()[
                'reset_orientation_instantaneous'
            ]
        )

        preferences._load_shortcut_settings(
            {'reset_orientation_instantaneous': 'false'}
        )
        self.assertFalse(self.panel.reset_orientation_instantaneous)
        self.assertFalse(
            preferences.reset_orientation_instantaneous_checkbox.isChecked()
        )

    def test_fusion_worker_applies_current_pose_recenter_command(self):
        filter_instance = QuaternionComplementaryFilter()
        filter_instance.q = filter_instance._quat_from_euler(
            37.0, 14.0, -12.0
        )
        serial_queue = Queue()
        control_queue = Queue()
        control_queue.put(('recenter_orientation_to_current',))

        class StopAfterOneControlCycle:
            def __init__(self):
                self.checks = 0

            def is_set(self):
                self.checks += 1
                return self.checks > 4

        stop_event = StopAfterOneControlCycle()

        def get_control_or_empty(queue, timeout=0.0, default=None):
            if queue is control_queue:
                try:
                    return queue.get_nowait()
                except Exception:
                    return default
            return default

        with patch(
            'src.workers.fusion_wrk.QuaternionComplementaryFilter',
            return_value=filter_instance,
        ), patch(
            'src.workers.fusion_wrk.safe_queue_get',
            side_effect=get_control_or_empty,
        ), patch(
            'src.workers.fusion_wrk.time.sleep'
        ), patch(
            'src.util.timing_utils.enable_high_res_timer'
        ), patch(
            'src.util.timing_utils.disable_high_res_timer'
        ), patch(
            'src.util.timing_utils.raise_process_priority'
        ):
            run_worker(
                serial_queue,
                Queue(),
                Queue(),
                control_queue,
                Queue(),
                stop_event,
                Queue(),
            )

        for actual, expected in zip(
            filter_instance.output_origin, (37.0, 14.0, -12.0)
        ):
            self.assertAlmostEqual(actual, expected)

    def test_recenter_resets_native_output_filter_history(self):
        filter_instance = QuaternionComplementaryFilter()
        control_queue = Queue()
        control_queue.put(('recenter_orientation_to_current',))
        output_queue = Queue()

        class StopAfterOneSample:
            def __init__(self):
                self.checks = 0

            def is_set(self):
                self.checks += 1
                return self.checks > 4

        def get_sample(queue, timeout=0.0, default=None):
            if queue is control_queue:
                try:
                    command = queue.get_nowait()
                except Exception:
                    return default
                if command[0] == 'recenter_orientation_to_current':
                    recentered[0] = True
                return command
            if timeout == 0.05 and recentered[0] and not sample_sent[0]:
                sample_sent[0] = True
                return '0.004,0,0,1,0,0,0'
            return default

        recentered = [False]
        sample_sent = [False]
        with patch(
            'src.workers.fusion_wrk.QuaternionComplementaryFilter',
            return_value=filter_instance,
        ), patch(
            'src.workers.fusion_wrk.safe_queue_get',
            side_effect=get_sample,
        ), patch(
            'src.workers.fusion_wrk.time.sleep'
        ), patch(
            'src.util.timing_utils.enable_high_res_timer'
        ), patch(
            'src.util.timing_utils.disable_high_res_timer'
        ), patch(
            'src.util.timing_utils.raise_process_priority'
        ):
            run_worker(
                Queue(),
                output_queue,
                Queue(),
                control_queue,
                Queue(),
                StopAfterOneSample(),
                Queue(),
            )

        batch = output_queue.get_nowait()
        self.assertTrue(batch[0].reset_filter)

    def test_calibration_control_requests_sensor_biases(self):
        with patch(
            'src.workers.gui_qt.panels.orientation_panel.QMessageBox.question',
            return_value=QMessageBox.Yes,
        ):
            self.panel._on_recalibrate()

        self.assertEqual(
            self.control_queue.get_nowait(),
            ('recalibrate_sensor_bias',),
        )
        self.assertEqual(self.panel.recal_button.text(), "Calibrate Sensor Biases")
        self.assertIn("+Z axis facing up", self.panel.recal_button.toolTip())

    def test_sensor_calibration_confirmation_can_be_cancelled(self):
        with patch(
            'src.workers.gui_qt.panels.orientation_panel.QMessageBox.question',
            return_value=QMessageBox.No,
        ):
            self.panel._on_recalibrate()

        self.assertTrue(self.control_queue.empty())

    def test_center_assist_disengage_keeps_shortcut_visible_and_uses_hold_style(self):
        self.panel._set_disengage_shortcut("f9", "F9")
        self.panel._disengage_on()

        button = self.panel.disengage_btn
        self.assertEqual(button.text(), "Center Assist Disengaged")
        self.assertEqual(button._sub, "F9")
        self.assertEqual(button.property("status"), "warning")
        self.assertIn("Release this button", button.toolTip())
        self.assertFalse(button.text().startswith("🔴"))

        self.panel._disengage_off()

        self.assertEqual(button.text(), "Center Assist Disengage")
        self.assertEqual(button._sub, "F9")
        self.assertEqual(button.property("status"), "")


if __name__ == "__main__":
    unittest.main()
