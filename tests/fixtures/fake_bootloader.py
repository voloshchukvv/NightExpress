"""A simulated STM32 system bootloader (AN3155) for protocol tests.

It implements the device side of the protocol over an in-memory "serial port": every write is
processed immediately and replies are queued for read(). Faults can be injected per instance.
"""

from __future__ import annotations

ACK, NACK = 0x79, 0x1F
FLASH_BASE = 0x08000000


def xor(data: bytes) -> int:
    value = 0
    for b in data:
        value ^= b
    return value


class FakeBootloader:
    def __init__(
        self,
        pid: int = 0x412,
        flash_size: int = 32 * 1024,
        extended_erase: bool = False,
        silent: bool = False,
        protected: bool = False,
        nack_erase: bool = False,
        corrupt_read: bool = False,
        on_write=None,
    ) -> None:
        self.timeout: float | None = 1.0
        self.pid = pid
        self.flash = bytearray(b"\x00" * flash_size)  # not erased at start
        self.extended_erase = extended_erase
        self.silent = silent
        self.protected = protected
        self.nack_erase = nack_erase
        self.corrupt_read = corrupt_read
        self.on_write = on_write  # called with the address of every WRITE MEMORY
        self.synced = False
        self.erased = False
        self.started_at: int | None = None
        self.closed = False
        self.commands: list[int] = []
        self._in = bytearray()
        self._out = bytearray()
        self._state: tuple = ("cmd",)

    # --- transport API ---------------------------------------------------------------------

    def write(self, data: bytes) -> int:
        if self.silent:
            return len(data)
        self._in += data
        self._process()
        return len(data)

    def read(self, size: int = 1) -> bytes:
        chunk, self._out = bytes(self._out[:size]), self._out[size:]
        return chunk

    def reset_input_buffer(self) -> None:
        self._out.clear()

    def close(self) -> None:
        self.closed = True

    # --- device side -----------------------------------------------------------------------

    def _reply(self, *values: int) -> None:
        self._out += bytes(values)

    def _take(self, n: int) -> bytes | None:
        if len(self._in) < n:
            return None
        chunk, self._in = bytes(self._in[:n]), self._in[n:]
        return chunk

    def _process(self) -> None:
        while True:
            if not self.synced:
                byte = self._take(1)
                if byte is None:
                    return
                if byte[0] == 0x7F:
                    self.synced = True
                    self._reply(ACK)
                continue
            state = self._state[0]
            if state == "cmd":
                frame = self._take(2)
                if frame is None:
                    return
                code, check = frame
                if code ^ 0xFF != check:
                    self._reply(NACK)
                    continue
                self.commands.append(code)
                self._command(code)
            elif state == "addr":
                frame = self._take(5)
                if frame is None:
                    return
                address = int.from_bytes(frame[:4], "big")
                if xor(frame[:4]) != frame[4]:
                    self._reply(NACK)
                    self._state = ("cmd",)
                    continue
                self._reply(ACK)
                after = self._state[1]
                if after == "go":
                    self.started_at = address
                    self._state = ("cmd",)
                else:
                    self._state = (after, address)
            elif state == "read_len":
                frame = self._take(2)
                if frame is None:
                    return
                n, check = frame
                address = self._state[1]
                self._reply(ACK)
                offset = address - FLASH_BASE
                data = bytearray(self.flash[offset : offset + n + 1])
                if self.corrupt_read and data:
                    data[0] ^= 0xFF
                self._out += data
                self._state = ("cmd",)
            elif state == "write_data":
                if not self._in:
                    return
                n = self._in[0] + 1
                frame = self._take(n + 2)
                if frame is None:
                    return
                address = self._state[1]
                payload, check = frame[1:-1], frame[-1]
                if xor(frame[:-1]) != check:
                    self._reply(NACK)
                else:
                    if self.on_write:
                        self.on_write(address)
                    offset = address - FLASH_BASE
                    self.flash[offset : offset + len(payload)] = payload
                    self._reply(ACK)
                self._state = ("cmd",)
            elif state == "erase":
                frame = self._take(2)
                if frame is None:
                    return
                self._finish_erase(frame == b"\xff\x00")
            elif state == "ext_erase":
                frame = self._take(3)
                if frame is None:
                    return
                self._finish_erase(frame == b"\xff\xff\x00")

    def _finish_erase(self, global_erase: bool) -> None:
        if global_erase and not self.nack_erase:
            self.flash[:] = b"\xff" * len(self.flash)
            self.erased = True
            self._reply(ACK)
        else:
            self._reply(NACK)
        self._state = ("cmd",)

    def _command(self, code: int) -> None:
        supported = [0x00, 0x01, 0x02, 0x11, 0x21, 0x31, 0x44 if self.extended_erase else 0x43]
        if code == 0x00:
            self._reply(ACK, len(supported), 0x22, *supported, ACK)
        elif code == 0x02:
            self._reply(ACK, 0x01, self.pid >> 8, self.pid & 0xFF, ACK)
        elif code in (0x11, 0x31, 0x21):
            if self.protected:
                self._reply(NACK)
                return
            self._reply(ACK)
            self._state = ("addr", {0x11: "read_len", 0x31: "write_data", 0x21: "go"}[code])
        elif code == 0x43 and not self.extended_erase:
            self._reply(ACK)
            self._state = ("erase",)
        elif code == 0x44 and self.extended_erase:
            self._reply(ACK)
            self._state = ("ext_erase",)
        else:
            self._reply(NACK)
