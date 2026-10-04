# Orienta Initial Release Checklist

**Status: Not release-ready.** Close the blocking items below, record evidence
for the release gates, and update this checklist as items are completed. This
is a release-readiness assessment, not a claim that every listed issue has
already been reproduced on end-user hardware.

## Project overview

Orienta is a Windows 3-DOF head-tracking application. An IMU sends timestamped
CSV samples over a serial port; a quaternion complementary filter estimates
yaw, pitch, and roll; a PyQt5 GUI displays orientation and controls; and a UDP
worker sends opentrack's six-double packet with zero translation. There is no
magnetometer or translation tracking, so yaw is gyro-integrated and can drift.

The Windows multiprocessing design has a parent `ProcessHandler`, five child
processes (input, GUI, serial, fusion, and UDP), plus parent log-writer and
worker-monitor threads. Queues are owned by the process manager; pipeline
queues and display queues are deliberately separate. The GUI is the only Qt
widget owner. Preferences and logs live under `%LOCALAPPDATA%\Orienta`.

The architecture and module-by-module reference were cross-checked against the
implementation in [`ARCHITECTURE.md`](ARCHITECTURE.md) and
[`CODEBASE_REFERENCE.md`](CODEBASE_REFERENCE.md). The implementation inventory
includes the entry point, worker processes, Qt panels and helpers, managers,
utilities, configuration, assets/themes, Arduino example, build scripts,
tests, and user/developer documentation.

## Blocking items

### P0 — Resolve before publishing

- [ ] **Provide one working, documented clean-install route.** The README
  directs users to a root `install.bat` and root `requirements.txt`, but neither
  is present in the tracked repository. The manual installation command
  therefore cannot install dependencies from a clean checkout. Choose the
  intended end-user path (for example, add and test those files, or rewrite the
  instructions to use the supported setup files), then verify it from a clean
  Windows checkout with no pre-existing virtual environment.
  References: [`README.md`](../README.md),
  [`build/dev/setup_dev.bat`](../build/dev/setup_dev.bat),
  [`build/dev/requirements.txt`](../build/dev/requirements.txt).
- [ ] **Make the supported Python range installable and testable.** The runtime
  dependency manifest says Python 3.8–3.13 are supported but requires
  `numpy>=1.26.0`; NumPy 1.26 does not provide Python 3.8 support. The entry
  point warns about Python 3.14+, while the README lists 3.14 without
  qualification. Set a truthful support range, express compatible dependency
  constraints, and validate clean installs on the supported Python versions.
  References: [`build/dev/requirements.txt`](../build/dev/requirements.txt),
  [`orienta.py`](../orienta.py), [`README.md`](../README.md).
- [ ] **Fix the drift-smoothing zero-value mismatch.** The Preferences slider
  permits `0.0 s`, and the UI can save that value, but the fusion worker only
  applies a smoothing duration when it is strictly positive. Align the
  control's minimum/meaning with the worker's accepted values, including
  loading saved values, and verify that the displayed and active values stay
  in sync.
  References: [`preferences_panel.py`](../src/workers/gui_qt/panels/preferences_panel.py),
  [`fusion_wrk.py`](../src/workers/fusion_wrk.py).
- [ ] **Pass release qualification on Windows.** Exercise the documented
  install route and portable build on a clean Windows environment, then smoke
  test both the source run and packaged executable. Confirm startup, normal
  window close, Ctrl+C, worker failure/restart behavior, and clean shutdown
  without leaving worker processes behind.
  References: [`build/build_exe.bat`](../build/build_exe.bat),
  [`build/orienta.spec`](../build/orienta.spec),
  [`src/workers/process_man.py`](../src/workers/process_man.py).
- [ ] **Pass an end-to-end sensor and opentrack test.** With the bundled
  firmware and a supported IMU, verify the configured serial port/baud,
  seven-field sample parsing, gyro and level calibration, axis directions,
  reset/disengage shortcuts, stable live display, and UDP output accepted by
  opentrack. Confirm packets contain three zero translation values followed
  by yaw, pitch, and roll in degrees; test both uncapped and configured output
  rates.
  References: [`ARCHITECTURE.md`](ARCHITECTURE.md),
  [`Calibrated_sensor_output_timestamped.ino`](../arduino/Calibrated_sensor_output_timestamped/Calibrated_sensor_output_timestamped.ino),
  [`serial_wrk.py`](../src/workers/serial_wrk.py),
  [`fusion_wrk.py`](../src/workers/fusion_wrk.py),
  [`udp_wrk.py`](../src/workers/udp_wrk.py).

### P1 — Fix before or with the initial release

- [ ] **Correct the public sensor timestamp documentation.** The README labels
  the first CSV field as milliseconds, while the bundled firmware emits
  seconds and the parser/filter treat timestamps as seconds. State the
  required units consistently and validate the example data against the parser.
  References: [`README.md`](../README.md),
  [`ARCHITECTURE.md`](ARCHITECTURE.md),
  [`error_utils.py`](../src/util/error_utils.py).
- [ ] **Add automated tests for release-critical contracts.** The current
  tracked tests cover user-data paths, preference persistence, log rotation,
  and failed preference saves. Add focused tests for IMU parsing and units,
  filter behavior/calibration/reset and non-finite input, command validation,
  UDP packet shape/rate control, and worker shutdown/error paths. Keep tests
  deterministic and runnable using the documented development setup.
  References: [`src/tests/`](../src/tests),
  [`ARCHITECTURE.md`](ARCHITECTURE.md).
- [ ] **Replace inaccurate product/technology claims.** The About panel
  describes the application as “IMU + CV”, although the project has no
  computer-vision pipeline. Describe the actual IMU-only, 3-DOF behavior and
  ensure release copy does not imply translation tracking or magnetometer
  support.
  References: [`about_panel.py`](../src/workers/gui_qt/panels/about_panel.py),
  [`README.md`](../README.md).
- [ ] **Align repository documentation with tracked files and current UI.**
  The codebase reference still lists root installer/launcher/dependency files
  that are absent and describes a tabbed layout in places where the UI is now
  orientation-first with dialog-backed secondary panels. Update it alongside
  the chosen installation route so maintainers and users receive consistent
  instructions.
  References: [`CODEBASE_REFERENCE.md`](CODEBASE_REFERENCE.md),
  [`gui_wrk.py`](../src/workers/gui_wrk.py),
  [`README.md`](../README.md).

### P2 — Complete as part of release readiness

- [ ] **Finalize release identity.** Decide the initial public version and
  ensure the app title/About panel, release tag, executable name, and release
  notes consistently identify it. The current configured version is `0.10`.
  References: [`config.py`](../src/config/config.py),
  [`orienta.spec`](../build/orienta.spec).
- [ ] **Verify first-run and upgrade persistence behavior.** On a clean user
  profile and on an upgrade profile, check default preferences, theme and
  shortcut persistence, malformed/older configuration handling, log creation
  and rotation, and the documented manual migration from the old local config
  location. Confirm failures are visible and do not report a save as
  successful.
  References: [`preferences_manager.py`](../src/managers/preferences_manager.py),
  [`paths.py`](../src/util/paths.py),
  [`gui_wrk.py`](../src/workers/gui_wrk.py),
  [`README.md`](../README.md).
- [ ] **Verify release assets and attribution.** Inspect the packaged icon,
  logo, and both themes in the executable; confirm the About panel's third
  party list is current; and review all bundled runtime/build dependencies and
  the Arduino library/example for required notices or distribution terms.
  References: [`orienta.spec`](../build/orienta.spec),
  [`src/img/`](../src/img),
  [`src/themes/`](../src/themes),
  [`about_panel.py`](../src/workers/gui_qt/panels/about_panel.py),
  [`LICENSE`](../LICENSE).
- [ ] **Publish concise user-facing recovery guidance.** Explain how to
  identify the correct serial port and baud rate, recover from a lost serial
  connection, calibrate with the sensor still, configure opentrack's UDP
  receiver, and locate logs/preferences. Keep troubleshooting steps aligned
  with actual worker behavior.
  References: [`README.md`](../README.md),
  [`serial_wrk.py`](../src/workers/serial_wrk.py),
  [`process_man.py`](../src/workers/process_man.py).

## Verification recorded during this review

- `python -m compileall -q orienta.py src` passed.
- `build\dev\.venv\Scripts\python.exe -m unittest discover -s src\tests -v`
  passed all 11 existing tests.
- Running the same test discovery with the system Python failed to import
  PyQt5. This is an environment/dependency issue, not a failing assertion; the
  existing development virtual environment ran the suite successfully.
- No packaged executable build or physical sensor/opentrack test was performed
  during this review. Those remain explicit release gates above.
