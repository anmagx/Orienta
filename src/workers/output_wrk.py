"""Route the selected orientation output through one backend worker."""

import math
import time

from src.config.config import (
    DEFAULT_OUTPUT_PROTOCOL,
    DEFAULT_UDP_IP,
    DEFAULT_UDP_PORT,
    OUTPUT_PROTOCOL_FREETRACK,
    OUTPUT_PROTOCOL_NPCLIENT,
    OUTPUT_PROTOCOL_UDP,
    OUTPUT_RATE_MAX_HZ,
    OUTPUT_RATE_MIN_HZ,
    QUEUE_PUT_TIMEOUT,
)
from src.util.error_utils import safe_queue_get, safe_queue_put
from src.util.tracking_sample import TrackingSample


OUTPUT_PROTOCOLS = (
    OUTPUT_PROTOCOL_UDP,
    OUTPUT_PROTOCOL_FREETRACK,
    OUTPUT_PROTOCOL_NPCLIENT,
)


def run_worker(pose_queue, control_queue, stop_event, status_queue=None,
               log_queue=None):
    """Consume the latest pose and send it through the selected output backend."""
    from src.util.log_utils import log_error, log_info, log_warning
    from src.util.timing_utils import (
        disable_high_res_timer,
        enable_high_res_timer,
        raise_process_priority,
    )

    from src.outputs.native_output_processing import NativeOutputProcessor
    from src.outputs.shared_memory_output import FreeTrackSharedMemory
    from src.outputs.udp_output import UDPOutput

    enable_high_res_timer()
    raise_process_priority()

    protocol = DEFAULT_OUTPUT_PROTOCOL
    udp_host = DEFAULT_UDP_IP
    udp_port = DEFAULT_UDP_PORT
    output_enabled = False
    backend = None
    send_interval = 0.0
    next_send_time = time.perf_counter()
    sent_count = 0
    invalid_count = 0
    last_rate_time = time.perf_counter()
    native_processor = NativeOutputProcessor()

    def close_backend():
        nonlocal backend
        if backend is not None:
            current, backend = backend, None
            current.close()

    def start_backend():
        nonlocal backend
        if protocol == OUTPUT_PROTOCOL_UDP:
            backend = UDPOutput(udp_host, udp_port)
        else:
            backend = FreeTrackSharedMemory()

    def set_enabled(enabled):
        nonlocal output_enabled, next_send_time
        if enabled == output_enabled:
            return
        if enabled:
            start_backend()
            native_processor.reset()
            output_enabled = True
            next_send_time = time.perf_counter()
            if protocol in (OUTPUT_PROTOCOL_FREETRACK, OUTPUT_PROTOCOL_NPCLIENT):
                if backend.mapping_already_exists:
                    log_info(
                        log_queue,
                        "Output Worker",
                        "Reusing shared-memory mapping held by a game reader",
                    )
            log_info(
                log_queue,
                "Output Worker",
                f"{protocol} output enabled",
            )
            safe_queue_put(
                status_queue,
                ("output_status", "active"),
                timeout=QUEUE_PUT_TIMEOUT,
            )
        else:
            output_enabled = False
            close_backend()
            native_processor.reset()
            log_info(
                log_queue,
                "Output Worker",
                f"{protocol} output disabled",
            )
            safe_queue_put(
                status_queue,
                ("output_status", "inactive"),
                timeout=QUEUE_PUT_TIMEOUT,
            )

    log_info(log_queue, "Output Worker", "Started")
    safe_queue_put(
        status_queue,
        ("output_status", "ready"),
        timeout=QUEUE_PUT_TIMEOUT,
    )

    try:
        while not stop_event.is_set():
            while True:
                command = safe_queue_get(control_queue, timeout=0.0, default=None)
                if command is None:
                    break
                if not isinstance(command, (tuple, list)) or not command:
                    log_warning(
                        log_queue,
                        "Output Worker",
                        f"Ignoring invalid command: {command}",
                    )
                    continue

                if command[0] == "set_protocol" and len(command) >= 2:
                    requested = command[1]
                    if requested not in OUTPUT_PROTOCOLS:
                        log_warning(
                            log_queue,
                            "Output Worker",
                            f"Ignoring unsupported output protocol: {requested}",
                        )
                        continue
                    if requested != protocol:
                        try:
                            if output_enabled:
                                set_enabled(False)
                        except (OSError, RuntimeError) as error:
                            log_error(
                                log_queue,
                                "Output Worker",
                                f"Could not close {protocol} backend: {error}",
                            )
                            safe_queue_put(
                                status_queue,
                                ("output_status", "error"),
                                timeout=QUEUE_PUT_TIMEOUT,
                            )
                            continue
                        protocol = requested
                        native_processor.reset()
                        log_info(
                            log_queue,
                            "Output Worker",
                            f"Output protocol set to {protocol}",
                        )
                elif command[0] == "set_udp" and len(command) >= 3:
                    try:
                        udp_host = str(command[1]).strip()
                        udp_port = int(command[2])
                        if not udp_host or not 1 <= udp_port <= 65535:
                            raise ValueError("UDP host/port is invalid")
                    except (TypeError, ValueError) as error:
                        log_warning(
                            log_queue,
                            "Output Worker",
                            f"Ignoring invalid UDP target: {error}",
                        )
                        continue
                    if protocol == OUTPUT_PROTOCOL_UDP and output_enabled:
                        backend.set_target(udp_host, udp_port)
                    log_info(
                        log_queue,
                        "Output Worker",
                        f"UDP target updated to {udp_host}:{udp_port}",
                    )
                elif command[0] == "set_enabled" and len(command) >= 2:
                    try:
                        set_enabled(bool(command[1]))
                    except (OSError, RuntimeError, ValueError, OverflowError) as error:
                        output_enabled = False
                        try:
                            close_backend()
                        except (OSError, RuntimeError, OverflowError) as close_error:
                            log_error(
                                log_queue,
                                "Output Worker",
                                f"Could not close failed {protocol} backend: {close_error}",
                            )
                        log_error(
                            log_queue,
                            "Output Worker",
                            f"Could not start {protocol} output: {error}",
                        )
                        safe_queue_put(
                            status_queue,
                            ("output_status", "error"),
                            timeout=QUEUE_PUT_TIMEOUT,
                        )
                elif command[0] == "set_rate" and len(command) >= 2:
                    try:
                        requested_rate = float(command[1])
                    except (TypeError, ValueError):
                        requested_rate = 0.0
                    if math.isfinite(requested_rate) and requested_rate > 0:
                        output_rate_hz = max(
                            OUTPUT_RATE_MIN_HZ,
                            min(OUTPUT_RATE_MAX_HZ, requested_rate),
                        )
                        send_interval = 1.0 / output_rate_hz
                        log_info(
                            log_queue,
                            "Output Worker",
                            f"UDP output capped at {output_rate_hz:.1f} Hz",
                        )
                    else:
                        send_interval = 0.0
                        log_info(
                            log_queue,
                            "Output Worker",
                            "UDP output uncapped",
                        )
                    next_send_time = time.perf_counter()
                elif command[0] == "set_native_processing" and len(command) >= 2:
                    try:
                        native_processor.set_settings(command[1])
                    except (TypeError, ValueError, OverflowError) as error:
                        log_warning(
                            log_queue,
                            "Output Worker",
                            f"Ignoring invalid native output settings: {error}",
                        )
                        continue
                    log_info(
                        log_queue,
                        "Output Worker",
                        "Native output processing settings updated",
                    )
                else:
                    log_warning(
                        log_queue,
                        "Output Worker",
                        f"Ignoring unknown command: {command}",
                    )

            batch = safe_queue_get(pose_queue, timeout=0.02, default=None)
            batches = []
            if batch is not None:
                batches.append(batch)
                while True:
                    newer_batch = safe_queue_get(
                        pose_queue,
                        timeout=0.0,
                        default=None,
                    )
                    if newer_batch is None:
                        break
                    batches.append(newer_batch)

            if batches and output_enabled and backend is not None:
                try:
                    pose = None
                    for batch in batches:
                        for sample in batch:
                            if not isinstance(sample, TrackingSample):
                                raise ValueError("Expected a sensor-timestamped tracking sample")
                            if not (
                                len(sample.angles) == len(sample.rates) == 3
                                and all(math.isfinite(value) for value in (
                                    sample.timestamp, *sample.angles, *sample.rates
                                ))
                            ):
                                raise ValueError("Tracking sample must contain finite time, angles and rates")
                            if sample.reset_filter:
                                native_processor.reset()
                            pose = (
                                sample.angles
                                if protocol == OUTPUT_PROTOCOL_UDP
                                else native_processor.process(
                                    sample.angles, sample.timestamp, sample.rates
                                )
                            )
                    if pose is None:
                        continue

                    now = time.perf_counter()
                    send_due = (
                        protocol != OUTPUT_PROTOCOL_UDP
                        or send_interval <= 0
                        or now >= next_send_time
                    )
                    if send_due:
                        backend.send_pose(pose)
                        sent_count += 1
                        if protocol == OUTPUT_PROTOCOL_UDP and send_interval > 0:
                            next_send_time += send_interval
                            if next_send_time < now:
                                next_send_time = now
                    elif (
                        protocol == OUTPUT_PROTOCOL_UDP
                        and send_interval > 0
                    ):
                        time.sleep(max(0.0, next_send_time - now))
                except (IndexError, TypeError, ValueError) as error:
                    invalid_count += 1
                    if invalid_count % 50 == 0:
                        log_warning(
                            log_queue,
                            "Output Worker",
                            f"Dropping invalid pose #{invalid_count}: {error}",
                        )
                except (OSError, RuntimeError, OverflowError) as error:
                    log_error(
                        log_queue,
                        "Output Worker",
                        f"{protocol} output failed: {error}",
                    )
                    output_enabled = False
                    try:
                        close_backend()
                    except (OSError, RuntimeError, OverflowError) as close_error:
                        log_error(
                            log_queue,
                            "Output Worker",
                            f"Could not close failed {protocol} backend: {close_error}",
                        )
                    safe_queue_put(
                        status_queue,
                        ("output_status", "error"),
                        timeout=QUEUE_PUT_TIMEOUT,
                    )

            now = time.perf_counter()
            elapsed = now - last_rate_time
            if elapsed >= 1.0:
                rate = sent_count / elapsed if elapsed > 0 else 0.0
                safe_queue_put(status_queue, ("output_send_rate", rate), timeout=QUEUE_PUT_TIMEOUT)
                sent_count = 0
                last_rate_time = now
    finally:
        output_enabled = False
        try:
            close_backend()
        except (OSError, RuntimeError, OverflowError) as error:
            log_error(
                log_queue,
                "Output Worker",
                f"Could not close output backend: {error}",
            )
        safe_queue_put(
            status_queue,
            ("output_status", "inactive"),
            timeout=QUEUE_PUT_TIMEOUT,
        )
        log_info(log_queue, "Output Worker", "Stopped")
        disable_high_res_timer()
