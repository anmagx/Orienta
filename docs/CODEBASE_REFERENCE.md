# Orienta codebase reference

This is a file-by-file guide to the current repository. It complements
[`ARCHITECTURE.md`](ARCHITECTURE.md), which is the source of truth for runtime
data flow and inter-process contracts.

## Repository map

| Path | Role |
|---|---|
| `orienta.py` | Application entry point; owns the parent wait loop. |
| `src/` | Python source package and runtime assets. |
| `src/workers/` | All multiprocessing worker implementations and the process manager. |
| `src/workers/gui_qt/` | PyQt5 window, panels, and icon helper. |
| `src/managers/` | Application-wide preference and theme managers used by the GUI process. |
| `src/util/` | Queue/error/parsing helpers and cross-process logging API. |
| `src/config/` | Static application defaults. |
| `src/tests/` | Automated regression tests. |
| `arduino/` | Example firmware that produces compatible IMU frames. |
| `src/themes/` | Application-wide light/dark Qt style sheets. |
| `src/img/` | Window icon and application logo. |
| `install.bat`, `launch_orienta.bat` | Windows installation and launch helpers. |
| `README.md` | End-user installation, hardware protocol, and operation guide. |
| `requirements.txt` | Python runtime dependencies. |

## Entrypoint and worker modules

### `orienta.py`

`main()` is intentionally small. It rejects Python older than 3.8, warns for
Python 3.14+, starts `ProcessHandler`, and waits on its shared stop event. All
lifecycle policy belongs in the process manager, not in this file.

### `src/workers/process_man.py`

`ProcessHandler` is the ownership boundary for:

* all queues and the global `multiprocessing.Event`;
* input, GUI, serial, fusion, and UDP `Process` instances;
* a parent-only log-writer thread;
* a parent-only worker-monitor/restart thread;
* signal handling and idempotent forced shutdown.

`start_workers()` is the canonical worker wiring. Change it whenever a worker
signature or queue is added. It also stores each worker's exact startup
configuration for automatic restart. `get_queue_health_report()` and
`log_queue_health()` expose best-effort queue sizing diagnostics; `qsize()` is
not a correctness mechanism.

### `src/workers/serial_wrk.py`

`open_serial()` retries `serial.Serial` creation while honoring stop/control
signals. `serial_thread()` drains serial controls before reading, copies raw
lines to data and display queues, reports connection/data/rate state, and
closes on a stop or serial exception. `run_worker()` is the process target.

The reader decodes with `errors='ignore'`; malformed-but-decodable data is
rejected later by fusion parsing. Serial reconnection after a read failure
requires another start command because the worker returns to its idle state.

### `src/workers/fusion_wrk.py`

This is the core orientation implementation. `QuaternionComplementaryFilter`
contains quaternion math, accelerometer-derived roll/pitch, stationary
detection, gradual drift correction, calibration offsets, and reset state.
`run_worker()` implements the control command protocol, gathers calibration
samples, runs the filter, handles output queue backpressure, and produces
status changes. The fusion worker validates that computed Euler outputs are
finite before publishing them to downstream queues and will drop non-finite
frames, emitting rate-limited logs for visibility.

Important state fields:

* `q`: normalized orientation quaternion `[w, x, y, z]`;
* `last_time`: IMU timing baseline, not wall-clock time;
* `gyro_bias_yaw`: stationary-estimated `gz` bias;
* `center_offset_*`: level/rest-pose offsets;
* `center_threshold_*`: independent limits before auto-drift correction;
* `invert_*`: output axis inversion;
* `_stationary_start` and `_drift_correction_start`: timestamp-driven state.

The helper `_calculate_drift_factor`, `_slerp`, and `_nlerp` are retained
filter utilities. The active `update()` implementation uses the configured
curve's per-frame correction calculation directly. Preserve math units:
gyroscope input is degrees/second, quaternion integration converts it to
radians/second, and all user-facing values are degrees.

### `src/workers/udp_wrk.py`

`run_worker()` owns one IPv4 UDP socket. It drains to a latest sample, handles
target/enabled controls, serializes opentrack's six-double packet, reports send
rate, and always closes the socket. It intentionally sends nothing until the
UI enables output. The UDP worker also validates that Euler values are finite
and will drop any NaN/Infinity samples before sending, with rate-limited
logging to avoid log spam.

### `src/workers/input_wrk.py`

`PygameManager` is a per-process singleton for joystick initialization.
`InputWorker` owns shortcut state, input-capture mode, an internal command
thread, and optional listener threads. Its `run_worker()` starts the object and
keeps the process alive until the shared event is set. Keyboard and pygame are
optional imports; absence disables the corresponding input path rather than
the entire application.

`keyboard.unhook_all()` is process-global for this worker. Keep keyboard hook
ownership within this module; another listener in the same process would
interfere with it.

### `src/workers/gui_wrk.py`

`TabbedGUIWorker` is the actual main window despite the historical name. It
constructs the orientation-first layout, creates dialog-backed support panels,
owns queue polling and UI refresh timers, routes statuses to panels, and loads
or saves preferences. `start_gui_worker()` sets up `QApplication`; `run_worker`
is the process target.

The GUI has refactor-era compatibility names such as `calibration_panel`
pointing at `orientation_panel`; preserve those references until all callers
have been migrated. It also contains bounded queue-draining loops, which are
deliberate latency protection.

## GUI components

### `src/workers/gui_qt/panels/orientation_panel.py`

This large module is the primary feature surface:

* `OrientationPanelQt` renders the orientation UI and owns fusion controls.
* `hold_panel.py` provides the animated `HoldPanelQt` status banner.
* `two_line_button.py` provides the reusable shortcut-aware button.
* `shortcut_dialog.py` owns keyboard/gamepad capture through the input worker.
* `visualization_popup.py` owns visualization reparenting, popup geometry, and opacity.
* `OrientationVisualizationWidget` paints orientation and drift indicators.
* `SquareContainer` maintains a square visualization child.
* `OrientationPanelQt` renders yaw/pitch/roll values and controls reset,
  calibration, drift thresholds/disengagement, shortcuts, visualization, and
  Monitor/Preferences/About dialogs.

`OrientationPanelQt` is also the consumer of `inputResponseQueue`. It sends
fusion controls through its `control_queue`. `PreferencesPanel` connects to it
as `orientation_panel`; the retired `calibration_panel` alias is no longer an
active API.

### `src/workers/gui_qt/panels/connection_panel.py`

`ConnectionPanelQt` combines former serial and network screens. It validates
and stores port/baud/IP/UDP-port UI values, produces serial/UDP commands,
tracks live state and rates, and serializes the `serial`/`network` preference
sections. `TwoLineButton` is a local rendering implementation, separate from
the similarly named orientation class.

### `src/workers/gui_qt/panels/preferences_panel.py`

`PreferencesPanel` supplies theme, fusion tuning, stationary/drift behavior,
gyro calibration sample count, axis inversion, and disengage-mode controls.
Several sliders use one-shot QTimers so a drag does not flood the small fusion
control queue. `_apply_settings_to_fusion_worker()` is the bridge from saved
UI state back to runtime commands. New persisted fusion options need:

1. widget/state/default,
2. loading and preference serialization,
3. a command from this panel,
4. fusion command validation/application.

### `src/workers/gui_qt/panels/message_panel.py`

`MessagePanelQt` batches raw serial and application messages in capped Python
lists, then writes them to read-only text widgets only when visible. Its parent
is initially `None` so dialog code can reparent it. It is not a logging
transport; `logQueue` remains the durable cross-process log transport.

### `src/workers/gui_qt/panels/about_panel.py`

`AboutPanel` renders metadata, logo, project link, and dependency attribution.
It is visual only. Its mention of “IMU + CV” is UI text, not evidence that the
application includes a computer-vision pipeline.

### `src/workers/gui_qt/panels/base_panel.py`

`BasePanelQt` is a light `QGroupBox` base with optional message callback,
preference hooks, and a Qt message signal. `ConnectionPanelQt` uses it; panels
that do not share its group-box model use direct Qt base classes.

### Managers and GUI helper

* `src/managers/preferences_manager.py` uses `src/util/paths.py` to locate
  `%LOCALAPPDATA%\Orienta\config.cfg`, reads nested INI sections, and writes atomically.
  Its older flat-key helpers coexist with the nested GUI format.
* `src/managers/theme_manager.py` searches upward for `src/themes/`, reads a requested
  QSS file, and applies it to `QApplication`.
* `helpers/icon_helper.py` searches upward for `src/img/icon.ico` (or `icon.png`)
  and applies it to a window.

The package `__init__.py` files in `src/workers/`, `src/workers/gui_qt/`, its
subdirectories, `src/config/`, and `src/util/` document/import package APIs. They have
no independent runtime loop, but their exports affect import compatibility.

## Shared configuration and utilities

### `src/config/config.py`

Static defaults only; it must not accumulate mutable runtime state. It defines
application version, timer periods, serial/network defaults, queue capacities,
timeouts, restart policy, complementary-filter tuning, calibration constants,
display limits, logging rotation, and preference filename. The duplicate
`THRESH_DEBOUNCE_MS` definition currently resolves to the same `150` value.

### `src/util/error_utils.py`

The shared defensive helpers are:

* `safe_queue_put` / `safe_queue_get` for bounded best-effort queue I/O;
* `monitor_queue_health` / `log_queue_stats` for queue diagnostics;
* `parse_csv_line` / `parse_imu_line` for input validation; `parse_imu_line` additionally rejects non-finite values (NaN/Infinity) and enforces timestamp/accel/gyro sanity checks.
* `clamp`, `normalize_angle`, `validate_numeric_range`, and
  `safe_float_convert` for scalar handling.

These helpers intentionally return failure/default values in availability
paths. Do not use them where silent loss would violate a new correctness
requirement without adding explicit reporting at the caller.

### `src/util/log_utils.py`

`log_info`, `log_warning`, and `log_error` wrap `log()`, which sends the
standard three-part log tuple. It must remain safe when the log queue is
unavailable or full, because it is used inside error paths.

## Firmware, installation, and non-Python assets

### Arduino example

[`Calibrated_sensor_output_timestamped.ino`](../arduino/Calibrated_sensor_output_timestamped/Calibrated_sensor_output_timestamped.ino)
uses FastIMU and an MPU6500 at address `0x68`. It optionally calibrates on
startup, configures I2C at 400 kHz and serial at 500000 baud, then emits the
seven-field CSV payload at a target 250 Hz (4 ms interval). The Python
application has different default serial settings (`COM3`, 115200), so users
must select the firmware's actual port and baud in the UI.

### Themes and images

`src/themes/light.qss` and `src/themes/dark.qss` are the application style sources.
`src/img/icon.ico` is used for native window/shortcut branding and
`src/img/orienta_logo.png` is displayed by the About panel and README. Treat them
as assets, not generated build output.

### Windows scripts

`install.bat` checks for Python, offers to recreate `.venv`, installs
`requirements.txt`, regenerates `launch_orienta.bat`, and optionally creates a
desktop shortcut. `launch_orienta.bat` changes to its own directory and invokes
the virtual-environment interpreter. Keep the scripts aligned with any entry
point or virtual-environment layout change.

## Documentation and dependency files

`README.md` is the end-user guide and describes hardware framing, setup, and
opentrack operation. `requirements.txt` currently declares NumPy, pyserial,
keyboard, PyQt5, and pygame. `LICENSE` is MIT; `.gitignore` excludes virtual
environments, caches, generated logs, preference files, and other local
artifacts.

## Refactor-aware maintenance notes

The recent UI refactor left compatibility vocabulary and broad best-effort
exception handling in several UI paths. These are not architectural APIs:

* “Tabbed” names remain although the main UI is now orientation-first and
  secondary views are dialogs.
* `calibration_panel` aliases `orientation_panel` because calibration UI moved
  into that panel.
* `MessagePanelQt` documentation references the previous tkinter behavior,
  although the implementation is PyQt5.
* Several UI/logging helpers intentionally swallow widget-destruction or
  optional-dependency failures. Do not copy that pattern into state-changing
  code; surface new command/configuration failures to the existing GUI message
  and log paths.

When simplifying any of this residue, search all imports and attribute uses,
then remove compatibility code and update both reference documents in the same
change.
