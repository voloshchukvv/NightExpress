"""Turn received bytes into display text for the serial monitor (text or hex, timestamps)."""

from __future__ import annotations

import codecs
from datetime import datetime

from nightexpress.models.state import DisplayMode

HEX_BYTES_PER_LINE = 16


class MonitorRenderer:
    """Stateful: a UTF-8 character or a line may be split across reads."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._at_line_start = True
        self._hex_column = 0
        self._pending_cr = False

    def render(
        self, data: bytes, mode: DisplayMode, timestamps: bool, now: datetime | None = None
    ) -> str:
        stamp = f"[{(now or datetime.now()).strftime('%H:%M:%S.%f')[:-3]}] " if timestamps else ""
        if mode is DisplayMode.HEX:
            return self._hex(data, stamp)
        return self._text(self._decoder.decode(data), stamp)

    def _text(self, text: str, stamp: str) -> str:
        out: list[str] = []
        for char in text:
            if self._pending_cr:
                self._pending_cr = False
                if char == "\n":
                    continue  # the "\n" of a "\r\n" pair already produced the line break
            if char == "\r":
                char, self._pending_cr = "\n", True
            if self._at_line_start and char != "\n":
                out.append(stamp)
                self._at_line_start = False
            out.append(char)
            if char == "\n":
                self._at_line_start = True
        return "".join(out)

    def _hex(self, data: bytes, stamp: str) -> str:
        out: list[str] = []
        for byte in data:
            if self._at_line_start:
                out.append(stamp)
                self._at_line_start = False
            out.append(f"{byte:02X} ")
            self._hex_column += 1
            if byte == 0x0A or self._hex_column == HEX_BYTES_PER_LINE:
                out.append("\n")
                self._at_line_start, self._hex_column = True, 0
        return "".join(out)
