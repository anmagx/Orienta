# Native Output Processing Implementation Plan

## Goal and scope

Add user-configurable axis response curves, low-latency jitter filtering, and
static pose offsets to Orienta's direct FreeTrack 2.0 and NPClient output.
UDP remains a transparent relay to opentrack: it must continue sending the
computed yaw, pitch, and roll without Orienta-side curves, filtering, or
offsets.

## Current architecture

- Fusion publishes `[yaw, pitch, roll]` in degrees to `outputQueue`.
- `OutputWorker` drains stale poses and selects UDP or the shared-memory
  backend. FreeTrack and NPClient share `FreeTrackSharedMemory`.
- The UDP backend serializes the angles as the last three values in an
  opentrack six-double packet; its translation values are zero.
- The shared-memory backend applies FreeTrack axis signs and converts angles
  from degrees to radians. It currently publishes zero translation.
- Preferences are stored as string values in INI sections. The Preferences
  panel already sends runtime commands to `OutputWorker`; `outputControlQueue`
  already owns output configuration and lifecycle commands.

These boundaries make `OutputWorker` the appropriate place to select
protocol-specific processing. Keep the pose from fusion unchanged and add a
small, independently testable processing component used only for FreeTrack and
NPClient before calling the shared-memory backend.

## Recommended processing model

Use rotations in degrees ordered yaw, pitch, roll and translations in
millimeters using the FreeTrack shared-memory X/Y/Z axes. The UI exposes those
same axis signs; `src/outputs/shared_memory_output.py` remains responsible for
converting rotations to FreeTrack radians and signs. NPClient scales the same
translation values against a 500 mm range.

For direct protocols, process each fresh pose in this order:

1. Validate finite input and obtain its monotonic sample time.
2. Unwrap yaw across the ±180° representation boundary.
3. Apply the adaptive filter to the input yaw, pitch, and roll.
4. Apply each axis's input-magnitude limit and response curve to the filtered
   angles.
5. Add the six configured output offsets: yaw, pitch, roll, X, Y, and Z.
6. Pass the processed pose to the shared-memory backend, which remains
   responsible for FreeTrack representation and protocol conversion.

Keep this processing entirely out of `UDPOutput.send_pose`. With UDP selected,
pass the original three fusion angles straight to the existing backend and
retain its current zero-translation packet layout and rate limiting.

The NPClient DLL converts the shared pose to NPClient axis units and applies
the game-ID response table as a byte-level encoding step; it does not apply an
additional angle response curve. Preserve that encoding and the existing
game-ID handshake after Orienta's curves have been applied.

## Feature design

### Axis mapping curves

- Provide separate editable curves for yaw, pitch, and roll, with the input
  magnitude axis spanning 0° to the selected limit, the output magnitude axis
  spanning 0° to 180°, and a current-pose indicator.
- Give each axis an independent maximum-input dropdown with choices 30°, 45°,
  60°, 75°, and 90°. Set the curve's input domain to the selected limit and
  keep its final point at that input limit; rescale control points when the
  limit changes. The final output defaults to 180° and is vertically editable
  within the 0°–180° range. Use 90° as the default.
- Allow each axis to opt into a custom input magnitude from 1° to 180°.
  Automatic calibration captures comfortable left/right yaw and upper/lower
  pitch angles on the primary display, uses the smaller absolute angle from
  each opposite-side pair, and stores the results as custom yaw/pitch limits.
  Roll remains manually configured.
- Represent each curve as ordered control points and evaluate with
  shape-preserving cubic Hermite interpolation. Use one positive-magnitude curve per axis,
  then restore the input sign to apply the response symmetrically. Start with
  a linear curve through `(0, 0)`, `(limit/2, 90)`, and `(limit, 180)`.
- Keep the zero point fixed to preserve a neutral pose. Fix the input
  coordinates at 0° and 180° while allowing the midpoint and other interior
  points to move in both axes. Clamp out-of-domain magnitudes and interpolate
  between sorted points. Validate finite values, unique input coordinates,
  and output bounds before applying a complete curve. Clamp final rotations
  to ±180° after applying static offsets.
- Support the motivating example: a `+30°` yaw input can map to `+120°` output,
  with the corresponding negative mapping under a symmetric curve.
- Keep the curve editor responsive but avoid sending a UI command for every
  drag event; debounce edits and apply a complete validated settings snapshot.

### Selectable filtering

- Keep native pose filters in `src/filters/`, with a shared filter contract,
  filter metadata/registry, and one implementation per module. One Euro is
  the default registered filter; EMA, hysteresis jitter suppression, and a
  critically damped smoother and an Accela-inspired adaptive response are
  also available. New filters must remain addable without putting algorithm
  code in the output worker or settings dialog.
- Add a filter dropdown to Native Output Settings. Build filter-specific
  controls and tooltips from each registered filter's metadata, and persist
  selection plus per-filter settings in the `native_output` section.
- Keep One Euro's adaptive low-pass settings for low-speed smoothing,
  fast-movement responsiveness, and velocity-estimate stability. EMA exposes
  a fixed smoothing cutoff in Hz. Tooltips explain that lower cutoffs smooth
  more but add lag. The critically damped filter exposes a natural response
  frequency; lower values settle more gradually and higher values respond
  faster. The Accela-inspired filter uses a per-axis deadzone and a smooth
  vector-magnitude response boost. Keep defaults, slider ranges, and steps
  configurable in `src/config/config.py`; document upstream inspiration and
  licensing in `docs/THIRD_PARTY_NOTICES.md`.
- Use monotonic sample timestamps, reset filter state when native output is
  enabled, its settings change, or the output protocol changes, and handle
  yaw wraparound so crossing `+/-180°` does not look like a sudden full-speed
  motion.
- Do not add queueing or wait for future samples: process the newest available
  pose in the existing output worker and continue dropping stale poses.
- Measure jitter attenuation against step/ramp latency before settling
  defaults. A deadband must not prevent deliberate slow movement from
  accumulating or create a noticeable release jump.

### Static offsets

- Add separate rotation offsets in degrees and translation offsets in the
  agreed native linear unit, all defaulting to zero.
- Apply rotations after the response curve and filter so offsets remain
  absolute output-view adjustments and do not alter their own curve response.
- Apply translations only to the direct shared-memory pose. Preserve explicit
  coordinate signs/conversions in one place, rather than embedding game/API
  conventions in the UI.
- Keep offsets distinct from fusion recentering and sensor calibration; they
  should not modify the fused orientation or UDP stream.

## Settings, UI, and runtime updates

1. Add `native_output_settings_dialog.py` as the native-output settings dialog.
   A button in `connection_panel.py` opens it only while FreeTrack or
   NPClient is selected. Include a curve editor, per-axis preset/custom input
   limits and automatic calibration, filter sliders, and rotation/translation
   offset controls grouped and labeled by axis and unit.
2. Persist settings in a dedicated INI section using scalar values and a
   documented serialization for curve points (JSON is suitable for a single
   string value). Default missing or older settings to identity curves,
   conservative filter defaults, and zero offsets.
3. Validate and normalize settings at the UI/config boundary. Log invalid
   persisted data and use safe defaults for that setting; do not pass malformed
   values into the output process.
4. Add a `set_native_processing` command on `outputControlQueue` carrying one
   validated settings snapshot. Apply it in the output worker without
   restarting the shared-memory mapping, and reset filter state when needed.
5. On application startup and output-worker restart, resend the saved
   processing settings, just as protocol and rate settings are synchronized.
6. Persist and retain native settings when UDP is selected, but hide the
   settings button in UDP mode. Switching to UDP must bypass native processing
   immediately; switching back to a direct protocol uses the retained settings.

## Implementation sequence

1. **Specify conventions and interactions.** Use millimeters for translation
   and the FreeTrack shared-memory X/Y/Z axis conventions. Preserve the
   NPClient byte-level game-ID response-table encoding after processing.
2. **Build processing primitives.** Add curve validation/interpolation,
   adaptive-filter state, wrap-safe yaw handling, offsets, and a pure pose
   transformation API. Keep it independent of Qt, Windows shared memory, and
   process queues.
3. **Integrate the output worker.** Use monotonic output-worker sample times
   for velocity estimation, process only FreeTrack/NPClient poses, preserve
   latest-pose draining, and reset state at lifecycle/settings boundaries.
   Leave UDP data and packet serialization unchanged.
4. **Persist and expose settings.** Add the standalone output panel, validation,
   serialization, live settings command, startup synchronization, and
   documentation/help text.
5. **Test and tune.** Add deterministic unit, worker-routing, UI/persistence,
   and regression tests. Tune filter defaults using recorded or simulated
   jitter and deliberate movement, then validate in FreeTrack and NPClient
   games.

## Verification and acceptance criteria

- Identity curves, zero offsets, and filtering disabled produce the same
  direct-output pose as the current implementation.
- An edited yaw curve maps `+30°` to `+120°` (and the expected negative value
  for a symmetric curve); interpolation, endpoints, malformed curves, and
  finite-value checks have unit coverage for all axes.
- Static rotation offsets and all three translation offsets are visible in the
  shared-memory fields with verified signs and units; zero remains backward
  compatible.
- Adaptive filtering reduces the RMS of a 0.35° 13 Hz jitter test by at least
  half, and a 60° step at 120 Hz reaches 95% within 100 ms without overshoot.
  Yaw wraparound does not cause a spike; disabling/changing output resets
  filter state cleanly.
- FreeTrack and NPClient both receive the processed shared pose, and existing
  game response-table behavior and handshake remain intact.
- In UDP mode the processing component is not invoked, and byte-level tests
  confirm the packet contains the original fusion angles and unchanged zero
  translations.
- Preferences round-trip, missing/legacy values, invalid persisted curves,
  startup synchronization, and output-worker restart behavior are covered.
- Validate manually with at least one FreeTrack game and one NPClient game,
  including runtime switching to UDP and back.
