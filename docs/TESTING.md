# Testing Orienta

Orienta's automated Python tests use the standard-library `unittest` runner.
They exercise fusion math, Qt panel behavior, output routing and serialization,
native-output processing, and per-user persistence. The GUI tests run with
Qt's offscreen platform, so they do not need a visible desktop or a running
Orienta instance.

## Run the Python suite

From the repository root in PowerShell, using the development virtual
environment:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\build\dev\.venv\Scripts\python.exe -m unittest discover -s src\tests -v
```

To run a focused module:

```powershell
.\build\dev\.venv\Scripts\python.exe -m unittest src.tests.test_native_output_processing -v
.\build\dev\.venv\Scripts\python.exe -m unittest src.tests.test_output_protocol_selection -v
```

The test files can also be run directly with the repository's selected Python
environment, provided the runtime requirements are installed. No third-party
test runner is required.

## Test module guide

| Module | Main coverage |
|---|---|
| `test_fusion_center_assist_and_bias.py` | Centre-assist engagement and sustained correction, sample-rate independence, thresholds and stationarity gates, three-axis gyro-bias calibration and worker behavior. |
| `test_fusion_orientation_hold.py` | Fusion-level pose hold, recentering, and smooth resume to live orientation. |
| `test_orientation_hold_input_modes.py` | Momentary versus toggle Hold Orientation behavior for buttons and shortcuts, including preferences. |
| `test_orientation_held_marker.py` | Visualization rendering for held-pose and recenter-guide markers. |
| `test_reset_orientation_toggle_follow.py` | Reset/recenter press and release timing, instantaneous mode, multiple held inputs, and fusion-worker command handling. |
| `test_native_output_processing.py` | Filter registry, One Euro, EMA, hysteresis, critically damped, and Accela-inspired behavior; response curves and limits; native processing and offsets; settings migration/round-trip; calibration prompts; output panel layout, themes, and controls. |
| `test_output_protocol_selection.py` | UDP, FreeTrack, and NPClient routing; live native-filter switching; UDP packet layout; FreeTrack shared-memory structure and game-response tables; client registration and restoration. |
| `test_user_data_paths.py` | Preferences and logs in per-user AppData, atomic preference writes, failed-save behavior, and packaged-path behavior. |

The output and native-processing tests use queues, temporary directories,
mocked sockets/worker backends, and Qt widgets. They test application-side
contracts; they do not launch a game or require an IMU.

## Windows-native client smoke tests

FreeTrack and NPClient DLL checks are separate from Python test discovery. From
the repository root, with the development environment installed, run:

```powershell
.\build\build_game_clients.bat
```

This builds x86 and x64 client DLLs and their native smoke-test consumers, then
runs the consumers to check the client ABI and shared-memory behavior. The
development setup and executable build scripts also invoke this native build
and smoke-test step.

Some Python tests that exercise real Windows shared-memory mappings or the
Windows registry are decorated with `skipUnless(sys.platform == "win32", ...)`.
They are skipped on non-Windows hosts; mocked routing, conversion, and
registration tests remain runnable there.

## What still needs manual integration testing

The automated suites do not verify the complete hardware-to-game path. Before
releasing or changing low-level output behavior, manually check the relevant
setup:

1. Connect the target IMU and confirm serial input and live orientation.
2. For UDP mode, confirm the configured destination receives the expected
   opentrack packet and that Orienta's native curves/filter/offsets are bypassed.
3. For direct FreeTrack and NPClient modes, verify registration, game
   discovery, live head movement, and runtime protocol switching in
   representative games.
4. Exercise screen-edge input-limit calibration while tracking data is live.

An automated unit or ABI smoke test passing is not evidence that a particular
game, driver, or sensor has been integration-tested.
