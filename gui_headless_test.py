import os
import sys
import tempfile
import queue

# Ensure headless Qt
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

# Simple smoke tests for PreferencesPanel and OrientationPanel

def run_tests():
    failures = []
    try:
        from PyQt5.QtWidgets import QApplication
    except Exception as e:
        print(f"SKIP: PyQt5 not available: {e}")
        return 2

    app = QApplication.instance() or QApplication([])

    # Use a temporary directory for preferences so we don't touch real config
    tmpdir = tempfile.mkdtemp(prefix='orienta_test_')
    from src.managers.preferences_manager import PreferencesManager
    pm = PreferencesManager(config_dir=tmpdir)

    # Create a PreferencesPanel and set tuning values
    from src.workers.gui_qt.panels.preferences_panel import PreferencesPanel
    prefs_panel = PreferencesPanel(parent=None, preferences_manager=pm)

    # Assign some non-default tuning values
    prefs_panel.alpha_pitch = 0.987
    prefs_panel.alpha_roll = 0.954
    prefs_panel.stationary_gyro_threshold = 3.5
    prefs_panel.stationary_debounce_s = 0.123
    prefs_panel.drift_smoothing_time = 0.5
    prefs_panel.drift_correction_strength = 0.45
    prefs_panel.drift_transition_curve = 'cosine'
    prefs_panel.gyro_bias_cal_samples = 500
    prefs_panel.invert_yaw = True
    prefs_panel.invert_pitch = False
    prefs_panel.invert_roll = True

    # Save via PreferencesManager using orientation section
    tuning = prefs_panel.get_tuning_preferences()
    saved = pm.save({'orientation': tuning})
    if not saved:
        failures.append('PreferencesManager.save returned False')
    loaded = pm.load()
    loaded_orient = loaded.get('orientation', {})

    # Compare saved vs loaded keys
    for k, v in tuning.items():
        if str(loaded_orient.get(k, '')) != str(v):
            failures.append(f"Mismatch for {k}: saved={v} loaded={loaded_orient.get(k)}")

    # Test OrientationPanel queue commands and clear_calibration_state preserving sliders
    from src.workers.gui_qt.panels.orientation_panel import OrientationPanelQt

    control_q = queue.Queue()
    messages = []
    def msg_cb(m):
        messages.append(m)

    orient_panel = OrientationPanelQt(parent=None, control_queue=control_q, message_callback=msg_cb)

    # Wire preferences_panel so recalibrate reads sample count
    orient_panel.preferences_panel = prefs_panel

    # Set drift slider to a non-default
    try:
        orient_panel.drift_yaw_slider.setValue(123)
        before_val = orient_panel.drift_yaw_slider.value()
    except Exception:
        failures.append('Failed to set/query drift_yaw_slider')
        before_val = None

    # Call clear_calibration_state and ensure slider unchanged
    try:
        orient_panel.clear_calibration_state()
        after_val = orient_panel.drift_yaw_slider.value() if before_val is not None else None
        if before_val is not None and after_val != before_val:
            failures.append(f"drift_yaw_slider changed after clear_calibration_state: before={before_val} after={after_val}")
    except Exception as e:
        failures.append(f"clear_calibration_state raised: {e}")

    # Test reset orientation command
    try:
        orient_panel._on_reset_orientation()
        item = control_q.get(timeout=1)
        if item != 'reset_orientation':
            failures.append(f"reset_orientation queue item mismatch: {item}")
    except Exception as e:
        failures.append(f"_on_reset_orientation failed: {e}")

    # Test recalibrate command sends sample count
    try:
        orient_panel._on_recalibrate()
        item2 = control_q.get(timeout=1)
        # Expected ('recalibrate_gyro_bias', 500) or ('recalibrate_gyro_bias',)
        if not (isinstance(item2, (tuple, list)) and item2[0] == 'recalibrate_gyro_bias'):
            failures.append(f"recalibrate queue item unexpected: {item2}")
        else:
            # If sample count present, verify equals prefs_panel.gyro_bias_cal_samples
            if len(item2) > 1 and str(item2[1]) != str(prefs_panel.gyro_bias_cal_samples):
                failures.append(f"recalibrate sample count mismatch: {item2[1]} vs {prefs_panel.gyro_bias_cal_samples}")
    except Exception as e:
        failures.append(f"_on_recalibrate failed: {e}")

    if failures:
        print("FAILED:\n" + "\n".join(failures))
        return 1

    print("OK: headless GUI smoke tests passed")
    return 0

if __name__ == '__main__':
    rc = run_tests()
    sys.exit(rc)
