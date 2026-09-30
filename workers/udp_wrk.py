import socket
import struct
import time
import math

from config.config import (
    DEFAULT_UDP_IP,
    DEFAULT_UDP_PORT,
    OUTPUT_RATE_MIN_HZ,
    OUTPUT_RATE_MAX_HZ,
    FPS_REPORT_INTERVAL,
    QUEUE_PUT_TIMEOUT,
)
from util.error_utils import safe_queue_get, safe_queue_put


def run_worker(eulerQueue, stop_event, udp_ip=None, udp_port=None,
               controlQueue=None, statusQueue=None, logQueue=None):
    """Send orientation data to OpenTrack over UDP.

    Euler queue entries have the format ``[yaw, pitch, roll]``. OpenTrack
    requires six little-endian doubles in translation-first order, so the
    orientation-only application always emits zero translation:
    ``(0.0, 0.0, 0.0, yaw, pitch, roll)``.
    """
    from util.log_utils import log_error, log_info
    from util.timing_utils import enable_high_res_timer, disable_high_res_timer, raise_process_priority

    # Windows rounds time.sleep() up to the ~15.6ms system clock tick unless
    # this process requests higher resolution; must be set per-process.
    enable_high_res_timer()
    raise_process_priority()

    udp_ip = DEFAULT_UDP_IP if udp_ip is None else udp_ip
    udp_port = DEFAULT_UDP_PORT if udp_port is None else udp_port
    log_info(logQueue, "UDP Worker", f"Starting UDP sender to {udp_ip}:{udp_port}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_enabled = False
    send_count = 0
    last_rate_ts = time.time()
    # Counter to rate-limit non-finite sample logging
    nonfinite_counter = 0

    # Output rate cap: decouples the send rate from whatever rate fusion
    # happens to produce. Defaults to unlimited (0) so behavior matches the
    # original uncapped throughput; a cap is opt-in via ('set_rate', hz) so
    # enabling UDP never silently caps output below what fusion can produce.
    # Uses perf_counter (monotonic, high-resolution) rather than time.time(),
    # whose ~15.6ms Windows clock resolution would floor any sub-16ms
    # interval to ~60-80Hz regardless of the configured rate.
    output_rate_hz = 0
    min_send_interval = 0.0
    # Fixed schedule (not "last send + interval"): computing each deadline
    # from a running schedule instead of from when the previous packet
    # actually went out prevents per-iteration loop overhead from
    # accumulating into a systematic drift below the configured rate.
    next_send_time = time.perf_counter()

    try:
        while not stop_event.is_set():
            while True:
                cmd = safe_queue_get(controlQueue, timeout=0.0, default=None)
                if cmd is None:
                    break
                if not isinstance(cmd, (list, tuple)) or not cmd:
                    continue
                if cmd[0] == "set_udp" and len(cmd) >= 3:
                    udp_ip = str(cmd[1])
                    udp_port = int(cmd[2])
                    log_info(logQueue, "UDP Worker", f"UDP target updated to {udp_ip}:{udp_port}")
                elif cmd[0] == "udp_enable" and len(cmd) >= 2:
                    udp_enabled = bool(cmd[1])
                    status = "enabled" if udp_enabled else "disabled"
                    log_info(logQueue, "UDP Worker", f"UDP sending {status}")
                    if udp_enabled:
                        # Start the schedule fresh so enabling never waits on
                        # a stale deadline left over from before it was off.
                        next_send_time = time.perf_counter()
                elif cmd[0] == "set_rate" and len(cmd) >= 2:
                    try:
                        requested = float(cmd[1])
                    except (TypeError, ValueError):
                        requested = None
                    if requested and requested > 0:
                        output_rate_hz = max(OUTPUT_RATE_MIN_HZ, min(OUTPUT_RATE_MAX_HZ, requested))
                        min_send_interval = 1.0 / output_rate_hz
                        log_info(logQueue, "UDP Worker", f"Output rate capped at {output_rate_hz:.1f} Hz")
                    else:
                        output_rate_hz = 0
                        min_send_interval = 0.0
                        log_info(logQueue, "UDP Worker", "Output rate uncapped")
                    # Rate changed: restart the schedule rather than reuse a
                    # deadline computed under the old interval.
                    next_send_time = time.perf_counter()

            # Wait for the next sample rather than non-blocking polling + a
            # separate sleep - the same anti-pattern fixed in fusion_wrk.py's
            # serialQueue read: a blocking get() returns as soon as data
            # arrives, so this tracks fusion's actual push rate instead of
            # being capped by repeated empty-queue misses and sleep rounding.
            latest = safe_queue_get(eulerQueue, timeout=0.05, default=None)
            if latest is not None:
                # Drain the ENTIRE backlog to the true latest sample. A capped
                # drain would only claw back a few samples per iteration after
                # this process was starved of CPU time (e.g. by a demanding
                # game), forcing many iterations to grind through stale data
                # before catching up - eulerQueue's maxsize bounds this loop.
                while True:
                    newer = safe_queue_get(eulerQueue, timeout=0.0, default=None)
                    if newer is None:
                        break
                    latest = newer

            if latest is None:
                continue

            try:
                yaw, pitch, roll = (float(latest[0]), float(latest[1]), float(latest[2]))

                # Drop non-finite samples to avoid sending NaN/Inf over UDP
                if not (math.isfinite(yaw) and math.isfinite(pitch) and math.isfinite(roll)):
                    nonfinite_counter += 1
                    if nonfinite_counter % 50 == 0:
                        log_error(logQueue, "UDP Worker", f"Dropping non-finite Euler sample #{nonfinite_counter}: yaw={yaw}, pitch={pitch}, roll={roll}")
                    # Skip this frame
                    continue

                now = time.time()
                now_perf = time.perf_counter()
                if udp_enabled and (min_send_interval <= 0.0 or now_perf >= next_send_time):
                    sock.sendto(
                        struct.pack("<6d", 0.0, 0.0, 0.0, yaw, pitch, roll),
                        (udp_ip, udp_port),
                    )
                    send_count += 1
                    if min_send_interval > 0.0:
                        next_send_time += min_send_interval
                        # If we fell behind (e.g. a data gap), resync to now
                        # instead of bursting out a catch-up backlog.
                        if next_send_time < now_perf:
                            next_send_time = now_perf
                elif udp_enabled and min_send_interval > 0.0:
                    remaining = next_send_time - now_perf
                    if remaining > 0:
                        time.sleep(remaining)

                elapsed = now - last_rate_ts
                if elapsed >= FPS_REPORT_INTERVAL:
                    safe_queue_put(
                        statusQueue,
                        ("send_rate", send_count / elapsed if elapsed > 0 else 0.0),
                        timeout=QUEUE_PUT_TIMEOUT,
                    )
                    send_count = 0
                    last_rate_ts = now
            except (IndexError, TypeError, ValueError, OSError) as error:
                log_error(logQueue, "UDP Worker", f"Pack/send error: {error}")
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        log_info(logQueue, "UDP Worker", "Stopped")
        disable_high_res_timer()
