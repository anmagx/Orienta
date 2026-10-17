"""Shared construction helpers for themed, consistently spaced dialogs."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.config.config import DEFAULT_SPACING, DIALOG_CONTENT_MARGIN
from src.util.windows_titlebar import schedule_windows_titlebar_theme


def configure_dialog(dialog: QDialog, object_name: str, title: str) -> QVBoxLayout:
    """Apply common top-level dialog styling and return its content layout."""
    dialog.setObjectName(object_name)
    dialog.setAttribute(Qt.WA_StyledBackground, True)
    dialog.setAutoFillBackground(True)
    dialog.setWindowTitle(title)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(
        DIALOG_CONTENT_MARGIN,
        DIALOG_CONTENT_MARGIN,
        DIALOG_CONTENT_MARGIN,
        DIALOG_CONTENT_MARGIN,
    )
    layout.setSpacing(DEFAULT_SPACING)
    schedule_windows_titlebar_theme(dialog)
    return layout


def create_close_button(dialog: QDialog) -> QPushButton:
    """Return a standard Close button connected to its owning dialog."""
    button = QPushButton("Close", dialog)
    button.clicked.connect(dialog.close)
    return button


def configure_section_layout(layout: QLayout) -> None:
    """Apply the common frame padding and spacing to a section layout."""
    layout.setContentsMargins(
        DIALOG_CONTENT_MARGIN,
        DIALOG_CONTENT_MARGIN,
        DIALOG_CONTENT_MARGIN,
        DIALOG_CONTENT_MARGIN,
    )
    layout.setSpacing(DEFAULT_SPACING)


def create_dialog_content(
    dialog: QDialog,
    dialog_layout: QVBoxLayout,
    object_name: str,
) -> QVBoxLayout:
    """Add a themed, inset content surface inside a standard dialog shell."""
    content = QWidget(dialog)
    content.setObjectName(object_name)
    content.setAttribute(Qt.WA_StyledBackground, True)
    content_layout = QVBoxLayout(content)
    configure_section_layout(content_layout)
    dialog_layout.addWidget(content)
    return content_layout
