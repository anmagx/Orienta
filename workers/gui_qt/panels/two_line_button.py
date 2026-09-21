"""Two-line push button used for shortcut-aware actions."""

from PyQt5.QtWidgets import QStyleOptionButton, QPushButton
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QPalette
from PyQt5.QtCore import Qt, QRect, QSize


class TwoLineButton(QPushButton):
    """Paint a primary label and optional shortcut label."""

    def __init__(self, main_text="", sub_text="", parent=None):
        super().__init__(main_text, parent)
        self._main = main_text or ""
        self._sub = sub_text or ""
        self.setCursor(Qt.PointingHandCursor)

    def setParts(self, main, sub):
        self._main = main or ""
        self._sub = sub or ""
        self.setText(self._main)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        base = super().sizeHint()
        main_font = QFont(self.font())
        main_font.setBold(True)
        main_metrics = QFontMetrics(main_font)
        sub_height = 0
        sub_width = 0
        if self._sub:
            sub_font = QFont(self.font())
            sub_font.setPointSize(max(sub_font.pointSize() - 2, 8))
            sub_metrics = QFontMetrics(sub_font)
            sub_height = sub_metrics.height()
            sub_width = sub_metrics.horizontalAdvance(self._sub)
        height = main_metrics.height() + (sub_height + 2 if self._sub else 0) + 12
        width = max(base.width(), main_metrics.horizontalAdvance(self._main) + 24, sub_width + 24)
        return QSize(width, height)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        option.initFrom(self)
        option.text = ""
        painter = QPainter(self)
        self.style().drawControl(self.style().CE_PushButton, option, painter, self)
        rect = self.rect().adjusted(8, 6, -8, -6)
        main_font = QFont(self.font())
        main_font.setBold(True)
        main_height = QFontMetrics(main_font).height()
        role = QPalette.ButtonText
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        main_pen = self.palette().color(group, role)

        painter.setFont(main_font)
        painter.setPen(main_pen)
        if self._sub:
            sub_font = QFont(self.font())
            sub_font.setPointSize(max(sub_font.pointSize() - 2, 8))
            sub_metrics = QFontMetrics(sub_font)
            total_height = main_height + 2 + sub_metrics.height()
            y = rect.top() + max(0, (rect.height() - total_height) // 2)
            painter.drawText(QRect(rect.left(), y, rect.width(), main_height), int(Qt.AlignCenter), self._main)
            painter.setFont(sub_font)
            painter.drawText(QRect(rect.left(), y + main_height + 2, rect.width(), sub_metrics.height()), int(Qt.AlignCenter), self._sub)
        else:
            painter.drawText(rect, int(Qt.AlignCenter), self._main)
        painter.end()
