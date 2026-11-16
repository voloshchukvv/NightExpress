"""State of the serial monitor."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Signal

from nightexpress.models.entities import DEFAULT_BAUD_RATE, SerialPortInfo
from nightexpress.models.state.base import ObservableModel

COMMON_BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
MAX_BUFFER_BYTES = 1024 * 1024


class DisplayMode(Enum):
    TEXT = "text"
    HEX = "hex"


class LineEnding(Enum):
    NONE = "none"
    LF = "lf"
    CR = "cr"
    CRLF = "crlf"

    @property
    def bytes(self) -> bytes:
        return {"none": b"", "lf": b"\n", "cr": b"\r", "crlf": b"\r\n"}[self.value]


class SerialMonitorModel(ObservableModel):
    port_changed = Signal(object)  # SerialPortInfo | None
    baud_rate_changed = Signal(int)
    open_changed = Signal(bool)
    display_mode_changed = Signal(object)  # DisplayMode
    line_ending_changed = Signal(object)  # LineEnding
    autoscroll_changed = Signal(bool)
    data_received = Signal(bytes)
    cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._port: SerialPortInfo | None = None
        self._baud_rate = DEFAULT_BAUD_RATE
        self._is_open = False
        self._display_mode = DisplayMode.TEXT
        self._line_ending = LineEnding.LF
        self._autoscroll = True
        self._buffer = bytearray()

    @property
    def port(self) -> SerialPortInfo | None:
        return self._port

    def set_port(self, port: SerialPortInfo | None) -> None:
        self._update("_port", port, self.port_changed)

    @property
    def baud_rate(self) -> int:
        return self._baud_rate

    def set_baud_rate(self, baud_rate: int) -> None:
        if baud_rate <= 0:
            raise ValueError(f"baud rate must be positive, got {baud_rate}")
        self._update("_baud_rate", baud_rate, self.baud_rate_changed)

    @property
    def is_open(self) -> bool:
        return self._is_open

    def set_open(self, is_open: bool) -> None:
        self._update("_is_open", is_open, self.open_changed)

    @property
    def display_mode(self) -> DisplayMode:
        return self._display_mode

    def set_display_mode(self, mode: DisplayMode) -> None:
        self._update("_display_mode", mode, self.display_mode_changed)

    @property
    def line_ending(self) -> LineEnding:
        return self._line_ending

    def set_line_ending(self, ending: LineEnding) -> None:
        self._update("_line_ending", ending, self.line_ending_changed)

    @property
    def autoscroll(self) -> bool:
        return self._autoscroll

    def set_autoscroll(self, enabled: bool) -> None:
        self._update("_autoscroll", enabled, self.autoscroll_changed)

    # --- received data ---------------------------------------------------------------------

    @property
    def buffer(self) -> bytes:
        return bytes(self._buffer)

    def append_data(self, data: bytes) -> None:
        if not data:
            return
        self._buffer += data
        overflow = len(self._buffer) - MAX_BUFFER_BYTES
        if overflow > 0:
            del self._buffer[:overflow]
        self.data_received.emit(data)

    def clear(self) -> None:
        if self._buffer:
            self._buffer.clear()
            self.cleared.emit()
