# Orienta Infrastructure

Orienta is a multiprocess Python application for real-time 3DOF IMU
head-tracking.

```text
IMU serial input -> SerialWorker -> FusionWorker -> UDPWorker -> OpenTrack
                                   |
                                   +-> GUIWorker

GUIWorker -> SerialWorker, FusionWorker, UDPWorker, InputWorker
```

`ProcessHandler` starts the GUI, serial, fusion, UDP, and input workers. It
also owns bounded inter-process queues, the shared shutdown event, a log writer,
and worker monitoring/restart logic.

The primary queue contracts are:

- `serialQueue`: raw IMU CSV strings from serial acquisition to fusion.
- `eulerQueue`: `[yaw, pitch, roll]` values from fusion to UDP.
- `eulerDisplayQueue`: the same orientation values for the GUI.
- `serialControlQueue`: serial start/stop commands.
- `controlQueue`: fusion calibration, reset, filter, and settings commands.
- `udpControlQueue`: UDP target and enablement commands.

OpenTrack output is always six little-endian doubles:

```python
struct.pack("<6d", 0.0, 0.0, 0.0, yaw, pitch, roll)
```

See [PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md) for the maintained architecture,
configuration, lifecycle, queue contracts, and refactoring constraints.
