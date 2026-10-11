"""UDP transport backend for opentrack-compatible orientation packets."""

import math
import socket
import struct

from src.config.config import DEFAULT_UDP_IP, DEFAULT_UDP_PORT


class UDPOutput:
    """Own a UDP socket and send translation-first opentrack packets."""

    def __init__(self, host=DEFAULT_UDP_IP, port=DEFAULT_UDP_PORT):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.set_target(host, port)

    def set_target(self, host, port):
        """Update the destination used by subsequent packets."""
        self._target = (str(host), int(port))

    def send_pose(self, pose):
        """Send one finite [yaw, pitch, roll] pose."""
        yaw, pitch, roll = (float(pose[index]) for index in range(3))
        if not all(math.isfinite(value) for value in (yaw, pitch, roll)):
            raise ValueError("UDP pose must contain only finite values")

        packet = struct.pack("<6d", 0.0, 0.0, 0.0, yaw, pitch, roll)
        self._socket.sendto(packet, self._target)

    def close(self):
        self._socket.close()
