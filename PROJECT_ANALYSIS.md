# Orienta Project Analysis

This document is a maintained technical reference for contributors and agents.
It describes the system as observed after the migration to Orienta and before
any refactoring. Update it when an architectural contract changes.

## Purpose and operating environment

Orienta is a Windows-focused, real-time 3DOF head-tracking desktop application.
It receives accelerometer and gyroscope samples over a serial connection,
estimates yaw, pitch, and roll, optionally estimates X/Y position from a
single bright camera marker, and sends the resulting six degrees-of-freedom
packet to OpenTrack over UDP.

The supplied Arduino firmware targets an Arduino Nano with a FastIMU-supported
IMU (configured as MPU6500 at I2C address `0x68`). It emits a CSV record at a
target 250 Hz on a 500000 baud serial link:

```text
time_seconds,accel_x,accel_y,accel_z,gyro_x,gyro_y,gyro_z
```

The Python parser expects seven numeric CSV fields. Accelerometer values are
treated as g units and gyroscope values as degrees per second.

## Repository map

| Location | Responsibility |
|---|---|
| `orienta.py` | Command-line entry point and lifecycle owner. |
| `config/config.py` | Versioned application defaults and limits. |
| `config/config.cfg` | Ignored, machine-local user preferences written by the GUI. |
| `workers/process_man.py` | Creates queues/events, starts workers, monitors/restarts them, and writes logs. |
| `workers/serial_wrk.py` | Serial connection control and raw sample acquisition. |
| `workers/fusion_wrk.py` | Complementary orientation filters, calibration, drift handling, and combined output. |
| `workers/udp_wrk.py` | OpenTrack-compatible UDP serialization and send enablement. |
| `workers/camera_wrk.py` | Bright-marker tracking, preview production, and relative position output. |
| `workers/pseyepy_prov.py` | Isolated PS3 Eye capture subprocess and JPEG framing protocol. |
| `workers/input_wrk.py` | Global keyboard/gamepad shortcut capture and monitoring. |
| `workers/gui_wrk.py` | Qt main window, panel composition, queue draining, status routing, and preference lifecycle. |
| `workers/gui_qt/` | Qt panels, theme/preference managers, and icon/shortcut helpers. |
| `util/error_utils.py` | Queue, parsing, conversion, clamping, and angle helper functions. |
| `util/log_utils.py` | Best-effort worker-to-manager logging helper. |
| `themes/` | Application-wide Qt stylesheets. |
| `arduino/` | Reference firmware. |

No automated tests, packaging metadata, type-checker configuration, or CI
workflow were present at the time of analysis.

## Startup and shutdown

`orienta.py` requires Python 3.8 or later, warns for Python 3.14+, parses
`--diagnostics`, constructs `ProcessHandler`, starts workers, and waits on its
shared `stop_event`.

`ProcessHandler` creates all queues and starts these processes:

1. `InputWorker`
2. `GUIWorker`
3. `SerialWorker`
4. `FusionWorker`
5. `UDPWorker`
6. `CameraWorker`

The manager also owns:

- a daemon log-writer thread that appends to `orienta.log`, rotating it at
  5 MiB to `orienta_YYYYMMDD_HHMMSS.log`;
- a daemon worker monitor that checks every second, logs unexpected exits, and
  restarts a worker at most three times; and
- signal handlers for `SIGINT` and `SIGTERM`.

Closing the GUI sets the shared stop event. Shutdown then stops monitoring,
sets the event, waits briefly, terminates live worker processes, joins them,
and finally attempts to terminate leftover pseyepy camera child processes
recorded in the operating system temporary directory.

## Process and data-flow architecture

```text
Arduino/other IMU --serial CSV--> SerialWorker --serialQueue--> FusionWorker
                                                              |
CameraWorker --translationQueue------------------------------|
                                                              v
                                                    eulerQueue -> UDPWorker -> UDP/OpenTrack
                                                              |
                                                              +-> eulerDisplayQueue -> GUIWorker

GUIWorker -> serialControlQueue ------> SerialWorker
GUIWorker -> controlQueue ------------> FusionWorker
GUIWorker -> udpControlQueue ---------> UDPWorker
GUIWorker -> cameraControlQueue ------> CameraWorker
GUIWorker <-> inputCommand/Response --> InputWorker
all workers -> status/uiStatus/log/message/display queues -> GUI/manager
```

The design intentionally favors freshness rather than completeness:

- all multiprocessing queues are bounded;
- data queues default to 300 records, display queues to 60, and control queues
  to 10;
- workers generally drain several records and process only the newest; and
- non-blocking/best-effort queue writes drop data under load rather than
  increasing latency.

This behavior is essential to real-time responsiveness. Refactors must not
replace it with unbounded buffering or blocking cross-process writes without
an explicit latency analysis.

## Queue contracts

Queues carry Python lists, tuples, and strings rather than declared types.
These formats are cross-process contracts and must be preserved or migrated
atomically with every producer and consumer.

| Queue | Producer(s) | Consumer(s) | Payload contract |
|---|---|---|---|
| `serialQueue` | Serial worker | Fusion worker | Raw UTF-8 CSV sample string. |
| `serialDisplayQueue` | Serial worker | GUI worker | Raw CSV string for the message monitor. |
| `eulerQueue` | Fusion worker | UDP worker | `[yaw, pitch, roll, x, y, z]`, numeric. |
| `eulerDisplayQueue` | Fusion worker | GUI worker | Same orientation/position list; GUI shows latest only. |
| `translationQueue` | Camera worker | Fusion worker, UDP worker | Numeric `[x, y, z]` plus internal control tuples noted below. |
| `translationDisplayQueue` | Camera worker | GUI worker/camera panel | `_CAM_DATA_`, `_CAM_STATUS_`, or legacy numeric position payloads. |
| `cameraPreviewQueue` | Camera worker | GUI worker/camera panel | JPEG preview bytes. |
| `serialControlQueue` | GUI | Serial worker | `('start', port, baud)`, `('stop',)`. |
| `controlQueue` | GUI, input-flow bridge | Fusion worker | Orientation, calibration, filter, and axis-inversion commands. |
| `udpControlQueue` | GUI | UDP worker | `('set_udp', host, port)`, `('udp_enable', bool)`. |
| `cameraControlQueue` | GUI, calibration panel | Camera worker | Preview, camera, and marker-tracking commands. |
| `inputCommandQueue` | GUI panels | Input worker | Shortcut setup/clear/capture and manual reset trigger commands. |
| `inputResponseQueue` | Input worker | GUI panels/GUI worker | Captured input and shortcut-trigger notifications. |
| `statusQueue` | Most workers | GUI worker | `(status_name, value)` telemetry/state. |
| `uiStatusQueue` | Serial/fusion workers | GUI worker | UI-specific `(status_name, value)` events. |
| `messageQueue` | Serial worker | GUI worker | Human-readable strings. |
| `logQueue` | All workers | Process manager | `(level, worker_name, message)`. |

### Internal translation controls

`translationQueue` multiplexes numeric positions with control tuples:

- `('_POS_ENABLE_', bool)` enables/disables fusion-side relative-position
  handling.
- `('_CAM_ORIGIN_', x, y, z)` establishes a camera origin; `CAM_ORIGIN` is
  also accepted by fusion.

Both fusion and UDP explicitly skip string-first tuples when seeking position
data. New translation messages must retain that discrimination rule or move to
a separate queue in one coordinated change.

### GUI status vocabulary

Observed status names include `processing`, `serial_connection`, `serial_data`,
`msg_rate`, `send_rate`, `cam_fps`, `stationary`, `drift_correction`,
`gyro_calibrating`, `gyro_calibrated`, `center_calibrating`,
`center_calibrated`, `filter_type`, `camera_crashed`, and `cam_origin_ack`.
The GUI routes them to its serial, calibration, orientation, hold-still, and
status-bar components.

## Sensor acquisition and fusion

### Serial worker

The serial worker is idle until it receives `('start', port, baud)`. Connection
attempts retry every two seconds and can be cancelled by `('stop',)` or the
shared stop event. On each readable line it decodes UTF-8 with invalid bytes
ignored, strips it, publishes the raw string to both serial queues, reports
data activity, and reports message rate once per second.

Connection loss closes the port and reports an error, but does not autonomously
reopen it; the GUI must send a new start command.

### Fusion behavior

`fusion_wrk.py` contains both Euler and quaternion complementary-filter
implementations. The active filter can be changed at runtime. Each filter:

1. parses the latest serial sample;
2. computes `dt` from the sample timestamp and rejects duplicate/too-small
   intervals (`< 0.001 s`) and gaps larger than `0.1 s`;
3. integrates gyroscope angular velocity;
4. blends accelerometer-derived roll/pitch only when acceleration magnitude is
   within the configured gravity tolerance;
5. treats yaw as gyro-derived because the hardware input has no magnetometer;
6. detects stationary state using valid gravity and gyro magnitude below the
   configured threshold for a debounce duration; and
7. applies gradual drift correction only when stationary and inside independent
   yaw/pitch/roll center thresholds.

The quaternion implementation represents orientation as `[w, x, y, z]`,
normalizes after gyro integration, extracts Euler angles for display/output,
and applies accelerometer roll/pitch blending by converting the blended state
back into a quaternion. Its drift correction uses spherical interpolation and
supports exponential, linear, cosine, and quadratic curves.

Gyro-bias calibration collects stationary samples and estimates yaw bias.
Level calibration collects stationary accelerometer samples and stores
roll/pitch center offsets. `reset_orientation` retains calibration, reseeds
from a recent accelerometer sample where possible, schedules level
recalibration, and resets the relative position origin. `reset` clears the
runtime calibration state.

Fusion emits the most recent `[yaw, pitch, roll, x, y, z]` to the UDP and
display queues. Axis inversion is applied at output.

### Fusion control vocabulary

The fusion worker accepts:

- `reset_orientation`, `reset`;
- `set_center_threshold` and per-axis
  `set_center_threshold_yaw`, `_pitch`, `_roll`;
- `set_threshold`;
- `set_alpha_yaw`, `_pitch`, `_roll`;
- `set_drift_smoothing_time`, `set_drift_curve_type`,
  `set_drift_correction_strength`;
- `set_invert_yaw`, `_pitch`, `_roll`;
- `set_filter_type`;
- `recalibrate_gyro_bias` with an optional sample count; and
- `calibrate_level` with an optional sample count.

Control handling and recalibration consume from `serialQueue`; calibration
therefore temporarily takes ownership of arriving sensor samples.

## Camera position tracking

The camera worker initializes its provider only while preview or position
tracking is requested. It currently defaults to the isolated `pseyepy`
backend. It converts each BGR frame to grayscale, thresholds brightness,
selects the largest connected contour above `MIN_BLOB_AREA`, maps its center
relative to the frame center to X/Y, low-pass filters the result, and clamps
it to the configured output range. Z is always `0.0`.

Camera controls:

- `preview_on`, `preview_off`;
- `start_pos`, `stop_pos`;
- `latch_origin`;
- `set_thresh`, `set_scale`;
- `set_cam_params(width, height, fps)`, `set_cam(index)`;
- `set_exposure`, `set_gain`, `set_cam_setting(name, value)`;
- `set_backend`, `calibrate`, and `close_cam`.

When tracking starts, the first marker is captured as a local origin. The
worker communicates the origin to fusion and sends relative values thereafter.
If no marker is visible, the last value remains usable for
`STALE_DETECTION_TIMEOUT` seconds, then marker-lost status is emitted.
Translation publication is capped at approximately 50 Hz unless movement
changes by more than 0.01.

`PSEyeProvider` launches a temporary Python wrapper in a subprocess to
isolate native camera crashes. The child imports `pseyepy`, captures frames,
JPEG-encodes them, and writes a binary stream:

```text
uint32_le jpeg_length | float64_le timestamp | jpeg bytes
```

The parent parses that stream on a reader thread and supplies BGR images to
the camera worker. Changing camera parameters restarts the provider.

## UDP/OpenTrack output

The UDP worker consumes only the latest orientation record and caches the most
recent numeric translation. It does not transmit until the GUI sends
`('udp_enable', True)`. `('set_udp', host, port)` updates the target.

It sends six little-endian doubles:

```text
struct.pack('<6d', tx, ty, tz, yaw, pitch, roll)
```

This translation-first ordering is intentional for OpenTrack compatibility,
even though the internal fusion payload is rotation-first. Translation falls
back to zero after its stale timeout.

## GUI and preferences

`TabbedGUIWorker` owns a PyQt5 application window with:

- Orientation Tracking: serial connection, calibration, orientation display,
  and network panels;
- Camera;
- optional Diagnostics, visible only with `--diagnostics`;
- Messages;
- Preferences; and
- About.

The GUI drains queues frequently but displays only the newest item where
appropriate. It persists settings on close through `PreferencesManager`.
`config/config.cfg` has `serial`, `network`, `orientation`, `calibration`,
`camera`, and `gui` sections. It is deliberately ignored because it contains
machine/user-specific port, controller, and GUI choices.

The preferences manager writes atomically via a temporary file followed by
`os.replace`, with fallback replacement behavior. Themes are whole-application
stylesheets loaded from `themes/light.qss` and `themes/dark.qss`.

The input worker owns keyboard and pygame joystick listeners. It supports
reset-orientation and drift-disengage shortcut actions, captures either
keyboard or gamepad input, and reports shortcut events back to the GUI flow.
The optional diagnostics panel uses a lazy matplotlib import, retains 1000
orientation samples, and refreshes plots at 10 Hz only when enabled.

## Configuration sources and precedence

`config/config.py` supplies code defaults: queue capacities, timing,
serial/UDP defaults, filter defaults, camera limits, UI timings, log identity,
and application identity. GUI-loaded values in `config/config.cfg` override
many of those settings by issuing worker control commands at startup or after
the user changes a control.

Do not place mutable session state in `config/config.py`. Do not commit a
personal `config/config.cfg`; use a sanitized example if configuration sharing
becomes necessary.

## Refactoring constraints and observed technical debt

These observations are not requested fixes. They identify behavior that a
future change must deliberately preserve or test.

1. **Untyped, multiplexed IPC is the highest-risk boundary.** Queue contents
   are dynamically shaped and several queues carry both telemetry and control
   traffic. Add explicit dataclasses/protocols only with a coordinated
   producer-and-consumer migration.
2. **Freshness is a product feature.** Bounded queues, non-blocking writes,
   draining loops, and frame drops limit latency. Throughput improvements must
   be evaluated against latency and control-message delivery.
3. **The fusion module is large and stateful.** Filter math, runtime command
   handling, calibration loops, translation-origin handling, queue publication,
   and UI state reporting coexist in one file. Extract only behind tests with
   recorded IMU fixtures and exact output expectations.
4. **Time bases must remain compatible.** Filter `dt` uses the MCU timestamp,
   while stale detection/rates use host `time.time()`. Do not interchange the
   two without accounting for their domains.
5. **Camera tracking has two relative-origin layers.** Camera and fusion each
   manage origins. Preserve their `_POS_ENABLE_` and `_CAM_ORIGIN_` handshake
   before modifying position reset behavior.
6. **Camera isolation is intentional.** The pseyepy subprocess, debug files,
   and shutdown PID-file cleanup protect the main program from native crashes.
   Any simplification must prove equivalent failure containment on Windows.
7. **Error handling is frequently best-effort.** Numerous broad catches and
   dropped log/status messages prevent real-time workers from crashing, but can
   obscure faults. Improve observability one boundary at a time; do not make a
   diagnostic path block sensor processing.
8. **Configuration defaults are not fully centralized at runtime.** Some
   workers and panels contain local defaults or UI ranges. When changing a
   setting, trace `config.py`, preference serialization, panel initialization,
   GUI load/apply behavior, worker command validation, and runtime behavior.
9. **The GUI has legacy remnants.** Several docstrings and comments reference
   former Tkinter/legacy behavior, and preference collection includes a
   duplicate shortcut-preference merge. Treat cleanup as a separate,
   behavior-preserving task.
10. **There is no automated behavioral safety net.** Before substantive
    refactoring, introduce focused tests for IMU parsing, both filters,
    command handling, UDP byte order, preference round trips, and queue
    contract validation.

## Recommended verification baseline for future work

At minimum, run:

```powershell
python -m compileall -q .
python orienta.py --help
```

For behavioral changes, additionally test with deterministic recorded IMU
lines and a local UDP receiver; avoid requiring physical serial/camera
hardware in unit tests. Manually verify serial connection, startup
calibration, reset, UDP enablement, and the camera preview/tracking toggle
when changing their respective paths.
