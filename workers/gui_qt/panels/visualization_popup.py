"""Floating visualization window lifecycle."""

from PyQt5.QtCore import QEvent, QRect, Qt, QTimer
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFrame, QVBoxLayout, QWidget

from .visualization_widget import SquareContainer


class VisualizationPopupController:
    """Own the visualization widget's pop-out/pop-in lifecycle."""

    def __init__(self, panel):
        self.panel = panel

    def create_popup(self):
        panel = self.panel
        try:
            popup = QWidget(None, Qt.Window | Qt.FramelessWindowHint | Qt.Tool)
            popup.setObjectName("visualizationPopup")
            popup.setAttribute(Qt.WA_StyledBackground, True)
            popup.setWindowFlags(popup.windowFlags() | Qt.WindowStaysOnTopHint)
            popup.setAttribute(Qt.WA_TranslucentBackground, True)
            popup.setAttribute(Qt.WA_ShowWithoutActivating, True)

            container = QFrame(popup)
            container.setObjectName("visualizationPopup")
            container.setAttribute(Qt.WA_StyledBackground, True)
            parent_window = panel.window()
            app = QApplication.instance()
            style = parent_window.styleSheet() or (app.styleSheet() if app else "")
            if style:
                popup.setStyleSheet(style)
            palette = parent_window.palette() if parent_window else (app.palette() if app else QPalette())
            popup.setPalette(palette)
            popup.setAutoFillBackground(True)
            container.setStyleSheet("border-radius: 10px; border: 1px solid palette(mid);")

            outer = QVBoxLayout(popup)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(container)
            inner = QVBoxLayout(container)
            inner.setContentsMargins(8, 8, 8, 8)
            square = SquareContainer(panel.visualization_widget, parent=container)
            inner.addWidget(square)

            screen = QApplication.primaryScreen().availableGeometry()
            geometry = panel._popup_geom
            if isinstance(geometry, QRect):
                popup_geometry = QRect(geometry)
            else:
                popup_geometry = QRect(screen.right() - 344, screen.bottom() - 344, 320, 320)
            popup.setGeometry(popup_geometry)
            popup.setWindowOpacity(max(0.1, min(1.0, float(panel._popup_opacity))))
            popup.installEventFilter(panel)
            popup.show()
            panel._popup_geom = popup.geometry()
            return popup, square
        except Exception:
            return None, None

    def toggle(self):
        panel = self.panel
        if not panel._viz_popped_out:
            panel._viz_prev_size = panel.visualization_square.size()
            parent = panel.visualization_square.parent()
            panel._viz_layout = parent.layout() if parent else None
            panel._viz_index = None
            if panel._viz_layout:
                for index in range(panel._viz_layout.count()):
                    if panel._viz_layout.itemAt(index).widget() is panel.visualization_square:
                        panel._viz_index = index
                        break
            placeholder = QWidget()
            placeholder.setMinimumSize(160, 160)
            placeholder.setSizePolicy(panel.visualization_square.sizePolicy())
            if panel._viz_layout is not None and panel._viz_index is not None:
                panel._viz_layout.takeAt(panel._viz_index)
                panel._viz_layout.insertWidget(panel._viz_index, placeholder, stretch=1)
            popup, square = self.create_popup()
            if popup is None:
                if panel._viz_layout is not None and panel._viz_index is not None:
                    panel._viz_layout.insertWidget(panel._viz_index, panel.visualization_square, stretch=1)
                return
            panel._popup_window = popup
            panel._popup_square = square
            panel._placeholder_square = placeholder
            panel._viz_popped_out = True
            panel.pop_viz_button.setText("Pop In")
            return

        if panel._popup_window:
            panel.visualization_widget.setParent(panel.visualization_square)
            panel.visualization_widget.show()
            panel.visualization_square._child = panel.visualization_widget
            panel.visualization_square.show()
            if panel._viz_layout is not None and panel._viz_index is not None:
                for index in range(panel._viz_layout.count()):
                    if panel._viz_layout.itemAt(index).widget() is panel._placeholder_square:
                        panel._viz_layout.takeAt(index)
                        panel._viz_layout.insertWidget(index, panel.visualization_square, stretch=1)
                        break
            panel._popup_window.close()
        panel._popup_window = None
        panel._popup_square = None
        panel._placeholder_square = None
        panel._viz_layout = None
        panel._viz_index = None
        panel._viz_popped_out = False
        panel.pop_viz_button.setText("Pop Out")
        QTimer.singleShot(0, panel.visualization_square.refresh_child_geometry)
        QTimer.singleShot(0, panel.visualization_widget.show)

    def event_filter(self, obj, event):
        panel = self.panel
        if obj is panel._popup_window and event.type() in (QEvent.Move, QEvent.Resize):
            panel._popup_geom = QRect(obj.geometry())
            panel._request_pref_save()
        return False
