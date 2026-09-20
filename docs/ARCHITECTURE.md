# Orienta runtime architecture

This document describes the runtime design of Orienta as it exists in this
repository. It is written for maintainers and AI assistants changing the
application. Read it before changing worker signatures, queue payloads,
orientation conventions, or GUI-to-worker commands.

## Purpose and boundaries

Orienta is a Windows 3-DOF head tracker. An IMU connected over a serial port
emits CSV samples. The application estimates yaw, pitch, and roll, displays
them in a PyQt5 interface, and sends the resulting angles to opentrack via
UDP. It does not estimate translation and it does not use a magnetometer, so
yaw is gyro-integrated and is expected to drift over time.

The application runs five child processes, plus two management threads in the
parent process:

```text
orienta.py (parent process)
  ProcessHandler
    log-writer thread
    worker-monitor thread
    InputWorker process
      command-loop thread
      optional keyboard-listener thread
      optional gamepad-listener thread
    GUIWorker process (Qt event loop and QTimers)
    SerialWorker process
    FusionWorker process
    UDPWorker process
```

On Windows, `multiprocessing` uses spawn semantics. Worker entry points must
therefore remain importable top-level functions, and all queues/events passed
to a process must be picklable.

## Startup, ownership, and shutdown

1. [`orienta.py`](../orienta.py) validates the Python version, constructs
   `ProcessHandler`, and calls `start_workers()`.
2. [`ProcessHandler`](../workers/process_man.py) creates every
   `multiprocessing.Queue` and the one shared `Event`. It starts its log writer
   and monitoring threads before starting the five workers.
3. The GUI process owns all Qt widgets. It loads preferences and starts its
   event loop; it must never be manipulated from another process.
4. The parent main loop waits on `stop_event`. A GUI close, Ctrl+C, or handled
   OS signal causes shutdown.
5. `stop_workers()` first stops monitoring, sets the shared event, gives
   workers 0.5 seconds to leave naturally, then calls `terminate()`, `join()`,
   and finally `kill()` if a worker remains alive. It is guarded by a
   non-blocking lock to make repeated shutdown requests safe.

`ProcessHandler` monitors its `workers` list roughly once per second. A dead
worker is restarted from its stored target/args up to
`MAX_WORKER_RESTART_ATTEMPTS` (three). Native access-violation exit codes are
not restarted to avoid a crash loop. A restarted worker gets the existing
queues and event; no application state is reconstructed for it.

## Data-flow diagram

```text
 IMU firmware
     | CSV: time,ax,ay,az,gx,gy,gz
     v
 SerialWorker
     | serialQueue (data, 300) ---------------------+
     | serialDisplayQueue (display, 60)             |
     v                                               v
 GUIWorker -> MessagePanel (raw serial)        FusionWorker
                                                   | eulerQueue (data, 300)
                                                   | eulerDisplayQueue (display, 60)
                                                   v              v
                                              UDPWorker        GUIWorker
                                                   |              |
                                   six little-endian doubles     orientation/UI state
                                                   v
                                                opentrack
```

The duplicate queues are intentional:

* **Pipeline queues** (`serialQueue`, `eulerQueue`) feed correctness-critical
  consumers. A full queue drops a current frame rather than blocking the
  real-time producer.
* **Display queues** are best-effort. The GUI drains them and retains only the
  most recent sample, avoiding growing display latency.
* **Control/status queues** carry infrequent state changes and are deliberately
  small (10 or 60 entries).

Do not introduce a second consumer for an existing `multiprocessing.Queue`
when both consumers need every message. A queue distributes messages among
consumers rather than broadcasting them. Add an explicitly owned duplicate
queue instead.

## Queue contract

All queues are created by `ProcessHandler`; their configuration defaults are
in [`config/config.py`](../config/config.py).

| Queue | Writer(s) | Reader(s) | Payload / meaning |
|---|---|---|---|
| `serialQueue` | Serial worker | Fusion worker | Raw IMU CSV string |
| `eulerQueue` | Fusion worker | UDP worker | `[yaw, pitch, roll]` floats in degrees |
| `serialDisplayQueue` | Serial worker | GUI worker | Raw IMU CSV string; display only |
| `eulerDisplayQueue` | Fusion worker | GUI worker | `[yaw, pitch, roll]`; display only |
| `controlQueue` | GUI/Orientation/Preferences | Fusion worker | Fusion commands described below |
| `serialControlQueue` | Connection panel | Serial worker | `('start', port, baud)` or `('stop',)` |
| `udpControlQueue` | Connection panel | UDP worker | `('set_udp', host, port)` and `('udp_enable', bool)` |
| `statusQueue` | Serial, fusion, UDP workers | GUI worker | `(status_name, value)` worker state/rates |
| `uiStatusQueue` | Serial and fusion workers | GUI worker | UI-specific `('serial_connection', state)` or `('processing', state)` |
| `messageQueue` | Serial worker | GUI worker | Human-readable connection/reconnection text |
| `inputCommandQueue` | GUI panels | Input worker | Input commands described below |
| `inputResponseQueue` | Input worker | Orientation panel | Captured keys and shortcut state transitions |
| `logQueue` | All processes | Parent log-writer thread | `(level, worker_name, message)` |

The code uses `safe_queue_put` for most cross-process writes. It attempts a
non-blocking write first and then a very short timed write. Failure means the
payload was dropped; control senders should treat `False` as an observable
failure, rather than assuming a command was applied.

## Serial input contract

[`serial_wrk.py`](../workers/serial_wrk.py) owns pyserial. It is idle until it
receives `('start', port, baud)`. Opening retries every
`SERIAL_RETRY_DELAY` seconds and can be cancelled by a stop command or the
shared event. Once open, each non-empty UTF-8 line is:

1. forwarded to `serialQueue`,
2. copied to `serialDisplayQueue`,
3. used to report throttled data activity and per-second message rate.

Its expected format is:

```text
timestamp,accel_x,accel_y,accel_z,gyro_x,gyro_y,gyro_z
```

The timestamp is in seconds; acceleration is in g; gyro values are degrees per
second. `parse_imu_line` rejects negative timestamps, non-finite values (NaN/Infinity),
acceleration over 10 g, and gyro values outside +/-2000 deg/s. Keep coordinate-system conversions
centralized in fusion; the serial worker deliberately transports raw payloads.

## Fusion contract

[`QuaternionComplementaryFilter`](../workers/fusion_wrk.py) represents
orientation as a normalized `[w, x, y, z]` quaternion. Its public update
result is:

```text
(yaw, pitch, roll, drift_active, is_stationary)
```

`FusionWorker` parses raw samples, calls `update`, validates that the resulting
Euler angles are finite (rejecting NaN/Infinity), drops any non-finite frames with
rate-limited logging, places valid Euler `[yaw, pitch, roll]` values into both
Euler queues, and reports state changes. Its processing flow is:

1. First valid timestamp creates a timing baseline and emits zero orientation.
2. Reject non-positive/too-small `dt`; retain the existing quaternion.
3. On a `dt` greater than `DT_MAX`, reset only the timing baseline.
4. Integrate gyro angular velocity into the quaternion. `gyro_bias_yaw` is
   subtracted from `gz`.
5. If acceleration magnitude is close enough to 1 g, calculate accelerometer
   roll/pitch and blend those with quaternion-derived roll/pitch using the
   independent pitch/roll alphas. Yaw remains gyro-only.
6. Call a pose stationary only when valid acceleration and gyro magnitude
   below `STATIONARY_GYRO_THRESHOLD` persist for
   `STATIONARY_DEBOUNCE_S`.
7. While stationary **and** within all three configured centre thresholds,
   smoothly pull the orientation toward centre offsets. The selected
   exponential, linear, cosine, or quadratic curve determines its gradual
   correction rate.
8. Subtract centre offsets from the final normalized Euler output. Apply axis
   inversion in the worker's output path.

`reset_orientation` preserves the gyro-bias calibration. It preferentially
seeds the new quaternion from the most recent acceleration sample to avoid a
visible pitch jump, schedules a short level calibration, and falls back to an
identity reset when no valid sample is available.

### Fusion control protocol

All values sent to `controlQueue` must be validated before adding new commands.
Existing commands are:

| Command | Effect |
|---|---|
| `'reset_orientation'` / `('reset_orientation',)` | Recenter with accel seeding; schedule level calibration |
| `'reset'` / `('reset',)` | Reset filter and calibration/UI status |
| `('set_center_threshold', degrees)` | Set the shared near-centre threshold |
| `('set_threshold', yaw, pitch, roll)` | Set all independent thresholds |
| `('set_center_threshold_yaw/pitch/roll', degrees)` | Set one threshold |
| `('set_alpha_yaw/pitch/roll', alpha)` | Set filter blend alpha (`0..1`) |
| `('set_drift_smoothing_time', seconds)` | Set positive smoothing duration |
| `('set_drift_curve_type', name)` | `exponential`, `linear`, `cosine`, or `quadratic` |
| `('set_drift_correction_strength', strength)` | Set strength in `(0, 1]` |
| `('set_invert_yaw/pitch/roll', bool)` | Set axis inversion |
| `('recalibrate_gyro_bias', samples?)` | Gather valid stationary samples and update yaw bias |
| `('calibrate_level', samples?)` | Gather valid stationary samples and set pitch/roll centre offsets |

Gyro and level calibration consume `serialQueue` while they collect samples.
That is intended, but means normal orientation output pauses during calibration.

## Output, UI, and input contracts

### UDP

[`udp_wrk.py`](../workers/udp_wrk.py) drains up to ten Euler samples at a time
and sends only the latest. UDP is disabled at worker start and enabled only by
`('udp_enable', True)`. Each packet is exactly:

```python
struct.pack("<6d", 0.0, 0.0, 0.0, yaw, pitch, roll)
```

It uses six little-endian doubles, translation first, which is the opentrack
UDP ordering. Do not send `[yaw, pitch, roll]` directly without preserving the
three leading zero translations.

### GUI

[`gui_wrk.py`](../workers/gui_wrk.py) is a Qt process. Its 25 ms queue timer
drains bounded batches from display/status/message queues; its 16 ms GUI timer
refreshes widgets. It owns the single-window layout:

* `OrientationPanelQt` is the primary screen and owns calibration, recenter,
  drift-disengage, shortcut, visualization, and pop-up dialog behavior.
* `ConnectionPanelQt` starts/stops serial and UDP and displays rates/state.
* `MessagePanelQt`, `PreferencesPanel`, and `AboutPanel` are shared instances
  opened in dialogs rather than persistent tabs.

The GUI consumes latest Euler samples as `[yaw, pitch, roll]`; preserve that
ordering even though some signal names/documentation refer to roll first.
Qt signals and timers are the in-process UI synchronization mechanism; do not
read a multiprocessing queue from a background Qt thread unless ownership and
widget access are redesigned together.

### Input worker

[`input_wrk.py`](../workers/input_wrk.py) wraps optional `keyboard` and
`pygame` global input APIs. Its process has a command-loop thread and starts
keyboard/gamepad listener threads only when a shortcut or capture mode needs
them.

Commands: `('set_shortcut', key, display_name, action)`,
`('clear_shortcut', action?)`, `('start_capture',)`, `('stop_capture',)`,
and `('trigger_reset',)`. Responses include
`('input_captured', key, display_name)`,
`('shortcut_pressed', key, action)`, and
`('shortcut_released', key, action)`.

The current actions are `reset_orientation` and `disengage_drift`. The
orientation panel defines whether disengagement is hold or toggle behavior,
then changes fusion thresholds accordingly.

## Logging and error behavior

`util.log_utils` is the normal cross-process logging API. It sends a tuple to
`logQueue`; the parent `_log_writer` timestamps and appends it to
`orienta.log`, rotating the file at 5 MB. Logging is best-effort: a full log
queue drops entries rather than stalling real-time work.

`util.error_utils` supplies the shared queue helpers, IMU parser, bounds
helpers, and queue-health reports. The process monitor checks queue health
every ten seconds and emits warnings only for warning/critical queues.

Status names currently handled by the GUI include serial connection/data
activity, message/send rate, `processing`, `drift_correction`, `stationary`,
and gyro/centre calibration states. When adding a status name, update both the
producer and `TabbedGUIWorker._handle_status_update`; a producer-only status
silently has no UI effect.

## Persistence and assets

`PreferencesManager` reads/writes `config/config.cfg`, which is a runtime
file rather than a tracked source file. It uses a `.tmp` file and
`os.replace()` to avoid partial writes. Preferences include serial/network
values, theme, orientation settings, calibration settings, and shortcuts.
Themes are QSS files in `themes/`; application images are in `img/`.

## Change safety checklist

Before changing this design:

1. Trace the queue's single writer and all readers; do not assume queues
   broadcast.
2. Preserve `[yaw, pitch, roll]` throughout the pipeline and `<6d` UDP
   serialization.
3. Keep the GUI as the sole Qt-widget owner process.
4. Make every blocking loop observe `stop_event`, use bounded queue waits, and
   close external resources in `finally`.
5. Update preferences load, save, and fusion command application together for
   new persisted controls.
6. Update this document and `CODEBASE_REFERENCE.md` when a queue, command,
   process, or persistent preference changes.
