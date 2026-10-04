"""Shortcut capture dialog and input-worker queue integration."""

import queue

from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QVBoxLayout, QLabel
from PyQt5.QtCore import QTimer, Qt

from src.util.error_utils import safe_queue_put
from src.config.config import QUEUE_PUT_TIMEOUT
from .base_panel import DEFAULT_SPACING, DIALOG_CONTENT_MARGIN


class KeyCaptureDialog(QDialog):
    """Capture one keyboard or input-worker shortcut."""

    def __init__(self, parent=None, current_key=None, input_command_queue=None,
                 input_response_queue=None, owner_panel=None):
        super().__init__(parent)
        self.setWindowTitle("Capture Reset Shortcut")
        self.setModal(True)
        self.setMinimumSize(300, 150)
        self.input_command_queue = input_command_queue
        self.input_response_queue = input_response_queue
        self.owner_panel = owner_panel
        self.captured_key = current_key if current_key and current_key != "None" else None
        self.display_name = None

        self._apply_parent_theme()

        layout = QVBoxLayout(self)
        layout.setSpacing(DEFAULT_SPACING)
        layout.setContentsMargins(DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN, DIALOG_CONTENT_MARGIN)
        instructions = QLabel("Press any key or gamepad button to set as shortcut:")
        instructions.setAlignment(Qt.AlignCenter)
        layout.addWidget(instructions)
        info = QLabel("(Keyboard, gamepad buttons, or D-pad supported)\n(Esc to cancel)")
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)
        if current_key and current_key != "None":
            layout.addWidget(QLabel(f"Current: {current_key}"))
        self.status_label = QLabel("Waiting for input...")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.owner_panel is not None:
            self.owner_panel.pause_input_response_monitoring()
        if self.input_command_queue is not None:
            if not safe_queue_put(self.input_command_queue, ("start_capture",), timeout=QUEUE_PUT_TIMEOUT):
                self.status_label.setText("Input capture unavailable")
            self.response_timer = QTimer(self)
            self.response_timer.timeout.connect(self._check_input_response)
            self.response_timer.start(50)
        else:
            self.status_label.setText("Input capture unavailable")

    def _check_input_response(self):
        if self.input_response_queue is None:
            return
        try:
            response = self.input_response_queue.get_nowait()
        except queue.Empty:
            return
        except Exception:
            return
        if isinstance(response, (list, tuple)) and len(response) >= 3 and response[0] == "input_captured":
            self.captured_key = response[1]
            self.display_name = response[2]
            self.status_label.setText(f"Captured: {self.display_name}")

    def _apply_parent_theme(self):
        """Inherit the active application/window theme for this top-level dialog."""
        parent = self.parentWidget()
        window = parent.window() if parent is not None else None
        app = QApplication.instance()
        source = window or parent
        if source is not None:
            self.setStyleSheet(source.styleSheet())
            self.setPalette(source.palette())
        elif app is not None:
            self.setStyleSheet(app.styleSheet())
            self.setPalette(app.palette())
        self.setAutoFillBackground(True)

    def done(self, result):
        timer = getattr(self, "response_timer", None)
        if timer is not None:
            timer.stop()
        if self.input_command_queue is not None:
            safe_queue_put(self.input_command_queue, ("stop_capture",), timeout=QUEUE_PUT_TIMEOUT)
        if self.owner_panel is not None:
            self.owner_panel.resume_input_response_monitoring()
        super().done(result)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        self.status_label.setText("Waiting for input worker capture...")
        event.ignore()
