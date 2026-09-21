# Orienta pre-release quality audit

## Executive summary

**Overall assessment: not release-ready without targeted fixes.** No confirmed
critical issue was found, but several HIGH defects affect tracking correctness
and resilience:

- Non-finite serial values (`NaN`/`Infinity`) can reach fusion and UDP output.
- The bundled firmware's floating-point timestamp loses precision after long
  uptime and can make fusion reject all samples.
- Automatic worker restart loses live fusion calibration/settings and input
  shortcuts.
- Several user-facing settings either do not take effect or can leave the UI
  in an incorrect enabled state.

The core quaternion conventions are internally coherent under specific IMU
assumptions, but the physical sensor-axis mapping and OpenTrack convention
still require bench validation before release.

## Findings and release TODOs

| # | Severity | Location | Problem and recommended fix | Confidence |
|---|---|---|---|---|

| 2 | HIGH | `arduino/Calibrated_sensor_output_timestamped/Calibrated_sensor_output_timestamped.ino`; `workers/fusion_wrk.py:QuaternionComplementaryFilter.update` | **Replace floating-point firmware timestamps.** On common 32-bit-float Arduino targets, timestamp precision grows worse with uptime. Around roughly 12 days, 4 ms samples can quantize farther apart than `DT_MAX` (100 ms), causing continuous large-`dt` suppression and frozen tracking. Emit integer `uint32` milliseconds/microseconds or a sample counter; handle wrap and convert to seconds in Python. | High |
| 3 | HIGH | `workers/process_man.py:ProcessHandler._worker_monitor`; `workers/fusion_wrk.py`; `workers/input_wrk.py` | **Make automatic restart state-safe, or disable it until it is.** A restarted fusion worker loses gyro bias, centre offsets, thresholds, tuning, and inversion settings. A restarted input worker loses registered shortcuts. The GUI does not replay state after worker recovery. Add worker-ready/generation state and replay configuration, or require explicit user recovery/recalibration. | High |
| 4 | MEDIUM | `workers/gui_qt/panels/preferences_panel.py:_apply_stationary_gyro`; `workers/gui_qt/panels/preferences_panel.py:_apply_stationary_debounce`; `workers/fusion_wrk.py:QuaternionComplementaryFilter.__init__` | **Wire stationary settings to fusion or remove/disable the controls.** The UI persists/displays values, but both apply functions contain `pass`; fusion continues using startup constants. Add validated fusion commands, apply saved values at startup, and show delivery failure. | High |
| 5 | MEDIUM | `workers/fusion_wrk.py:QuaternionComplementaryFilter.update` | **Correct accelerometer angle blending across wrap boundaries.** Linear roll/pitch blending turns a `+179°` versus `-179°` comparison into a sweep through `0°`, rather than using the shortest path. Blend wrapped angular deltas or apply gravity correction in quaternion/vector space. | High |
| 6 | MEDIUM | `workers/fusion_wrk.py:QuaternionComplementaryFilter.update` | **Fix drift-correction completion for cosine/quadratic curves.** Their per-frame rate reaches zero at the configured endpoint. Multiplicative correction therefore leaves material residual error rather than reaching the centre target. Interpolate from saved start orientation to target, or continue non-zero asymptotic correction until a defined epsilon. | High |
| 7 | MEDIUM | Calibration paths in `workers/fusion_wrk.py:run_worker` | **Do not mark partial calibration as successful.** Shutdown, disconnect, or sample starvation can end calibration after one valid sample; current code averages partial data and reports success. Require exactly the requested sample count; otherwise retain prior calibration and report cancelled, timed out, or insufficient samples. | High |
| 8 | MEDIUM | `workers/gui_qt/panels/connection_panel.py:ConnectionPanelQt._enable_udp` | **Validate UDP configuration before changing UI state.** The method marks UDP enabled, changes button text, and disables inputs before parsing/queuing the port. Invalid input or queue failure leaves UI enabled although no usable command reached the UDP worker. Validate first, queue commands, then commit UI state; restore controls on failure. | High |
| 9 | MEDIUM | `util/error_utils.py:safe_queue_get`; `workers/fusion_wrk.py:run_worker`; `workers/gui_wrk.py:TabbedGUIWorker.process_queues` | **Make queue and malformed-data faults diagnosable.** Broad catches convert queue/IPC failures and malformed samples into empty-queue behavior or silent skips. Keep real-time drop policy, but rate-limit explicit logging/statuses for parsing and unexpected queue failures. | High |
| 10 | LOW | `workers/gui_qt/panels/connection_panel.py:ConnectionPanelQt.setup_ui` | **Provide serial-port discovery or validated editable selection.** The UI offers `COM1` through `COM99` rather than discovering devices. Enumerate `serial.tools.list_ports`, provide refresh, retain custom saved values, and report opening failure details. | High |
| 11 | LOW | `config/config.py`; `README.md` | **Verify/document the default OpenTrack port.** The app defaults to `4243`; common OpenTrack UDP setups often use `4242`. This is not confirmed as a defect because users can configure either side. Align with the intended profile or clearly document that both endpoints must match. | Medium |
| 12 | LOW | `workers/gui_qt/panels/about_panel.py:AboutPanel.setup_ui` | **Correct the About capability statement.** It says "IMU + CV," but this repository contains no computer-vision pipeline. Change to IMU-based orientation tracking. | High |

## Sensor-fusion correctness assessment

### Pipeline traced

```text
Firmware CSV
  timestamp, ax, ay, az, gx, gy, gz
        |
        v
parse_imu_line()
  expects seconds, g, deg/s
        |
        v
QuaternionComplementaryFilter.update()
  gyro deg/s -> rad/s
  q_dot = 0.5 * q ⊗ [0, wx, wy, wz]
  q <- normalize(q + q_dot * dt)
        |
        v
acceleration-derived roll/pitch correction
        |
        v
Euler extraction:
  roll  = atan2(2(wx + yz), 1 - 2(x² + y²))
  pitch = asin(clamp(2(wy - zx)))
  yaw   = atan2(2(wz + xy), 1 - 2(y² + z²))
        |
        v
optional per-axis sign inversion
        |
        v
UDP: <6d = 0, 0, 0, yaw, pitch, roll
```

### Internally verified mathematics

The following parts are internally consistent:

- Quaternion order is consistently `[w, x, y, z]`.
- Hamilton multiplication matches that ordering.
- `q_dot = 0.5 * q ⊗ omega` is appropriate when gyro readings are body-frame
  angular velocity under the matching body-to-reference convention.
- Gyro degrees/s are converted to radians/s before integration.
- The quaternion is normalized after integration and correction.
- Euler extraction is the standard ZYX yaw-pitch-roll form: `gx` produces
  roll, `gy` pitch, and `gz` yaw.
- A numerical smoke test without acceleration correction produced approximately
  +90° of roll, pitch, and yaw respectively from +90°/s for one second around
  the X, Y, and Z gyro axes.

### Conditions that must be true externally

Physical correctness depends on these assumptions:

1. FastIMU emits accelerometer values in **g** and gyro values in
   **degrees/s** for the configured MPU6500.
2. Rest gravity is positive sensor Z (`ax≈0`, `ay≈0`, `az≈+1`).
3. FastIMU axes map directly to desired roll/pitch/yaw axes.
4. The sensor mount needs only sign inversions; the application has no general
   axis-permutation/remapping matrix for a 90°-rotated mount.
5. OpenTrack accepts yaw, pitch, and roll in degrees using the six-double
   translation-first packet layout.
6. The configured OpenTrack profile maps yaw/pitch/roll to intended head axes.

### Mathematical limits

- Gimbal lock is expected near Euler pitch ±90°. Quaternion integration remains
  stable, but the Euler representation sent to OpenTrack can exhibit the
  normal yaw/roll coupling.
- With no magnetometer, yaw has no absolute reference. Stationary
  near-centre drift correction deliberately recentres rather than establishing
  a physical heading.
- Accelerometer correction treats measured acceleration as gravity. Sustained
  linear acceleration near 1 g can bias pitch/roll; this is a limitation of
  this complementary filter design rather than a confirmed implementation bug.

## Runtime and lifecycle assessment

### Startup and normal operation

- The parent owns queues/event and starts log/monitor threads before workers.
- The GUI process owns Qt widgets and uses Qt timers to poll queues.
- Serial stays idle until explicitly started; fusion reports inactive until
  valid data arrives.
- UDP remains disabled until explicitly enabled.
- Bounded queues and latest-sample behavior prevent display or UDP delay from
  blocking serial/fusion indefinitely.

### Device disconnect and malformed output

- `SerialException` handling closes the port, reports error state, and returns
  the worker to idle.
- Fusion marks processing inactive after two seconds without data.
- Malformed data and queue faults are mostly silent, while non-finite input is
  currently accepted; both require the fixes listed above.

### OpenTrack and network behavior

- No OpenTrack listener is not itself an error for UDP and should not
  destabilize the app.
- The UDP worker owns a single socket and closes it in `finally`.
- Socket send errors are logged.
- The worker drains input rather than timer-retransmitting a cached sample, so
  it does not endlessly send stale orientation after fusion stops.

### Shutdown

- A shared stop event allows cooperative loop exit; the process manager
  escalates to terminate/kill after bounded joins.
- Input listener threads are joined with timeouts.
- The log writer can leave final queued logs undrained after stop; this is
  acceptable for telemetry but not for guaranteed final failure reporting.

## Pre-shipping checklist

### Required fixes and decisions

- [ ] Reject non-finite serial input and Euler output.
- [ ] Replace float timestamp transport with integer ticks and test long uptime.
- [ ] Make worker recovery replay fusion/input configuration or disable silent
  auto-restart.
- [ ] Wire stationary detection controls end-to-end or remove them.
- [ ] Correct wrapped angle blending and drift-correction completion.
- [ ] Require full calibration sample counts and report timeout/cancellation.
- [ ] Make UDP enable/disable UI state transactional.
- [ ] Remove hard-coded debug paths and debug artifacts from release output.
- [ ] Add rate-limited diagnostics for malformed data and queue/worker faults.
- [ ] Perform a real OpenTrack axis/sign integration test.

### Platform and deployment verification

- [ ] Test a fresh installation using `install.bat`, outside the developer
  checkout path.
- [ ] Verify supported Python versions and state the Python 3.14 support
  policy.
- [ ] Test missing keyboard/gamepad support or permission failures.
- [ ] Test missing device, wrong port/baud, unplug/reconnect, garbage data,
  paused device, and device reboot with timestamp restart.
- [ ] Test unavailable saved COM port/controller, invalid network config, and
  off-screen popout geometry.
- [ ] Deliberately crash/restart fusion and input workers; verify state recovery
  or explicit failure.
- [ ] Capture UDP packets: assert a 48-byte little-endian `<6d` layout with
  zero translations.
- [ ] Bench-test yaw, pitch, roll, inversion, rapid movement, prolonged
  stationarity, and near-vertical pitch.

## High-value tests

1. **Deterministic fusion tests** — identity gravity; ±90° axis rotations;
   mixed-axis rotations; duplicate/out-of-order timestamps; large gaps;
   timestamp restart; malformed CSV. Assert finite normalized quaternion and
   Euler state after every accepted sample.
2. **Non-finite/boundary input tests** — `nan`, `inf`, empty/extra/partial
   values, extreme values. Verify rejection and ensure no bad output reaches
   queues or UDP.
3. **Calibration transaction tests** — timeout, disconnect, stop event, and
   partial samples must not overwrite a valid prior calibration.
4. **Queue backpressure integration test** — simulate a producer faster than
   GUI/UDP consumers; verify bounded latency and no deadlock.
5. **Worker-recovery integration test** — set tuning/inversion/shortcuts,
   terminate fusion/input workers, and verify configuration replay or an
   explicit recovery failure state.
6. **UDP capture plus OpenTrack bench test** — assert packet layout and verify
   each physical axis/sign in OpenTrack preview and an actual consumer.
7. **Firmware endurance simulation** — run beyond the equivalent
   floating-timestamp precision threshold and verify stable `dt` after moving
   to integer timing.

## Non-findings

- Serial `readline()` framing matches the bundled firmware's `Serial.println`
  output.
- Bounded queues and latest-sample behavior are appropriate for real-time
  latency.
- UDP receiver absence does not require an acknowledgement path.
- No unsafe deserialization, arbitrary command execution, secrets, or
  externally exposed network listener was found.
