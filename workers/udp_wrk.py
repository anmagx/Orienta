import socket
import struct
import time

from config.config import (
    DEFAULT_UDP_IP,
    DEFAULT_UDP_PORT,
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

    udp_ip = DEFAULT_UDP_IP if udp_ip is None else udp_ip
    udp_port = DEFAULT_UDP_PORT if udp_port is None else udp_port
    log_info(logQueue, "UDP Worker", f"Starting UDP sender to {udp_ip}:{udp_port}")
    print(f"[UDP Worker] Starting. Sending to {udp_ip}:{udp_port}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_enabled = False
    send_count = 0
    last_rate_ts = time.time()

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

            latest = None
            for _ in range(10):
                sample = safe_queue_get(eulerQueue, timeout=0.0, default=None)
                if sample is None:
                    break
                latest = sample

            if latest is None:
                time.sleep(0.001)
                continue

            try:
                yaw, pitch, roll = (float(latest[0]), float(latest[1]), float(latest[2]))
                if udp_enabled:
                    sock.sendto(
                        struct.pack("<6d", 0.0, 0.0, 0.0, yaw, pitch, roll),
                        (udp_ip, udp_port),
                    )
                    send_count += 1

                now = time.time()
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
                print(f"[UDP Worker] Pack/send error: {error}")
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        log_info(logQueue, "UDP Worker", "Stopped")
        print("[UDP Worker] Stopped.")
