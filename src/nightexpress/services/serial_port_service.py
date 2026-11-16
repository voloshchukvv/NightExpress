"""USB-serial adapters, listed from OS metadata only.

Ports are never opened here: other devices may be attached to them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from serial.tools import list_ports

from nightexpress.models.entities import SerialPortInfo


def _from_pyserial(port: Any) -> SerialPortInfo:
    return SerialPortInfo(
        device=port.device,
        description=port.description or "",
        vid=port.vid,
        pid=port.pid,
        serial_number=port.serial_number or None,
        location=port.location or None,
    )


class SerialPortService:
    def __init__(self, enumerate_ports: Callable[[], Iterable[Any]] = list_ports.comports) -> None:
        self._enumerate = enumerate_ports

    def list_ports(self) -> list[SerialPortInfo]:
        """USB adapters only (Bluetooth and debug consoles have no VID/PID)."""
        ports = [_from_pyserial(p) for p in self._enumerate() if getattr(p, "vid", None)]
        return sorted(ports, key=lambda p: p.device)
