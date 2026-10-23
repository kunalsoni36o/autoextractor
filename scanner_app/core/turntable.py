from __future__ import annotations

from dataclasses import dataclass

import serial
from serial.tools import list_ports


@dataclass
class Turntable:
    port: str = "COM9"
    baudrate: int = 115200

    def __post_init__(self) -> None:
        self._serial: serial.Serial | None = None

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def probe(self) -> bool:
        """Open COM9 when it exists. Reads are non-blocking."""
        if self.connected:
            return True
        if not any(info.device.upper() == self.port.upper() for info in list_ports.comports()):
            return False
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=0, write_timeout=1)
            self._serial.reset_input_buffer()
            return True
        except (serial.SerialException, OSError):
            self._serial = None
            return False

    def disconnect(self) -> None:
        if self._serial:
            self._serial.close()
        self._serial = None

    def rotate_once(self) -> None:
        if not self.connected:
            raise RuntimeError(f"Turntable is not connected on {self.port}")
        self._serial.reset_input_buffer()
        self._serial.write(b"ROTATE\n")
        self._serial.flush()

    def read_lines(self) -> list[str]:
        if not self.connected:
            return []
        lines: list[str] = []
        while self._serial.in_waiting:
            line = self._serial.readline().decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)
        return lines
