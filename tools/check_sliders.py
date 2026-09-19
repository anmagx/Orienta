import sys
sys.path.insert(0, 'd:/Development/Projects/Orienta')
from PyQt5.QtWidgets import QApplication
app = QApplication([])
from workers.gui_qt.panels.orientation_panel import OrientationPanelQt
p = OrientationPanelQt()
print('has drift_yaw_slider:', hasattr(p, 'drift_yaw_slider'))
print('has drift_pitch_slider:', hasattr(p, 'drift_pitch_slider'))
print('has drift_roll_slider:', hasattr(p, 'drift_roll_slider'))
try:
    print('drift_yaw_slider type:', type(p.drift_yaw_slider))
except Exception as e:
    print('yaw type error:', e)
try:
    print('drift_yaw_slider exists in children:', any(type(c).__name__=='QSlider' for c in p.findChildren(object)))
except Exception as e:
    print('findChildren error:', e)
print('done')
