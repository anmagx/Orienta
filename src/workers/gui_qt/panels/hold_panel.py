"""Reusable hold-still status widget."""

from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget
from PyQt5.QtCore import QTimer, Qt


class HoldPanelQt(QWidget):
    """Animated hold-still banner used by the orientation UI."""

    def __init__(self, parent=None, text=None, height=30):
        super().__init__(parent)
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._on_blink_timer)
        self._scroll_index = 0
        self._is_scrolling = False
        self._direction = 1
        self._base_text = text if text else "- HOLD STILL & UPRIGHT -"
        self._highlight_width = 5
        self.setFixedHeight(int(height))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addStretch()
        self.hold_still_label = QLabel(self._base_text)
        self.hold_still_label.setAlignment(Qt.AlignCenter)
        font = self.hold_still_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.hold_still_label.setFont(font)
        self._set_normal_style()
        layout.addWidget(self.hold_still_label)
        layout.addStretch()

    def _set_normal_style(self):
        self.hold_still_label.setStyleSheet("color: #666666;")

    def _on_blink_timer(self):
        if not self._is_scrolling:
            return
        width = min(self._highlight_width, len(self._base_text))
        if len(self._base_text) <= width:
            self._update_scrolling_text()
            return
        self._scroll_index += self._direction
        max_index = len(self._base_text) - width
        if self._scroll_index >= max_index:
            self._scroll_index = max_index
            self._direction = -1
        elif self._scroll_index <= 0:
            self._scroll_index = 0
            self._direction = 1
        self._update_scrolling_text()

    @staticmethod
    def _escape_char(char):
        return {"\n": "<br>", " ": "&nbsp;", "&": "&amp;", "<": "&lt;", ">": "&gt;"}.get(char, char)

    def _update_scrolling_text(self):
        width = min(self._highlight_width, len(self._base_text))
        start = self._scroll_index
        parts = []
        for index, char in enumerate(self._base_text):
            color = "#FFD700" if start <= index < start + width else "#666666"
            weight = " bold" if start <= index < start + width else ""
            parts.append(f'<span style="color: {color}; font-weight:{weight};">{self._escape_char(char)}</span>')
        self.hold_still_label.setText("".join(parts))

    def start_blinking(self):
        if self._is_scrolling:
            return
        self._is_scrolling = True
        self._scroll_index = 0
        self._direction = 1
        self._blink_timer.start(15)
        self._update_scrolling_text()

    def stop_blinking(self):
        if not self._is_scrolling:
            return
        self._is_scrolling = False
        self._blink_timer.stop()
        self._scroll_index = 0
        self.hold_still_label.setText(self._base_text)
        self._set_normal_style()

    def is_blinking(self):
        return self._is_scrolling
