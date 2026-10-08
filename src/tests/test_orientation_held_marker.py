"""Regression tests for the live and held orientation visualization markers."""

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication

from src.workers.gui_qt.panels.visualization_widget import OrientationVisualizationWidget


class OrientationHeldMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = OrientationVisualizationWidget(range_degrees=30.0)
        self.widget.resize(240, 240)
        self.widget.show()
        self.app.processEvents()

    def tearDown(self):
        self.widget.close()

    def test_live_updates_do_not_move_the_held_marker(self):
        self.widget.update_orientation(4.0, -6.0, 2.0)
        self.widget.set_held_orientation(-15.0, 12.0, -8.0)

        self.widget.update_orientation(-9.0, 20.0, 11.0)

        self.assertEqual(self.widget.held_orientation, (-15.0, 12.0, -8.0))
        self.assertEqual(
            (self.widget.yaw, self.widget.pitch, self.widget.roll),
            (20.0, -9.0, 11.0),
        )

    def test_held_marker_matches_live_marker_except_for_dotted_yellow_line(self):
        self.widget.set_held_orientation(10.0, -5.0, 2.0)
        self.widget.update_orientation(-5.0, 10.0, 2.0)
        held_painter = Mock()
        live_painter = Mock()

        self.widget._draw_held_orientation_indicator(
            held_painter, 120, 120, 240, 240
        )
        self.widget._draw_orientation_indicator(
            live_painter, 120, 120, 240, 240
        )

        held_line_pen = held_painter.setPen.call_args_list[0].args[0]
        held_center_pen = held_painter.setPen.call_args_list[1].args[0]
        live_center_pen = live_painter.setPen.call_args_list[1].args[0]
        marker_color = QColor(255, 255, 0)
        self.assertEqual(held_line_pen.style(), Qt.DotLine)
        self.assertEqual(held_line_pen.color(), marker_color)
        self.assertEqual(held_line_pen.width(), live_painter.setPen.call_args_list[0].args[0].width())
        self.assertEqual(held_painter.drawLine.call_args, live_painter.drawLine.call_args)
        self.assertEqual(held_center_pen, live_center_pen)
        self.assertEqual(held_painter.drawEllipse.call_args, live_painter.drawEllipse.call_args)
        self.assertEqual(held_painter.setBrush.call_args.args[0], Qt.NoBrush)

    def test_held_marker_is_painted_and_removed_without_stopping_live_updates(self):
        self.widget.update_orientation(0.0, -5.0, 0.0)
        live_only_image = self.widget.grab().toImage()

        self.widget.set_held_orientation(16.0, 12.0, 7.0)
        held_and_live_image = self.widget.grab().toImage()
        self.assertNotEqual(held_and_live_image, live_only_image)

        self.widget.update_orientation(3.0, 9.0, -4.0)
        self.widget.clear_held_orientation()
        updated_live_image = self.widget.grab().toImage()

        self.assertIsNone(self.widget.held_orientation)
        self.assertEqual(
            (self.widget.yaw, self.widget.pitch, self.widget.roll),
            (9.0, 3.0, -4.0),
        )
        self.assertNotEqual(updated_live_image, live_only_image)


if __name__ == "__main__":
    unittest.main()
