# Orienta runtime architecture

This document describes the runtime design of Orienta as it exists in this
repository. It is written for maintainers and AI assistants changing the
application. Read it before changing worker signatures, queue payloads,
orientation conventions, or GUI-to-worker commands.

## Purpose and boundaries

Orienta is a Windows 3-DOF head tracker. An IMU connected over a serial port
emits CSV samples. The application estimates yaw, pitch, and roll, displays
them in a PyQt5 interface, and publishes the resulting angles through one
selected output: UDP to opentrack, direct FreeTrack 2.0, or NPClient. It
does not estimate translation and it does not use a magnetometer, so yaw is
gyro-integrated and is expected to drift over time.

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
    OutputWorker process
```

On Windows, `multiprocessing` uses spawn semantics. Worker entry points must
therefore remain importable top-level functions, and all queues/events passed
to a process must be picklable.

## Startup, ownership, and shutdown

1. [`orienta.py`](../orienta.py) validates the Python version, constructs
   `ProcessHandler`, and calls `start_workers()`.
2. [`ProcessHandler`](../src/workers/process_man.py) creates every
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
not restarted to avoid a crash loop. A restarted worker gets the existing queues and event; the process manager
does not reconstruct its internal state. When OutputWorker reports `ready`,
the GUI resends the selected protocol, UDP rate cap, and native processing
settings; other workers do not have a generic state-restore mechanism.

## Data-flow diagram

```text
IMU firmware --serialQueue--> SerialWorker --serialQueue--> FusionWorker
                                  |                           |
                                  +--serialDisplayQueue       +--outputQueue--> OutputWorker
                                  |                           |                     |
                                  v                           +--eulerDisplayQueue  +--UDP
                         GUI / MessagePanel                                      `--FT_SharedMem
                                                                                      |
                                                                        +-------------+-------------+
                                                                        |                           |
                                                               FreeTrack client DLL         NPClient DLL
                                                                        |                           |
                                                                FreeTrack games             TrackIR games
```

`OutputWorker` also reports its active backend and send rate to the GUI over
`statusQueue`.

The duplicate queues are intentional:

* **Pipeline queues** (`serialQueue`, `outputQueue`) carry correctness-critical
  samples. The output queue has one consumer; OutputWorker drains stale pose
  backlog to the latest sample so scheduling delays do not accumulate tracking
  latency.
* **Display queues** are best-effort. The GUI drains them and retains only the
  most recent Euler sample for the orientation display; serial monitor data is
  drained in bounded batches so the GUI does not retain an unbounded backlog.
* **Control/status queues** carry infrequent state changes and are deliberately
  small (10 or 60 entries).

Do not introduce a second consumer for an existing `multiprocessing.Queue`
when both consumers need every message. A queue distributes messages among
consumers rather than broadcasting them. Add an explicitly owned duplicate
queue instead.

## Queue contract

All queues are created by `ProcessHandler`; their configuration defaults are
in [`src/config/config.py`](../src/config/config.py).

| Queue | Writer(s) | Reader(s) | Payload / meaning |
|---|---|---|---|
| `serialQueue` | Serial worker | Fusion worker | Raw IMU CSV string |
| `outputQueue` | Fusion worker | Output worker | `[yaw, pitch, roll]` degrees; one selected protocol at a time |
| `serialDisplayQueue` | Serial worker | GUI worker | Raw IMU CSV string; display only |
| `eulerDisplayQueue` | Fusion worker | GUI worker | `[yaw, pitch, roll]`; display only |
| `controlQueue` | GUI/Orientation/Preferences | Fusion worker | Fusion commands described below |
| `serialControlQueue` | Connection panel | Serial worker | `('start', port, baud)` or `('stop',)` |
| `outputControlQueue` | GUI panels | Output worker | `('set_protocol', protocol)`, `('set_udp', host, port)`, `('set_enabled', bool)`, `('set_rate', hz)`, `('set_native_processing', settings)` |
| `statusQueue` | Serial, fusion, output workers | GUI worker | `(status_name, value)` worker state/rates |
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

[`serial_wrk.py`](../src/workers/serial_wrk.py) owns pyserial. It is idle until it
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

[`QuaternionComplementaryFilter`](../src/workers/fusion_wrk.py) represents
orientation as a normalized `[w, x, y, z]` quaternion. Its public update
result is:

```text
(yaw, pitch, roll, drift_active, is_stationary)
```

`FusionWorker` drains all queued raw samples and parses and calls `update` for
each one, because skipping samples degrades gyro integration. Only the final
pose of each batch is published. The worker validates that the resulting Euler
angles are finite (rejecting NaN/Infinity), drops any non-finite frames with
rate-limited logging, places valid Euler `[yaw, pitch, roll]` values into both
Euler queues, and reports state changes. Its processing flow is:

1. First valid timestamp creates a timing baseline and emits zero orientation.
2. Reject non-positive/too-small `dt`; retain the existing quaternion.
3. On a `dt` greater than `DT_MAX`, reset only the timing baseline.
4. Subtract the stationary-estimated raw sensor biases `gyro_bias_roll`,
   `gyro_bias_pitch`, and `gyro_bias_yaw` from `gx`, `gy`, and `gz`, respectively,
   then integrate angular velocity into the quaternion. These names identify
   sensor axes, not independent Euler angular rates. The quaternion lives in
   the IMU's right-handed frame, which the accelerometer and gyro share, so
   all three gyro axes are integrated unmodified. The application's yaw sign
   is the opposite of the IMU's, so `_euler_from_quat`/`_quat_from_euler`
   negate yaw at the Euler boundary. Negating `gz` during integration would
   mirror the gyro frame and turn yaw into pitch/roll on a tilted sensor.
5. Correct tilt with the accelerometer as body-rate feedback (Mahony-style).
   The error is the cross product of the normalized acceleration and the
   quaternion's predicted gravity direction. It is low-passed
   (`ACCEL_INNOVATION_CUTOFF_HZ`) and weighted by how close `|a|` is to 1 g and
   by a peak-held head rotation rate (`ACCEL_TRUST_GYRO_RATE_DPS`, decaying
   over `ACCEL_TRUST_HOLD_S`). This rejects vibration, linear accelerations,
   and the centripetal/tangential acceleration of an off-axis sensor during
   turns, including at turn reversals where the rate briefly crosses zero. Gains are `1/tau`, where tau is the per-axis time constant in
   seconds (`ACCEL_TAU_PITCH`/`ACCEL_TAU_ROLL`), so correction strength does not
   depend on sample rate; `tau = inf` disables it. A clamped integral term
   (`GYRO_BIAS_TRACKING_*`) tracks residual gyro bias. It stops learning while
   center assist is engaged or the tilt error is large, because that
   disagreement is not gyro bias and would otherwise wind up into drift. Yaw remains gyro-only. The first valid sample
   seeds tilt from the accelerometer.
6. Call a pose stationary only when valid acceleration and bias-corrected gyro magnitude
   below `STATIONARY_GYRO_THRESHOLD` persist for
   `STATIONARY_DEBOUNCE_S`.
7. While stationary **and** within all three configured centre thresholds,
   smoothly pull the orientation toward centre offsets. The selected
   exponential, linear, cosine, or quadratic curve ramps up engagement.
   Correction uses `1 - exp(-integrated_rate)` per sample, integrating the
   ramp over sensor time to avoid sample-rate-dependent gains. After the ramp
   completes, attraction continues at a sustained rate of
   `(strength / 0.3) / smoothing_time`; smoothing time is not a completion
   deadline. Correction disengages on motion or leaving the centre thresholds.
   This is centre assistance, not an absolute heading reference, and may pull
   small intentional held glances inward.
8. Subtract centre offsets from the final normalized Euler output. Apply axis
   inversion in the worker's output path.

The legacy `reset_orientation` command preserves gyro-bias calibration. It
seeds the quaternion from the most recent acceleration sample and schedules a
short level calibration, falling back to identity when no valid sample is
available. The Reset Orientation UI instead uses
`recenter_orientation_to_current` to set the current full yaw/pitch/roll pose
as the output origin. By default, it sends the command when the button/key is
released; checking **Instantaneous recenter** sends it on click/key press
instead. Neither mode changes calibrated center offsets. Stationary center
assistance uses the effective output origin as its target, so it remains active
after full-pose recentering. While the input is held, the GUI draws a dotted
gray guide matching the configured drift-angle region around the live
orientation marker.

### Fusion control protocol

All values sent to `controlQueue` must be validated before adding new commands.
Existing commands are:

| Command | Effect |
|---|---|
| `'reset_orientation'` / `('reset_orientation',)` | Legacy accel-seeded reset; preserve gyro-bias calibration and schedule level calibration |
| `('recenter_orientation_to_current',)` | Set current full yaw/pitch/roll pose as the output origin, preserving level calibration |
| `'reset'` / `('reset',)` | Reset filter and calibration/UI status |
| `('set_orientation_hold', bool)` | Freeze published orientation while sensor fusion continues; on release, slerp smoothly back to the live orientation |
| `('set_center_threshold', degrees)` | Set the shared near-centre threshold |
| `('set_threshold', yaw, pitch, roll)` | Set all independent thresholds |
| `('set_center_threshold_yaw/pitch/roll', degrees)` | Set one threshold |
| `('set_accel_tau_pitch/roll', seconds)` | Set accelerometer tilt-correction time constant (`> 0`) |
| `('set_drift_smoothing_time', seconds)` | Set positive smoothing duration |
| `('set_drift_curve_type', name)` | `exponential`, `linear`, `cosine`, or `quadratic` |
| `('set_drift_correction_strength', strength)` | Set strength in `(0, 1]` |
| `('set_invert_yaw/pitch/roll', bool)` | Set axis inversion |
| `('recalibrate_gyro_bias', samples?)` | Gather valid stationary raw samples and update all three X/Y/Z gyro biases |
| `('calibrate_level', samples?)` | Gather valid stationary samples and set pitch/roll centre offsets |

Gyro and level calibration consume `serialQueue` while they collect samples.
That is intended, but means normal orientation output pauses during calibration.
Gyro calibration averages raw readings, not previously bias-corrected readings,
so repeated calibration replaces rather than compounds the estimate. Recenter
preserves all three gyro biases; full reset clears them.

## Output, UI, and input contracts

### Output worker and backends

[`output_wrk.py`](../src/workers/output_wrk.py) owns the single bounded pose
queue and dispatches each latest pose to exactly one backend. Protocol, enablement, UDP target, rate-limit, and native-processing commands
all use `outputControlQueue`.
This keeps transport scheduling, backend lifecycle, and status reporting in
one place while the packet and shared-memory mechanisms remain focused backend
modules. On worker restart, the GUI resends the selected protocol, rate cap,
and native processing settings.

[`udp_output.py`](../src/outputs/udp_output.py) owns the UDP socket and
serializes opentrack packets. UDP is disabled at worker start and enabled only
by `('set_enabled', True)` with the UDP protocol selected. Sends default to
uncapped (matching fusion's raw output rate) and can optionally be capped via
`('set_rate', hz)`, clamped to
`OUTPUT_RATE_MIN_HZ..OUTPUT_RATE_MAX_HZ`; a rate of `0` restores uncapped
sending. Each packet is exactly:

```python
struct.pack("<6d", 0.0, 0.0, 0.0, yaw, pitch, roll)
```

The packet uses six little-endian doubles, translation first, which is
opentrack's UDP ordering. Do not send `[yaw, pitch, roll]` directly without
preserving the three leading zero translations.

### FreeTrack 2.0 and NPClient

The Output panel selects exactly one protocol. Fusion publishes every
valid pose to `outputQueue`; the OutputWorker dispatches it only to the
selected backend. Protocol choice, UDP destination/rate, and enabled state all
travel over a single `outputControlQueue`; the shared-memory backend owns its
Windows mapping only while either direct protocol is enabled.
Changing the output while Orienta runs switches backends in queue order.
Games may cache the game-facing DLL they loaded at startup, so changing
between FreeTrack and NPClient can require restarting the game.

For FreeTrack and NPClient only, `OutputWorker` maps yaw, pitch, and roll
magnitudes through configured shape-preserving cubic curves spanning each
axis's selected 30°/45°/60°/75°/90° input limit to at most 180° output.
The selected filter from `src/filters/` runs on the input angles before curve
mapping; Gyro-assisted (IMU) is the default native filter. One Euro, EMA,
hysteresis jitter suppression, a critically damped filter, and an
Accela-inspired smoother are also available. Fusion publishes each pose as
`[yaw, pitch, roll, yaw_rate, pitch_rate, roll_rate]`, where the rates are
gyro-derived Euler
rates in deg/s with the same sign conventions as the angles (zero while the
orientation is held). The worker forwards them to filters that set
`accepts_rates`; the gyro-assisted filter uses them to smooth heavily only
while the head is still and to pass movement through with little lag.
Changing an axis limit rescales its curve control points. The curve's final
output is vertically adjustable. The worker then adds rotation and translation
offsets; final rotations are clamped to ±180°.
Translation offsets are millimeters in the FreeTrack shared-memory coordinate
axes. The native settings
are sent as one validated `set_native_processing` command and persist in the
`native_output` preferences section. The Native Output Settings button is
available only while a direct protocol is selected. UDP bypasses this
processing and continues to forward the fusion angles unchanged.

The Native Output panel can calibrate yaw and pitch limits with topmost,
borderless prompts on the primary display while a center target remains
visible. It captures left/right yaw and upper/lower pitch, uses the smaller
absolute capture for each symmetric axis limit, and stores those two results
as custom values. Roll remains manually set.

[`shared_memory_output.py`](../src/outputs/shared_memory_output.py) is the
focused shared-memory backend. It opens the Windows `FT_SharedMem` mapping and
`FT_Mutext` mutex only while direct output is enabled. It publishes the
fixed-width `FTHeap` structure, uses radians and FreeTrack yaw/pitch axis signs,
and writes translation in millimeters. The initial implementation mirrors the
published pose into the raw-pose fields because the fusion pipeline does not
expose a distinct raw pose. A pre-existing mapping is reused without resetting
its frame counter because game readers may keep the mapping alive while output
is stopped. A separate Orienta-only mutex prevents a second Orienta process
from publishing concurrently. The publisher responds to a new `GameID` by
looking it up in the bundled
FaceTrackNoIR supported-games catalog, echoing it to `GameID2`, setting its
8-byte response table (zero for legacy V160 entries and unknown IDs), and
resetting `DataID`. Duplicate IDs use their first catalog entry, matching
OpenTrack lookup behavior. The NPClient DLL converts rotations/translations
to NPClient axis units and applies the LinuxTrack-derived byte-level game
encoding when the game ID and response ID match and the table is nonzero; this
is not a second angle response curve.

`data/facetracknoir_supported_games.csv` is the FaceTrackNoIR catalog used by
the publisher, obtained from the
[FaceTrackNoIR Supported Games download](https://sourceforge.net/projects/facetracknoir/files/FaceTrackNoIR%20Supported%20Games.csv/download).
`data/FACETRACKNOIR-COPYING.txt` carries the associated FaceTrackNoIR
permission and attribution notice; both are included in the packaged
executable. The catalog loader validates the expected columns and
response-table encodings; duplicate game IDs use the first row, matching the
reference lookup order.

`native/freetrack_client/` contains Orienta's game-facing FreeTrack DLL for
32-bit and 64-bit games. It copies frames under the FreeTrack mutex and exports
the FreeTrack client entry points. On enable, the GUI stages both DLLs under
`%LOCALAPPDATA%\Orienta\FreeTrackClient` and registers the per-user FreeTrack
client path. It restores the previous value only if the registry still points
to Orienta.

The output worker chooses this shared-memory backend for both FreeTrack 2.0
and NPClient. The game-facing protocols remain distinct: `native/freetrack_client/`
supplies the FreeTrack API DLL, while `native/npclient/` supplies the NPClient
API DLL. Neither game-facing DLL depends on the other.

`native/npclient/` contains Orienta's x86/x64 NPClient-compatible DLLs. The
implementation is derived from uglyDwarf's LinuxTrack NPClient work under the
MIT license; its source attribution and license are included in that folder.
It reads the same `FT_SharedMem` mapping using `FT_Mutext`, scales poses to
NPClient axis units, and exports the `NP_*` entry points. Its `NP_GetSignature`
payload matches the LinuxTrack compatibility payload. On enable, Orienta stages the DLLs under
`%LOCALAPPDATA%\Orienta\NPClient` and registers that path at
`Software\NaturalPoint\NATURALPOINT\NPClient Location`. It restores the prior
value on disable or GUI exit only if Orienta still owns it.
An unregistered second GUI session does not restore another session's client
path; crash recovery explicitly takes ownership of the saved registration
before restoring it. NPClient treats a finite centered pose as valid once the
publisher has initialized the mapping's camera dimensions. DLL unloading
releases its mapping and mutex handles.
`NPClient.log` beside the DLL records first API calls, mapping failures, and
sampled source/converted poses and game IDs (the first five frames, then every
120 frames) so discovery, shared-memory, handshake, and pose-conversion
failures can be distinguished.

Start the selected output before launching a game so it can load the registered
client and find the live mapping; keep Orienta running during tracking. The
registration backups persist so a later Orienta session can recover them after
a crash. `build/build_game_clients.bat` builds and smoke-tests both
protocols for x86 and x64. DCS 2.9.30 was verified to discover the NPClient
device and move the in-game view; catalog entries for other games are not a
guarantee that each title has been tested with Orienta.

### GUI

[`gui_wrk.py`](../src/workers/gui_wrk.py) is a Qt process. Its 25 ms queue timer
drains bounded batches from display/status/message queues; its 16 ms GUI timer
refreshes widgets. It owns the single-window layout:

* `OrientationPanelQt` is the primary screen and owns recenter,
   drift-disengage, and orientation controls.
* `hold_panel.py`, `two_line_button.py`, and `shortcut_dialog.py` own the
   reusable status/button/input-dialog widgets. `visualization_popup.py` owns
   popup reparenting and geometry persistence. The orientation panel remains
   the coordinator and fusion-control owner.
* `ConnectionPanelQt` owns serial input settings and status. `OutputPanelQt`
  owns UDP/FreeTrack/NPClient selection, target, native-client lifecycle, and
  output status. They are peer panels displayed side by side below Orientation.
* `MessagePanelQt`, `PreferencesPanel`, and `AboutPanel` are shared instances
  opened in dialogs rather than persistent tabs.

The GUI consumes latest Euler samples as `[yaw, pitch, roll]`; preserve that
ordering even though some signal names/documentation refer to roll first.
Qt signals and timers are the in-process UI synchronization mechanism; do not
read a multiprocessing queue from a background Qt thread unless ownership and
widget access are redesigned together.

### Input worker

[`input_wrk.py`](../src/workers/input_wrk.py) wraps optional `keyboard` and
`pygame` global input APIs. Its process has a command-loop thread and starts
keyboard/gamepad listener threads only when a shortcut or capture mode needs
them.

Commands: `('set_shortcut', key, display_name, action)`,
`('clear_shortcut', action?)`, `('start_capture',)`, `('stop_capture',)`,
and `('trigger_reset',)`. Responses include
`('input_captured', key, display_name)`,
`('shortcut_pressed', key, action)`, and
`('shortcut_released', key, action)`.

The current actions are `reset_orientation`, `disengage_drift`, and
`hold_orientation`. The orientation panel defines whether disengagement is
hold or toggle behavior, then changes fusion thresholds accordingly. Hold
Orientation is momentary by default (held while its button or shortcut is
pressed); the Preferences panel can enable toggle behavior instead.

Orientation hold is momentary by default and can be configured as a toggle.
While held, the fusion filter continues updating from sensor data. The
selected output remains at the held pose, while the display queue continues
carrying live orientation for the visualization. Releasing or toggling hold
off blends the held output quaternion back to the live filtered orientation
over the configured drift smoothing time, transition curve, and correction
strength. The GUI visualization retains a live-style marker at the pose
captured when hold was enabled, with a yellow dotted orientation line.

## Logging and error behavior

`util.log_utils` is the normal cross-process logging API. It sends a tuple to
`logQueue`; the parent `_log_writer` timestamps and appends it to
`%LOCALAPPDATA%\Orienta\orienta.log`, rotating the file at 5 MB within the same
directory. Logging is best-effort: a full log
queue drops entries rather than stalling real-time work.

`util.error_utils` supplies the shared queue helpers, IMU parser, bounds
helpers, and queue-health reports. The process monitor checks queue health
every ten seconds and emits warnings only for warning/critical queues.

Status names currently handled by the GUI include serial connection/data
activity, message/send rate, `output_status`, `processing`, `drift_correction`,
`stationary`, and gyro/centre calibration states. When adding a status name,
update the producer and `TabbedGUIWorker._route_status_update`; a
producer-only status silently has no UI effect.

## Persistence and assets

`PreferencesManager` reads/writes `%LOCALAPPDATA%\Orienta\config.cfg`, which is a runtime
file rather than a tracked source file. It uses a `.tmp` file and
`os.replace()` to avoid partial writes. Preferences include serial/network
values, theme, orientation settings, calibration settings, and shortcuts.
Themes are QSS files in `src/themes/`; application images are in `src/img/`.
The shared `util.paths.get_app_data_dir()` helper creates the per-user directory
without depending on the working directory, source tree, or PyInstaller bundle.
If `LOCALAPPDATA` is unset, it uses `~/AppData/Local/Orienta`. An explicit
`PreferencesManager(config_dir=...)` still overrides the default preferences
directory. Existing project-local preferences are not automatically migrated.

## Change safety checklist

Before changing this design:

1. Trace the queue's single writer and all readers; do not assume queues
   broadcast.
2. Preserve `[yaw, pitch, roll]` through `outputQueue`, `<6d` UDP
   serialization, and protocol-specific shared-memory conversion.
3. Keep one consumer of `outputQueue`; OutputWorker dispatches each pose only
   to its selected backend.
4. Keep the GUI as the sole Qt-widget owner process.
5. Make every blocking loop observe `stop_event`, use bounded queue waits, and
   close external resources in `finally`.
6. Update preferences load, save, and fusion command application together for
   new persisted controls.
7. Update this document and `CODEBASE_REFERENCE.md` when a queue, command,
   process, or persistent preference changes.
