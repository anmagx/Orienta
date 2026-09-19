from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt5.QtCore import QTimer
import sys

from workers.gui_qt.panels.orientation_panel import OrientationPanelQt

app = QApplication(sys.argv)

w = QWidget()
layout = QVBoxLayout(w)
panel = OrientationPanelQt(parent=w)
layout.addWidget(panel)
w.show()

# Print immediate widget info
print("[test] panel.sizeHint()=", panel.sizeHint())
print("[test] panel.geometry()=", panel.geometry())

# After layout
def dump():
    try:
        print("[test] AFTER LAYOUT: panel.geometry()=", panel.geometry())
        try:
            def recurse(w, indent=0):
                prefix = ' ' * indent
                try:
                    name = type(w).__name__
                except Exception:
                    name = str(w)
                try:
                    geo = w.geometry()
                except Exception:
                    geo = None
                try:
                    hint = w.sizeHint()
                except Exception:
                    hint = None
                print(f"{prefix}[test] node={name}, geo={geo}, hint={hint}")
                # Iterate over children widgets
                try:
                    for c in w.children():
                        # Only print QWidget or QLayout/QFrame types
                        recurse(c, indent + 2)
                except Exception:
                    pass

            recurse(panel)
        except Exception as e:
            print("[test] child dump error:", e)
    finally:
        QTimer.singleShot(200, app.quit)

QTimer.singleShot(300, dump)
app.exec_()
print("[test] done")
