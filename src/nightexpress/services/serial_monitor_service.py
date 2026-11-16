"""Serial monitor connection: a background reader thread plus thread-safe writes.

Only the port the user picked is opened. The frame format is the usual 8N1 of user firmware
(the bootloader's 8E1 is a separate thing, see serial_bootloader.py).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, Signal

READ_TIMEOUT_S = 0.05
READ_CHUNK = 4096


class MonitorPort(Protocol):
    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int | None: ...

    def close(self) -> None: ...


def open_monitor_port(device: str, baud_rate: int) -> MonitorPort:
    import serial  # pyserial

    return serial.Serial(device, baud_rate, timeout=READ_TIMEOUT_S, write_timeout=1.0)


class _Emitter(QObject):
    data = Signal(bytes)
    lost = Signal(str, object)  # reason, the stop event of the reader that failed


class SerialMonitorService(QObject):
    data_received = Signal(bytes)
    connection_lost = Signal(str)

    def __init__(
        self,
        open_port: Callable[[str, int], MonitorPort] = open_monitor_port,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._open_port = open_port
        self._emitter = _Emitter(self)
        self._emitter.data.connect(self.data_received)
        self._emitter.lost.connect(self._on_lost)
        self._port: MonitorPort | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self.device: str | None = None

    @property
    def is_open(self) -> bool:
        return self._port is not None

    def open(self, device: str, baud_rate: int) -> None:
        """Raises OSError / serial.SerialException if the port cannot be opened."""
        self.close()
        port = self._open_port(device, baud_rate)
        self._port, self.device = port, device
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._read_loop, args=(port, self._stop), name="serial-monitor", daemon=True
        )
        self._thread.start()

    def write(self, data: bytes) -> None:
        port = self._port
        if port is None:
            raise RuntimeError("the serial monitor is not connected")
        with self._write_lock:
            port.write(data)

    def close(self) -> None:
        port, thread = self._port, self._thread
        self._port, self._thread, self.device = None, None, None
        self._stop.set()
        if thread is not None:
            thread.join(timeout=1.0)
        if port is not None:
            with contextlib.suppress(OSError, ValueError):
                port.close()

    def _read_loop(self, port: MonitorPort, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                data = port.read(READ_CHUNK)
            except Exception as exc:
                # pyserial raises SerialException, OSError or even TypeError when unplugged.
                if not stop.is_set():
                    with contextlib.suppress(RuntimeError):
                        self._emitter.lost.emit(str(exc) or type(exc).__name__, stop)
                return
            if data and not stop.is_set():
                with contextlib.suppress(RuntimeError):  # the service may be gone already
                    self._emitter.data.emit(bytes(data))

    def _on_lost(self, reason: str, stop: object) -> None:
        # Queued across threads: by now the port may be closed, or reopened by a new reader.
        if self._port is None or stop is not self._stop:
            return
        self.close()
        self.connection_lost.emit(reason)
