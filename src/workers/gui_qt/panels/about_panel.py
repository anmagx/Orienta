"""
About Panel for orienta GUI — compact modern redesign.

Provides a compact, responsive layout: logo on the left, concise
app info and credits on the right, and a minimal third-party list.
"""
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QSizePolicy,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPixmap, QPalette

import os

from src.config.config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_SPACING,
)
from .dialog_utils import configure_section_layout


class AboutPanel(QWidget):
    """Compact, modern About panel showing app info and credits."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aboutPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setup_ui()

    def setup_ui(self):
        main = QVBoxLayout(self)
        configure_section_layout(main)

        about_group = QGroupBox("About Orienta", self)
        about_layout = QHBoxLayout(about_group)
        configure_section_layout(about_layout)

        # Logo (larger for better readability)
        logo_label = QLabel()
        logo_label.setFixedSize(140, 140)
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        logo_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), '..', '..', '..', 'img', 'orienta_logo.png')
        )
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                logo_label.setPixmap(pixmap.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                logo_label.setText(APP_NAME[:2].upper())
        else:
            logo_label.setText(APP_NAME[:2].upper())

        about_layout.addWidget(logo_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        # Info column
        info_col = QVBoxLayout()
        info_col.setSpacing(DEFAULT_SPACING)

        # Title row with version at the right
        title_row = QHBoxLayout()
        title_label = QLabel(APP_NAME)
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title_label.setFont(title_font)

        version_label = QLabel(f"v{APP_VERSION}")
        version_font = QFont()
        version_font.setPointSize(10)
        version_label.setFont(version_font)

        title_row.addWidget(title_label)
        title_row.addStretch()
        title_row.addWidget(version_label)
        info_col.addLayout(title_row)

        # Short description
        desc = QLabel("Compact real-time head tracking using IMU sensor data")
        desc.setWordWrap(True)
        desc.setMaximumHeight(48)
        info_col.addWidget(desc)

        # Author and repo link
        author_row = QHBoxLayout()
        author_lbl = QLabel("maintained by anmagx")
        author_font = QFont()
        author_font.setBold(True)
        author_lbl.setFont(author_font)

        github_lbl = QLabel('<a href="https://github.com/anmagx/orienta">github.com/anmagx/orienta</a>')
        github_lbl.setOpenExternalLinks(True)
        github_lbl.setTextFormat(Qt.RichText)

        # Use palette link color so the link remains visible in dark themes
        link_color = self.palette().color(QPalette.Link).name()
        github_lbl.setStyleSheet(f"color: {link_color};")

        author_row.addWidget(author_lbl)
        author_row.addStretch()
        author_row.addWidget(github_lbl)
        info_col.addLayout(author_row)

        about_layout.addLayout(info_col, 1)
        main.addWidget(about_group)

        packages_group = QGroupBox("Third-party software", self)
        packages_layout = QVBoxLayout(packages_group)
        configure_section_layout(packages_layout)
        packages_label = QLabel()
        packages_label.setTextFormat(Qt.RichText)
        packages_label.setText(
            """
            <ul style="margin:0; padding-left:14px;">
              <li>PyQt5 - Cross-platform GUI</li>
              <li>keyboard -Global hotkeys</li>
              <li>pygame - Gamepad support</li>
              <li>pyserial - Serial communication</li>
              <li>pyInstaller - Application bundling</li>
            </ul>
            """
        )
        packages_label.setWordWrap(True)
        packages_layout.addWidget(packages_label)
        main.addWidget(packages_group)