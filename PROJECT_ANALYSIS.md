# Orienta Project Analysis

This document is a maintained technical reference for contributors and agents.
It describes the orientation-only architecture after the removal of the optional
optical subsystem. Update it whenever a runtime contract changes.

## Purpose

Orienta is a Windows-focused, real-time 3DOF head-tracking desktop application.
It reads accelerometer and gyroscope samples from serial, estimates yaw, pitch,
and roll, and sends those orientation values to OpenTrack over UDP.

The included Arduino Nano/FastIMU example is configured for an MPU6500 at I2C
address `0x68`. It emits this seven-field numeric CSV schema at a target 250 Hz
and 500000 baud:

```text
time_seconds,accel_x,accel_y,accel_z,gyro_x,gyro_y,gyro_z
```

Accelerometer samples are g units; gyroscope samples are degrees per second.

## Runtime topology

`orienta.py` validates Python version, accepts `--diagnostics`, creates
`ProcessHandler`, starts workers, and waits for its shared stop event.

`ProcessHandler` starts five processes:

1. `InputWorker`
2. `GUIWorker`
3. `SerialWorker`
4. `FusionWorker`
5. `UDPWorker`

It also owns a log-writer thread, a worker-monitor/restart thread, bounded
queues, and shutdown signal handlers.

```text
IMU serial input -> SerialWorker -> serialQueue -> FusionWorker -> eulerQueue -> UDPWorker -> OpenTrack
                                                     |
                                                     +-> eulerDisplayQueue -> GUIWorker

GUIWorker -> serialControlQueue -> SerialWorker
GUIWorker -> controlQueue -------> FusionWorker
GUIWorker -> udpControlQueue ----> UDPWorker
GUIWorker <-> input queues ------> InputWorker
```

## Queue contracts

Queue payloads are untyped Python strings, lists, and tuples. They are
cross-process APIs: update every producer and consumer together.

| Queue | Producer | Consumer | Contract |
|---|---|---|---|
| `serialQueue` | Serial worker | Fusion worker | Raw UTF-8 IMU CSV string. |
| `serialDisplayQueue` | Serial worker | GUI worker | Raw CSV string. |
| `eulerQueue` | Fusion worker | UDP worker | Numeric `[yaw, pitch, roll]`. |
| `eulerDisplayQueue` | Fusion worker | GUI worker | Numeric `[yaw, pitch, roll]`. |
| `serialControlQueue` | GUI | Serial worker | `('start', port, baud)`, `('stop',)`. |
| `controlQueue` | GUI/input flow | Fusion worker | Reset, calibration, filter, and tuning commands. |
| `udpControlQueue` | GUI | UDP worker | `('set_udp', host, port)`, `('udp_enable', bool)`. |
| input command/response queues | GUI/Input worker | Both | Shortcut setup, capture, and trigger notifications. |
| status and UI-status queues | Workers | GUI | `(status_name, value)` telemetry. |
| `messageQueue` | Serial worker | GUI | Human-readable strings. |
| `logQueue` | Workers | Manager | `(level, worker_name, message)`. |

Bounded queues, non-blocking writes, and “latest record wins” draining behavior
are intentional. They bound latency at the cost of dropping data under load.

## Serial and fusion behavior

The serial worker does nothing until it receives `('start', port, baud)`. It
retries connection every two seconds, supports cancellation with `('stop',)`,
publishes raw records to data and display queues, and reports connection state
and message rate.

`fusion_wrk.py` contains Euler and quaternion complementary-filter
implementations. The active filter can be changed at runtime. Both parse the
latest serial record, use the device timestamp for `dt`, reject intervals below
0.001 s or above 0.1 s, integrate gyroscope rates, and blend gravity-derived
roll/pitch only for plausible acceleration magnitudes. Yaw has no absolute
reference because the input contains no magnetometer.

Stationary state requires plausible gravity and a gyro magnitude below the
configured threshold for the configured debounce duration. Drift correction
activates only when stationary and inside independently configured yaw, pitch,
and roll center thresholds.

Fusion commands include:

- `reset_orientation` and `reset`;
- center threshold, alpha, drift curve, drift smoothing, drift strength, and
  axis inversion updates;
- filter selection;
- `recalibrate_gyro_bias` with optional sample count; and
- `calibrate_level` with optional sample count.

Fusion publishes numeric `[yaw, pitch, roll]` records to its output queues.

## UDP contract

The UDP worker consumes the latest orientation record. It sends only after
`('udp_enable', True)` and updates its target through `('set_udp', host, port)`.
For OpenTrack compatibility it emits six little-endian doubles in
translation-first order:

```python
struct.pack("<6d", 0.0, 0.0, 0.0, yaw, pitch, roll)
```

The first three values are permanently zero in this orientation-only version.

## GUI, input, and configuration

The PyQt5 GUI includes orientation tracking, optional diagnostics, messages,
preferences, and about tabs. It drains display queues frequently and renders
the most recent record. The status bar reports serial message rate, UDP send
rate, and device stationary/moving state.

The input worker manages global keyboard and pygame gamepad shortcuts for
orientation reset and temporary drift disengagement. Diagnostics lazily imports
matplotlib and refreshes enabled plots at 10 Hz.

`config/config.py` is the versioned source of defaults and limits. The GUI
persists machine-local settings in ignored `config/config.cfg`, written
atomically by `PreferencesManager`. It contains serial, network, orientation,
calibration, and GUI sections. Themes are application stylesheets in `themes/`.

## Refactoring constraints

1. Queue payload shapes and command tuples are cross-process compatibility
   boundaries. Migrate all producer/consumer pairs atomically.
2. Data drops are intentional latency control. Do not introduce blocking or
   unbounded queues without measuring end-to-end latency.
3. Keep device timestamp calculations separate from host-time telemetry and
   rate calculations.
4. The fusion worker combines filters, calibration, command handling, and
   output publication. Extract behavior only behind deterministic IMU fixtures.
5. Worker monitoring/restart behavior must continue to use correct target
   arguments after worker signature changes.
6. Prefer narrowly scoped error reporting that does not block the real-time
   path.
7. There is no automated behavioral suite. Add focused tests before major
   filter or IPC changes.

## Verification baseline

```powershell
python -m compileall -q .
python orienta.py --help
```

For behavioral changes, test recorded IMU lines and receive UDP locally. Verify
serial connection, calibration, reset, and UDP enablement manually when their
paths change.
