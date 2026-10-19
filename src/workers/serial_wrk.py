import serial
import time
import threading
from serial import SerialException

from src.config.config import (
    DEFAULT_SERIAL_BAUD,
    SERIAL_RETRY_DELAY,
    SERIAL_RETRY_MAX_DELAY,
    SERIAL_TIMEOUT,
    FPS_REPORT_INTERVAL,
    QUEUE_PUT_TIMEOUT
)
from src.util.error_utils import safe_queue_put, safe_queue_get
from src.util.error_utils import parse_imu_line
from src.util.serial_discovery import detect_orienta_sensor
from src.util.sensor_settings import sensor_settings_command


def _send_sensor_command(ser, cmd, uiStatusQueue, logQueue):
    from src.util.log_utils import log_warning

    try:
        if len(cmd) < 2:
            raise ValueError("Missing sensor command")
        operation = cmd[1]
        values = cmd[2] if operation == "SET" and len(cmd) > 2 else None
        payload = sensor_settings_command(operation, values)
        ser.write(payload)
    except ValueError as error:
        message = f"Invalid sensor settings request: {error}"
        safe_queue_put(
            uiStatusQueue,
            ("sensor_settings_error", message),
            timeout=QUEUE_PUT_TIMEOUT,
        )
        log_warning(logQueue, "Serial Worker", message)
        return False
    except (OSError, SerialException) as error:
        message = f"Could not send sensor settings request: {error}"
        safe_queue_put(
            uiStatusQueue,
            ("sensor_settings_error", message),
            timeout=QUEUE_PUT_TIMEOUT,
        )
        log_warning(logQueue, "Serial Worker", message)
        raise


def open_serial(port, baud):
    """Attempt one serial open; retry scheduling belongs to the worker loop."""
    try:
        return serial.Serial(port, baud, timeout=SERIAL_TIMEOUT)
    except (OSError, SerialException, ValueError):
        return None


def _publish_connection_status(status_queue, ui_status_queue, state):
    for target_queue in (status_queue, ui_status_queue):
        if target_queue is not None:
            safe_queue_put(
                target_queue,
                ("serial_connection", state),
                timeout=QUEUE_PUT_TIMEOUT,
            )


def _run_detection(preferred_baud, cancel_event, ui_status_queue, log_queue):
    from src.util.log_utils import log_error

    try:
        result = detect_orienta_sensor(preferred_baud, cancel_event)
    except Exception as error:
        log_error(log_queue, "Serial Worker", f"Sensor detection failed: {error}")
        result = ("error", str(error))
    else:
        if cancel_event.is_set():
            result = ("cancelled", None)
        elif result is None:
            result = ("not_found", None)
        else:
            result = ("found", result)
    safe_queue_put(
        ui_status_queue,
        ("serial_detection", result),
        timeout=QUEUE_PUT_TIMEOUT,
    )


def serial_thread(messageQueue=None, serialQueue=None, serialDisplayQueue=None, stop_event=None, serialControlQueue=None, statusQueue=None, logQueue=None, uiStatusQueue=None):
    """Main serial thread controlled by commands from `serialControlQueue`.

    Commands expected on serialControlQueue:
      ('start', port, baud)  -> open and begin reading
      ('stop',)              -> close port and stop reading
      ('detect', baud)        -> probe ports for a valid Orienta sensor
      ('cancel_detection',)   -> cancel an in-progress probe

    Failed opens and read disconnects retry the selected port with bounded
    backoff. The main loop remains responsive to stop and detection commands.
    """
    from src.util.log_utils import log_info, log_error, log_warning
    from src.util.timing_utils import raise_process_priority

    raise_process_priority()
    log_info(logQueue, "Serial Worker", "Serial thread started")

    ser = None
    port = None
    baud = DEFAULT_SERIAL_BAUD
    connect_requested = False
    next_connect_time = 0.0
    retry_delay = SERIAL_RETRY_DELAY
    pending_start = None

    mps_count = 0
    last_mps_time = time.time()
    report_interval = FPS_REPORT_INTERVAL
    last_activity_report = 0
    activity_report_interval = 2.0
    malformed_lines = 0
    detection_thread = None
    detection_cancel_event = None

    def close_serial():
        nonlocal ser
        if ser is not None:
            current, ser = ser, None
            try:
                current.close()
            except (OSError, SerialException) as error:
                log_warning(logQueue, "Serial Worker", f"Could not close serial port: {error}")

    def begin_reconnect(error):
        nonlocal connect_requested
        nonlocal next_connect_time, retry_delay
        port_name = getattr(ser, "port", port) or "unknown"
        log_error(logQueue, "Serial Worker", f"Serial connection lost on {port_name}: {error}")
        safe_queue_put(
            messageQueue,
            f"\nConnection lost on {port_name}. Retrying...",
            timeout=QUEUE_PUT_TIMEOUT,
        )
        close_serial()
        connect_requested = True
        retry_delay = SERIAL_RETRY_DELAY
        next_connect_time = time.perf_counter() + retry_delay
        _publish_connection_status(statusQueue, uiStatusQueue, "reconnecting")

    try:
        while stop_event is None or not stop_event.is_set():
            while True:
                cmd = safe_queue_get(serialControlQueue, timeout=0.0, default=None)
                if cmd is None:
                    break
                if not isinstance(cmd, (list, tuple)) or not cmd:
                    continue

                if cmd[0] == "start" and len(cmd) >= 3:
                    try:
                        requested_baud = int(cmd[2])
                    except (TypeError, ValueError, OverflowError):
                        requested_baud = DEFAULT_SERIAL_BAUD
                    requested_port = str(cmd[1]).strip()
                    if not requested_port:
                        log_warning(logQueue, "Serial Worker", "Ignoring start request with an empty port")
                        continue
                    if detection_cancel_event is not None:
                        detection_cancel_event.set()
                    if detection_thread is not None and detection_thread.is_alive():
                        log_warning(
                            logQueue,
                            "Serial Worker",
                            "Deferring serial start until sensor detection is cancelled",
                        )
                        pending_start = (requested_port, requested_baud)
                        continue
                    close_serial()
                    port, baud = requested_port, requested_baud
                    connect_requested = True
                    retry_delay = SERIAL_RETRY_DELAY
                    next_connect_time = time.perf_counter()
                    safe_queue_put(
                        messageQueue,
                        f"Connecting to {port} at {baud} baud...",
                        timeout=QUEUE_PUT_TIMEOUT,
                    )
                    _publish_connection_status(statusQueue, uiStatusQueue, "starting")

                elif cmd[0] == "stop":
                    pending_start = None
                    connect_requested = False
                    close_serial()
                    if detection_cancel_event is not None:
                        detection_cancel_event.set()
                    _publish_connection_status(statusQueue, uiStatusQueue, "stopped")
                    safe_queue_put(
                        messageQueue,
                        f"\nSerial on {port or 'selected port'} stopped.",
                        timeout=QUEUE_PUT_TIMEOUT,
                    )

                elif cmd[0] == "detect":
                    if ser is not None or connect_requested:
                        safe_queue_put(
                            uiStatusQueue,
                            ("serial_detection", (
                                "error",
                                "Stop the serial connection before detecting a sensor.",
                            )),
                            timeout=QUEUE_PUT_TIMEOUT,
                        )
                    elif detection_thread is not None and detection_thread.is_alive():
                        safe_queue_put(
                            uiStatusQueue,
                            ("serial_detection", ("error", "Detection is already running.")),
                            timeout=QUEUE_PUT_TIMEOUT,
                        )
                    else:
                        try:
                            preferred_baud = int(cmd[1]) if len(cmd) >= 2 else baud
                        except (TypeError, ValueError, OverflowError):
                            preferred_baud = baud
                        detection_cancel_event = threading.Event()
                        detection_thread = threading.Thread(
                            target=_run_detection,
                            args=(
                                preferred_baud,
                                detection_cancel_event,
                                uiStatusQueue,
                                logQueue,
                            ),
                            name="SensorDetection",
                            daemon=True,
                        )
                        detection_thread.start()

                elif cmd[0] == "cancel_detection":
                    if detection_cancel_event is not None:
                        detection_cancel_event.set()

                elif cmd[0] == "sensor_command":
                    if ser is None:
                        safe_queue_put(
                            uiStatusQueue,
                            (
                                "sensor_settings_error",
                                "Sensor settings require an active serial connection.",
                            ),
                            timeout=QUEUE_PUT_TIMEOUT,
                        )
                    else:
                        try:
                            _send_sensor_command(ser, cmd, uiStatusQueue, logQueue)
                        except (OSError, SerialException) as error:
                            begin_reconnect(error)

            if (
                detection_thread is not None
                and not detection_thread.is_alive()
                and pending_start is not None
            ):
                port, baud = pending_start
                pending_start = None
                connect_requested = True
                retry_delay = SERIAL_RETRY_DELAY
                next_connect_time = time.perf_counter()
                _publish_connection_status(statusQueue, uiStatusQueue, "starting")

            if ser is None and connect_requested:
                now = time.perf_counter()
                if now >= next_connect_time:
                    ser = open_serial(port, baud)
                    if ser is None:
                        log_warning(
                            logQueue,
                            "Serial Worker",
                            f"Could not open {port} at {baud} baud; retrying in {retry_delay:g}s",
                        )
                        next_connect_time = now + retry_delay
                        retry_delay = min(
                            retry_delay * 2.0,
                            SERIAL_RETRY_MAX_DELAY,
                        )
                    else:
                        retry_delay = SERIAL_RETRY_DELAY
                        safe_queue_put(
                            messageQueue,
                            f"Connected to {port} at {baud} baud.",
                            timeout=QUEUE_PUT_TIMEOUT,
                        )
                        _publish_connection_status(
                            statusQueue,
                            uiStatusQueue,
                            "connected",
                        )
                        mps_count = 0
                        last_mps_time = time.time()
                        last_activity_report = 0.0
                else:
                    time.sleep(min(0.05, max(0.0, next_connect_time - now)))
                    continue

            if ser is None:
                time.sleep(0.05)
                continue

            try:
                if not ser.in_waiting:
                    time.sleep(0.001)
                    continue

                data = ser.readline().decode("utf-8", errors="ignore").strip()
                if not data:
                    continue

                safe_queue_put(
                    serialDisplayQueue,
                    data,
                    timeout=QUEUE_PUT_TIMEOUT,
                    queue_name="serialDisplayQueue",
                )
                if data.startswith("!SENSOR,"):
                    safe_queue_put(
                        uiStatusQueue,
                        ("sensor_settings_response", data),
                        timeout=QUEUE_PUT_TIMEOUT,
                    )
                    continue

                try:
                    parse_imu_line(data)
                except ValueError as error:
                    malformed_lines += 1
                    if malformed_lines % 100 == 0:
                        log_warning(
                            logQueue,
                            "Serial Worker",
                            f"Ignoring non-IMU serial lines: {malformed_lines} total; last error: {error}",
                        )
                    continue

                if not safe_queue_put(
                    serialQueue,
                    data,
                    timeout=QUEUE_PUT_TIMEOUT,
                    log_failures=True,
                    context="Serial data",
                    queue_name="serialQueue",
                ):
                    drop_count = getattr(ser, "_drop_count", 0) + 1
                    setattr(ser, "_drop_count", drop_count)
                    if drop_count % 50 == 0:
                        log_error(
                            logQueue,
                            "Serial Worker",
                            f"Dropped {drop_count} frames due to full serialQueue",
                        )

                now = time.time()
                if now - last_activity_report >= activity_report_interval:
                    for target_queue in (statusQueue, uiStatusQueue):
                        if target_queue is not None:
                            safe_queue_put(
                                target_queue,
                                ("serial_data", True),
                                timeout=QUEUE_PUT_TIMEOUT,
                            )
                    last_activity_report = now

                mps_count += 1
                elapsed = now - last_mps_time
                if elapsed >= report_interval:
                    safe_queue_put(
                        statusQueue,
                        ("msg_rate", mps_count / elapsed if elapsed > 0 else 0.0),
                        timeout=QUEUE_PUT_TIMEOUT,
                    )
                    mps_count = 0
                    last_mps_time = now

            except KeyboardInterrupt:
                break
            except (OSError, SerialException) as error:
                begin_reconnect(error)

    finally:
        connect_requested = False
        if detection_cancel_event is not None:
            detection_cancel_event.set()
        close_serial()
        if detection_thread is not None and detection_thread.is_alive():
            detection_thread.join(timeout=1.0)
        log_info(logQueue, "Serial Worker", "Serial thread stopped")

def run_worker(messageQueue, serialQueue, serialDisplayQueue, stop_event=None, serialControlQueue=None, statusQueue=None, logQueue=None, uiStatusQueue=None):
    from src.util.log_utils import log_info
    try:
        serial_thread(messageQueue, serialQueue, serialDisplayQueue, stop_event, serialControlQueue, statusQueue, logQueue, uiStatusQueue)
    except KeyboardInterrupt:
        log_info(logQueue, "Serial Worker", "Serial worker interrupted during shutdown")
    finally:
        log_info(logQueue, "Serial Worker", "Serial worker stopped")
        log_info(logQueue, "Serial Worker", "Stopped.")
