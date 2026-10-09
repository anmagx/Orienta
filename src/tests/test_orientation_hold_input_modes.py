"""Regression tests for momentary and toggle Hold Orientation controls."""

import os
import unittest
from queue import Queue
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from src.workers.gui_qt.panels.orientation_panel import OrientationPanelQt
from src.workers.gui_qt.panels.preferences_panel import PreferencesPanel


class OrientationHoldInputModeTests(unittest.TestCase):
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

    def test_button_holds_only_while_pressed_by_default(self):
        self.assertFalse(self.panel.orientation_hold_toggle_mode)
        self.assertFalse(self.panel.orientation_hold_btn.isCheckable())

        self.panel.orientation_hold_btn.pressed.emit()
        self.assertTrue(self.panel.orientation_held)
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('set_orientation_hold', True),
        )

        self.panel.orientation_hold_btn.released.emit()
        self.assertFalse(self.panel.orientation_held)
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('set_orientation_hold', False),
        )

    def test_shortcut_holds_until_key_release_by_default(self):
        self.input_response_queue.put(
            ('shortcut_pressed', 'f9', 'hold_orientation')
        )
        self.panel._process_input_responses()
        self.assertTrue(self.panel.orientation_held)
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('set_orientation_hold', True),
        )

        self.input_response_queue.put(
            ('shortcut_released', 'f9', 'hold_orientation')
        )
        self.panel._process_input_responses()
        self.assertFalse(self.panel.orientation_held)
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('set_orientation_hold', False),
        )

    def test_toggle_mode_keeps_button_and_shortcut_toggled_after_release(self):
        self.assertTrue(self.panel.set_orientation_hold_toggle_mode(True))
        self.assertTrue(self.panel.orientation_hold_btn.isCheckable())

        self.panel.orientation_hold_btn.click()
        self.assertTrue(self.panel.orientation_held)
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('set_orientation_hold', True),
        )
        self.panel.orientation_hold_btn.click()
        self.assertFalse(self.panel.orientation_held)
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('set_orientation_hold', False),
        )

        self.input_response_queue.put(
            ('shortcut_pressed', 'f9', 'hold_orientation')
        )
        self.input_response_queue.put(
            ('shortcut_released', 'f9', 'hold_orientation')
        )
        self.panel._process_input_responses()
        self.assertTrue(self.panel.orientation_held)
        self.assertTrue(self.panel.orientation_hold_btn.isChecked())
        self.assertEqual(
            self.control_queue.get_nowait(),
            ('set_orientation_hold', True),
        )

    def test_preferences_checkbox_persists_and_applies_toggle_mode(self):
        preferences = PreferencesPanel(preferences_manager=Mock())
        self.addCleanup(preferences.close)
        preferences.orientation_panel = self.panel

        preferences.orientation_hold_toggle_checkbox.setChecked(True)

        self.assertTrue(self.panel.orientation_hold_toggle_mode)
        self.assertTrue(preferences.get_tuning_preferences()['orientation_hold_toggle_mode'])

        preferences._load_shortcut_settings(
            {'orientation_hold_toggle_mode': 'false'}
        )
        self.assertFalse(self.panel.orientation_hold_toggle_mode)
        self.assertFalse(preferences.orientation_hold_toggle_checkbox.isChecked())


if __name__ == "__main__":
    unittest.main()
