# Pre-release audit: Orienta 0.12

**Audit date:** 2026-10-26  
**Scope:** Repository source and configuration at the time of audit, including existing uncommitted worktree changes.  
**Changes made during audit:** None. The worktree edits present during the audit were left intact.

## A. Executive summary

**The current tree is not ready for public release.** The audit found two confirmed high-severity issues and one strongly suspected release-artifact mismatch:

1. Sensor-bias calibration can accept a steady, slow yaw turn as “stationary,” learn that motion as gyro bias, and then create substantial yaw drift.
2. Python 3.8 is advertised and accepted by the launcher, but source annotations use built-in generic types that fail when evaluated on Python 3.8.
3. The installer currently references a portable executable whose file timestamp predates later changes in source. The exact artifact build provenance has not been verified.

The orientation implementation has several sound aspects: it integrates body rates as quaternions, normalizes after integration, uses accelerometer feedback for tilt, rejects non-finite serial samples, and has substantial deterministic test coverage. The full Python suite passed: **266 tests in 13.227 seconds**. This is evidence for application-side logic, not proof of firmware behavior, real sensor accuracy, latency, or compatibility with actual games.

**Verdict: NO-GO** until the calibration defect and Python-version mismatch are resolved, the release executable is rebuilt and verified against the release source, and required hardware/output integration checks pass.

## B. Release-blocking defects

### 1. Slow yaw motion can be stored as gyro bias — High, confirmed

During calibration, a sample is accepted when its gyro magnitude is below the general stationary threshold. The default threshold is 5°/s, and the default calibration collects 400 samples. The robust estimator checks variation in the readings, but a constant rate has near-zero variation and is not rejected. See [`src/workers/fusion_wrk.py`](src/workers/fusion_wrk.py) around lines 176 and 186 and [`src/config/config.py`](src/config/config.py) around lines 141 and 155.

This was reproduced using synthetic level accelerometer data and a constant 4°/s yaw rate: calibration accepted all 400 samples and estimated a 4°/s yaw bias. After the motion stopped and the raw gyro returned to zero, the filter produced **0.016° of false yaw in 4 ms**, equivalent to 4°/s of drift. This can spoil tracking immediately after calibration; if calibration persistence is enabled, the bad bias can also survive a restart.

**Root cause:** stable readings are treated as stationary based on low rate and low spread, without distinguishing a constant yaw turn from sensor bias. A 6-axis IMU has no independent heading reference to make that distinction.

**Recommended fix:** make the calibration acceptance rule stricter and explicit about supported gyro bias, and reject sustained motion rather than relying on sample variance alone. Retain the previous calibration whenever motion cannot be ruled out. Add the synthetic yaw-turn case before permitting persistence of the result.

**Verification:** a level `(0, 0, 1 g)` input with constant 4°/s yaw must be rejected or time out without changing existing bias; a genuinely stationary input with representative sensor bias must still calibrate. Confirm the resulting stopped yaw rate is within the documented calibration tolerance.

### 2. Advertised Python 3.8 support is incompatible with source annotations — High, confirmed

The launcher allows Python 3.8 and the development requirements claim Python 3.8–3.13 support ([`orienta.py`](orienta.py), [`build/dev/requirements.txt`](build/dev/requirements.txt)). However, source modules use annotations such as `tuple[float, ...]` without postponing annotation evaluation—for example, [`src/util/tracking_sample.py`](src/util/tracking_sample.py) and [`src/filters/base.py`](src/filters/base.py). Built-in generic subscripting is not available on Python 3.8, so importing those modules raises an error before tracking can start.

Only Python 3.13 was available in this environment; therefore the failure was not reproduced on a 3.8 runtime, but the incompatibility follows from the declared interpreter behavior and the code’s evaluated annotations.

**Recommended fix:** either make all affected annotations Python-3.8-compatible (for example, use `typing` aliases or postpone annotations consistently), or raise the minimum supported version everywhere: launcher, setup script, and documentation.

**Verification:** add a Python 3.8 import/startup smoke test if retaining support, plus a supported-version CI matrix. The current suite passing on Python 3.13 does not cover this issue.

### 3. Packaged installer may contain a stale executable — High, strongly suspected

The installer script includes [`build/installer/Orienta.iss`](build/installer/Orienta.iss), which sources `build/dist/Orienta-0.12.exe`. On disk, that executable was last modified **17 October**, while the current [`src/workers/fusion_wrk.py`](src/workers/fusion_wrk.py) was modified **25 October**. The [`build/dist/Orienta-Setup-0.12.exe`](build/dist/Orienta-Setup-0.12.exe) was modified **26 October**. These timestamps strongly suggest the installer was created using an executable older than the current source.

Timestamps do not prove binary contents, so this remains **strongly suspected**, not confirmed. There is no demonstrated build provenance showing that the installer contains the audited code.

**Impact:** users may install a version that does not include the current fusion, configuration, or UI changes even though the installer is versioned 0.12.

**Recommended fix:** build the portable executable from the final, committed release snapshot, then compile the installer from that newly produced executable. Record hashes or other provenance for both artifacts.

**Verification:** inspect the built executable’s version metadata, confirm its build inputs and timestamp, and smoke-test the installed application—not just the source checkout.

## C. Detailed technical audit

### Architecture and data path

The project is a Windows Python/PyQt application with separate serial, fusion, output, GUI, and input workers. The intended path is:

**IMU serial CSV → serial validation and queueing → quaternion fusion → Euler yaw/pitch/roll and rates → optional native processing → UDP or Windows shared-memory output.**

The bundled primary firmware example is an ESP32-C3 and MPU6500 sketch, nominally sampling at 250 Hz over 500000 baud. The application accepts generic serial sources, but the bundled sketch is only one concrete hardware example. The alternate ICM20948 sketch emits magnetometer fields, but fusion consumes only the first seven CSV fields; the magnetometer is ignored. The documentation correctly says Orienta does not use a magnetometer.

### Orientation mathematics and sensor fusion

The quaternion pipeline uses a right-handed IMU frame and standard ZYX Euler conversion. It integrates all gyro axes in the shared sensor frame and negates yaw at the application Euler boundary, rather than mirroring one gyro axis during integration. This is mathematically consistent with the documented convention. Normalization, pitch `asin` clamping, wrapped output angles, and `dt` bounds are present. See [`src/workers/fusion_wrk.py`](src/workers/fusion_wrk.py), particularly `_euler_from_quat`, `_quat_from_euler`, `_integrate_body_rates`, and `update`.

Accelerometer feedback corrects tilt, with trust weighting and filtering intended to reduce the influence of dynamic acceleration. The automated tests cover static tilt convergence, rate independence, yaw sign, and several tilted-turn scenarios. This supports the implementation’s internal consistency, but does not establish behavior on a mounted sensor.

Yaw is gyro-integrated. Without a magnetometer or another heading reference, **absolute heading cannot be recovered and yaw drift is unavoidable**. The project documents this limitation. Calibration does not estimate accelerometer scale or arbitrary sensor-to-head mounting rotation; the documented calibration requires the sensor level with +Z up.

Recentering stores and subtracts Euler-axis origins component by component. That is the tested behavior, but it is **not equivalent to applying a quaternion-relative rotation** for arbitrary combined yaw/pitch/roll poses. This appears intentional; the user-facing meaning should remain “zero these reported Euler axes,” unless a true relative-pose interpretation is intended.

### Calibration and user experience

The level-pose assumption, calibration timeout, robust spread checks, failure retention, and bias persistence are documented and tested. The slow-yaw acceptance defect above is the important exception: stable yaw motion is not distinguishable from yaw bias using the available sensors, and the present 5°/s acceptance threshold allows a material false bias.

The saved calibration is not tied to a sensor identity. If users switch devices, previously stored offsets may be applied to a different IMU. This is an operational limitation to document or address if multiple sensors are a supported use case.

### Timing, latency, and real-time behavior

The firmware uses sensor timestamps and extends the microsecond counter across rollover; the host rejects too-small and too-large time steps. Queue sizes are bounded, fusion processes each queued sample, and output processing drains backlogs. The firmware’s 250 Hz output rate is nominal, not a measured end-to-end rate.

No end-to-end latency, timing-jitter, sustained throughput, CPU, or long-run memory measurements were available. The source includes diagnostic timing counters, but those do not establish sensor-to-consumer latency. The README’s “over 240 Hz” claim should be checked on real hardware and across the full output path.

### Output protocol and integration

UDP serializes six little-endian doubles: zero translation followed by yaw, pitch, and roll. The test suite asserts this packet shape. The [opentrack UDP reader](https://github.com/opentrack/opentrack/blob/unstable/tracker-udp/ftnoir_tracker_udp.cpp) consumes the six values without a packet-level conversion.

The direct FreeTrack path converts angles to radians and applies protocol axis signs; the NPClient path exposes shared-memory poses through a separate client DLL. Shared-memory layout sizes are asserted, mutex handling is present, and native ABI smoke-test sources exist. These checks do not prove that a particular game or version accepts the output; no live game integration result was available.

### Singularities in rate output — Medium, confirmed

Euler-rate conversion clamps `cos(pitch)` to `1e-3` near ±90° but then divides by that value. At pitch 90°, a 10°/s body-Z rate produces `(-10000, 0, 10000)` degrees/s in the application yaw/pitch/roll rates. This result was reproduced from the implementation. Those rates are forwarded to rate-aware native filters ([`src/workers/fusion_wrk.py`](src/workers/fusion_wrk.py), `_euler_rates_from_body` and `update`; [`src/outputs/native_output_processing.py`](src/outputs/native_output_processing.py), `process`).

Euler yaw/roll are inherently singular at ±90° pitch; the clamp prevents division by zero but does not make the rates meaningful or bounded. Extreme looking-up/down motion could cause rate-aware filtering artifacts.

**Recommended fix:** define a policy near the singularity—such as bounding rates to a documented physical limit or disabling Euler-rate forwarding in a defined region—and test the policy. Do not treat the current clamp as a verified singularity solution.

### Software quality, reliability, and security

Serial samples are checked for finite values and plausible ranges. Reconnect attempts use bounded backoff, queues are bounded, and output backends report errors. No high-confidence unsafe parsing or memory-safety defect was found in the reviewed paths.

A confirmed recovery limitation remains: if the fusion worker crashes and is restarted, it creates a new filter from defaults. The automatic process restart does not replay all GUI tuning, axis inversion, and calibration state. The architecture documentation acknowledges this. A restart can therefore change tracking behavior while the GUI still reflects the previous settings. See [`src/workers/process_man.py`](src/workers/process_man.py), [`src/workers/fusion_wrk.py`](src/workers/fusion_wrk.py), and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Testing, build, packaging, and regression

The full Python suite passed: **266 tests, 13.227 seconds, OK**. Coverage is comparatively strong for fusion behavior, calibration flows, settings persistence, serial recovery fakes, output routing, and native-output processing. The tests are predominantly synthetic or mocked; they do not validate the physical sensor or third-party games.

The current Python test environment was Python 3.13. No Python 3.8 runtime was available. No Arduino compiler or Inno Setup compiler was available, and no build commands that could overwrite existing release artifacts were run. Firmware compilation, native-client smoke tests, and a clean package build therefore remain unverified.

Relative to the `v0.11` tag, the branch adds major changes including direct native output, adaptive filters, sensor settings, and a new MPU6500 sketch. The current tree also had **24 tracked paths with uncommitted edits** and no `v0.12` tag at audit time, so it was not an immutable release snapshot. [`README.md`](README.md) and [`docs/THIRD_PARTY_NOTICES.md`](docs/THIRD_PARTY_NOTICES.md) provide substantial setup and attribution information, and the project includes an MIT license. No release-to-release executable comparison or full clean-build reproducibility result was available.

## D. Findings table

| Severity | Location | Issue | User impact | Confidence | Recommended action |
|---|---|---|---|---|---|
| High | [`src/workers/fusion_wrk.py`](src/workers/fusion_wrk.py), calibration acceptance and estimator | Stable yaw motion below 5°/s can be calibrated as gyro bias. | False yaw drift; calibration can ruin tracking. | Confirmed; synthetic reproduction accepted 400 samples at 4°/s. | Reject sustained motion; add regression tests and preserve prior bias on rejection. |
| High | [`src/util/tracking_sample.py`](src/util/tracking_sample.py), [`src/filters/base.py`](src/filters/base.py), [`orienta.py`](orienta.py) | Evaluated built-in generic annotations conflict with the advertised Python 3.8 minimum. | Startup/import failure for Python 3.8 users. | Confirmed by Python 3.8 compatibility rules; 3.8 runtime unavailable here. | Make annotations compatible or raise the minimum version consistently. |
| High | [`build/installer/Orienta.iss`](build/installer/Orienta.iss), [`build/dist/Orienta-0.12.exe`](build/dist/Orienta-0.12.exe), [`build/dist/Orienta-Setup-0.12.exe`](build/dist/Orienta-Setup-0.12.exe) | Installer references an executable whose timestamp predates later source changes. | Installed build may omit audited source changes. | Strongly suspected; no build provenance/hash comparison. | Rebuild from the final source snapshot, then rebuild and smoke-test the installer. |
| Medium | [`src/workers/fusion_wrk.py`](src/workers/fusion_wrk.py), `_euler_rates_from_body` and `update`; [`src/outputs/native_output_processing.py`](src/outputs/native_output_processing.py), `process` | Euler-rate clamp yields enormous rates near pitch ±90°. | Rate-aware filters may react sharply during extreme pitch. | Confirmed mathematically and by calculation. | Define and test bounded behavior near the Euler singularity. |
| Medium | [`src/workers/process_man.py`](src/workers/process_man.py), [`src/workers/fusion_wrk.py`](src/workers/fusion_wrk.py), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Fusion worker restart loses process-local tuning and calibration. | Tracking may resume with changed axes, biases, or thresholds. | Confirmed in the restart design and documentation. | Replay a validated configuration snapshot after restart or make the reset explicit to the user. |

## E. Missing test coverage

Highest-value additions:

1. **Calibration during steady yaw motion:** feed 400 samples at 250 Hz, level acceleration, and constant 4°/s yaw. **Acceptance:** reject or time out, retain prior biases, and report failure. Also verify a genuinely stationary bias case remains calibratable.
2. **Python support matrix:** launch/import the fusion, filter, and tracking-sample modules on every advertised version. **Acceptance:** startup succeeds on the declared minimum, or documentation and version guards consistently reject it.
3. **Euler-rate singularity:** test body rates at pitch 89°, 89.9°, 90°, and their negative equivalents. **Acceptance:** rates remain finite and bounded according to an explicit policy, including through native-filter forwarding.
4. **Orientation reference tests:** use synthetic single-axis rotations with known inputs and outputs; for example, 90°/s around sensor X for one second should yield 90° roll within a declared tolerance, while positive sensor-Z yaw should produce the documented negative application yaw. Check quaternion norm and combined-rotation results against an independent quaternion reference.
5. **Fusion restart:** crash/restart the worker with non-default inversion, calibration, and thresholds. **Acceptance:** either settings are restored without a pose discontinuity or the user receives a clear inactive/error state and must explicitly restart tracking.
6. **Firmware and hardware tests:** compile the MPU6500 sketch against the documented FastIMU version; verify 250 Hz sample timing, synchronized accel/gyro timestamps, micros rollover, settings readback, and sensor-failure behavior.
7. **End-to-end integration:** measure sustained output rate and input-to-output latency; test UDP with opentrack and direct FreeTrack/NPClient with representative 32-bit and 64-bit games. Document acceptance criteria before testing; explicitly measure the README’s over-240-Hz claim.

## F. Prioritized remediation plan

### Must fix before release

1. Harden calibration against steady sub-threshold yaw motion and add the regression test. This comes first because invalid calibration can persist and affects subsequent tracking validation.
2. Resolve the Python 3.8 mismatch: preserve 3.8 support with compatible annotations, or raise and enforce the minimum version consistently.
3. Produce a clean, committed release snapshot; build the portable executable from it, then build the installer from that exact executable. Verify version metadata and artifact provenance.
4. Compile the bundled firmware and complete a real IMU-to-output smoke test. The current test suite cannot substitute for this.

### Should fix before release if practical

1. Replay fusion configuration on automatic worker restart, or make the loss of tracking state visible and require explicit user confirmation.
2. Bound or otherwise define rate behavior near the Euler singularity.
3. Measure the advertised output rate, latency, timing jitter, and a representative long session.
4. Add a user-facing release note documenting axis conventions, calibration assumptions, gyro-only yaw drift, and verified game/runtime compatibility.

### Safe to defer to a later version

1. Absolute heading correction: this requires an additional heading reference; the current gyro-plus-accelerometer design cannot provide it.
2. Arbitrary sensor mounting alignment and accelerometer scale calibration, provided current mounting limitations remain clear.
3. Multi-device calibration identity and broader hardware support, unless multiple IMUs are part of the release’s supported use case.

## G. Release verification checklist

- [ ] Commit and tag the exact audited source; confirm the release worktree is clean.
- [ ] Run the full Python test suite and the new calibration, compatibility, restart, and singularity tests.
- [ ] Verify the declared Python version range in a clean environment.
- [ ] Compile the firmware with the documented library/toolchain versions.
- [ ] Run native x86/x64 client smoke tests.
- [ ] Build the portable executable and installer from the same source snapshot; verify version metadata and hashes.
- [ ] On hardware, validate positive and negative yaw, pitch, and roll; combined rotations; calibration; recenter; and reconnection/failure behavior.
- [ ] Measure sustained output frequency, end-to-end latency, jitter, CPU/memory use, and long-session drift.
- [ ] Test UDP with opentrack and direct FreeTrack/NPClient with representative applications and architectures.
- [ ] Verify install, first run, preferences, client registration/restoration, uninstall, and documented handling of user data.
- [ ] Publish hardware requirements, supported versions, known limitations, and release notes alongside the artifacts.

## H. Final release verdict

**NO-GO.**

Before approval, fix and test the slow-yaw calibration acceptance, resolve the Python 3.8 compatibility claim, and rebuild/verify the 0.12 executable and installer from the final source snapshot. Then compile the firmware and demonstrate the complete sensor-to-consumer path on hardware. The passing unit suite is strong application-side evidence, but it does not establish those release-critical conditions.
