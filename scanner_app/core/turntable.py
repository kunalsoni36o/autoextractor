from __future__ import annotations

from dataclasses import dataclass

import serial
from serial.tools import list_ports


@dataclass
class Turntable:
    port: str | None = None
    baudrate: int = 115200

    def __post_init__(self) -> None:
        self._serial: serial.Serial | None = None

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def probe(self) -> bool:
        """Open the configured port, or discover a likely USB serial port."""
        if self.connected:
            return True
        ports = list(list_ports.comports())
        if self.port:
            ports = [info for info in ports if info.device.upper() == self.port.upper()]
        else:
            preferred = ("ESP32", "USB-SERIAL", "USB SERIAL", "CP210", "CH340", "UART")
            matching = [info for info in ports if any(term in f"{info.description} {info.hwid}".upper() for term in preferred)]
            ports = matching or ports
        if not ports:
            return False
        for info in ports:
            try:
                candidate = serial.Serial(info.device, self.baudrate, timeout=0, write_timeout=1)
                candidate.reset_input_buffer()
                self.port = info.device
                self._serial = candidate
                return True
            except (serial.SerialException, OSError):
                continue
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
        try:
            self._serial.write(b"ROTATE\n")
            self._serial.flush()
        except (serial.SerialException, OSError) as exc:
            self.disconnect()
            raise RuntimeError(f"Serial write failed on {self.port}. Reconnect the ESP32 and retry: {exc}") from exc

    def read_lines(self) -> list[str]:
        if not self.connected:
            return []
        lines: list[str] = []
        while self._serial.in_waiting:
            line = self._serial.readline().decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)
        return lines
