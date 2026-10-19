"""Probe serial ports for a sensor that reports a valid Orienta configuration."""

import time

import serial
from serial.tools import list_ports

from src.config.config import SERIAL_BAUD_RATES
from src.util.sensor_settings import (
    parse_sensor_settings_response,
    sensor_settings_command,
)


PROBE_TIMEOUT_S = 0.75
PROBE_QUERY_INTERVAL_S = 0.25
PROBE_READ_TIMEOUT_S = 0.05


def detect_orienta_sensor(preferred_baud, cancel_event=None):
    """Return ``(port, baud, settings)`` for the first valid sensor found.

    Opening a USB serial port may reset its attached device. This probe is
    intended for an explicit user action, not background polling.
    """
    try:
        preferred_baud = int(preferred_baud)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Preferred serial baud rate must be an integer") from error

    baud_rates = (preferred_baud,) + tuple(
        baud for baud in SERIAL_BAUD_RATES if baud != preferred_baud
    )
    for port_info in list_ports.comports():
        device = getattr(port_info, "device", None)
        if not device:
            continue
        for baud in baud_rates:
            if cancel_event is not None and cancel_event.is_set():
                return None
            try:
                with serial.Serial(
                    device,
                    baud,
                    timeout=PROBE_READ_TIMEOUT_S,
                    write_timeout=0.1,
                ) as connection:
                    deadline = time.monotonic() + PROBE_TIMEOUT_S
                    next_query = 0.0
                    line_buffer = bytearray()
                    while time.monotonic() < deadline:
                        if cancel_event is not None and cancel_event.is_set():
                            return None
                        now = time.monotonic()
                        if now >= next_query:
                            connection.write(sensor_settings_command("GET"))
                            next_query = now + PROBE_QUERY_INTERVAL_S

                        line_buffer.extend(connection.readline())
                        while b"\n" in line_buffer:
                            raw_line, _, remainder = line_buffer.partition(b"\n")
                            line_buffer = bytearray(remainder)
                            response = raw_line.decode(
                                "utf-8",
                                errors="ignore",
                            ).strip()
                            if not response.startswith("!SENSOR,SETTINGS,"):
                                continue
                            try:
                                settings = parse_sensor_settings_response(response)
                            except ValueError:
                                continue
                            return device, baud, settings
                        if len(line_buffer) > 1024:
                            line_buffer.clear()
            except (OSError, serial.SerialException, ValueError):
                continue
    return None
