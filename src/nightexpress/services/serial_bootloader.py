"""STM32 system-memory bootloader over UART (ST application note AN3155).

Implemented on top of a minimal transport (``write`` / ``read`` / ``reset_input_buffer``) so the
protocol is tested against a simulated chip; :func:`open_serial` provides the real pyserial port
(8 data bits, even parity, 1 stop bit). ``stm32loader`` is GPL and therefore not used.

The flasher is synchronous: run it in a worker thread.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from nightexpress.i18n import N_, _
from nightexpress.models.entities import UploadProgress, UploadStage
from nightexpress.services.elf_tools import BinaryImage

ACK = 0x79
NACK = 0x1F
SYNC = 0x7F

CMD_GET = 0x00
CMD_GET_ID = 0x02
CMD_READ = 0x11
CMD_GO = 0x21
CMD_WRITE = 0x31
CMD_ERASE = 0x43
CMD_EXTENDED_ERASE = 0x44

BAUD_RATE = 115200
BLOCK_SIZE = 256
SYNC_ATTEMPTS = 3
DEFAULT_TIMEOUT_S = 1.0
ERASE_TIMEOUT_S = 30.0


class Transport(Protocol):
    timeout: float | None

    def write(self, data: bytes) -> int | None: ...

    def read(self, size: int = 1) -> bytes: ...

    def reset_input_buffer(self) -> None: ...

    def close(self) -> None: ...


class BootloaderErrorKind(Enum):
    PORT_UNAVAILABLE = "port_unavailable"
    NO_RESPONSE = "no_response"
    NACK = "nack"
    PROTECTED = "protected"
    WRONG_CHIP = "wrong_chip"
    TOO_LARGE = "too_large"
    VERIFY_FAILED = "verify_failed"
    CANCELLED = "cancelled"


# User-facing explanations.
HINTS = {
    BootloaderErrorKind.PORT_UNAVAILABLE: (
        N_(
            "Не вдалося відкрити порт: його зайняла інша програма (монітор порту, термінал) "
            "або адаптер відключено."
        )
    ),
    BootloaderErrorKind.NO_RESPONSE: (
        N_(
            "Бутлоадер не відповідає. Перевірте: BOOT0 = 1 і натиснуто Reset; TX адаптера → RX "
            "бутлоадера, RX → TX (на STM32F1 це PA10 і PA9); спільний GND; рівень 3.3 V."
        )
    ),
    BootloaderErrorKind.NACK: N_("Бутлоадер відхилив команду. Натисніть Reset і спробуйте ще раз."),
    BootloaderErrorKind.PROTECTED: (
        N_(
            "Flash захищена від читання/запису (Read-out protection). Зніміть захист через ST-Link "
            "або STM32CubeProgrammer — це зітре чип."
        )
    ),
    BootloaderErrorKind.WRONG_CHIP: N_("Відповів інший чип, ніж той, для якого зібрано проєкт."),
    BootloaderErrorKind.TOO_LARGE: N_("Прошивка більша за flash цього чипа."),
    BootloaderErrorKind.VERIFY_FAILED: N_("Записане не збігається з прошивкою. Спробуйте ще раз."),
    BootloaderErrorKind.CANCELLED: N_("Прошивання зупинено."),
}


class BootloaderError(Exception):
    def __init__(self, kind: BootloaderErrorKind, details: str = "") -> None:
        super().__init__(details or _(HINTS[kind]))
        self.kind = kind
        self.details = details

    @property
    def hint(self) -> str:
        return _(HINTS[self.kind])


@dataclass(frozen=True)
class BootloaderVersion:
    version: int
    commands: tuple[int, ...]

    @property
    def version_text(self) -> str:
        return f"{self.version >> 4}.{self.version & 0x0F}"


def _checksum(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


class Bootloader:
    """Low-level AN3155 commands."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    # --- framing ---------------------------------------------------------------------------

    def _read_exact(self, size: int, what: str) -> bytes:
        data = self._t.read(size)
        if len(data) != size:
            raise BootloaderError(
                BootloaderErrorKind.NO_RESPONSE, _("немає відповіді: {what}").format(what=what)
            )
        return data

    def _wait_ack(self, what: str, timeout: float | None = None) -> None:
        previous = self._t.timeout
        if timeout is not None:
            self._t.timeout = timeout
        try:
            reply = self._read_exact(1, what)[0]
        finally:
            self._t.timeout = previous
        if reply == NACK:
            raise BootloaderError(BootloaderErrorKind.NACK, f"NACK: {what}")
        if reply != ACK:
            raise BootloaderError(
                BootloaderErrorKind.NO_RESPONSE,
                _("неочікувана відповідь 0x{reply:02X}: {what}").format(reply=reply, what=what),
            )

    def _command(self, code: int, name: str) -> None:
        self._t.write(bytes((code, code ^ 0xFF)))
        try:
            self._wait_ack(name)
        except BootloaderError as exc:
            if exc.kind is BootloaderErrorKind.NACK and code in (CMD_READ, CMD_WRITE, CMD_GO):
                raise BootloaderError(BootloaderErrorKind.PROTECTED, f"NACK: {name}") from exc
            raise

    def _send_address(self, address: int, name: str) -> None:
        raw = address.to_bytes(4, "big")
        self._t.write(raw + bytes((_checksum(raw),)))
        self._wait_ack(_("{name}: адреса 0x{address:08X}").format(name=name, address=address))

    # --- commands --------------------------------------------------------------------------

    def sync(self) -> None:
        """Send 0x7F so the bootloader measures the baud rate."""
        self._t.reset_input_buffer()
        for _attempt in range(SYNC_ATTEMPTS):
            self._t.write(bytes((SYNC,)))
            reply = self._t.read(1)
            if reply and reply[0] in (ACK, NACK):  # NACK: it was already synchronised
                return
        raise BootloaderError(BootloaderErrorKind.NO_RESPONSE, _("немає відповіді на 0x7F"))

    def get(self) -> BootloaderVersion:
        self._command(CMD_GET, "GET")
        count = self._read_exact(1, "GET")[0] + 1
        payload = self._read_exact(count, "GET")
        self._wait_ack("GET")
        return BootloaderVersion(payload[0], tuple(payload[1:]))

    def get_id(self) -> int:
        self._command(CMD_GET_ID, "GET ID")
        count = self._read_exact(1, "GET ID")[0] + 1
        pid = int.from_bytes(self._read_exact(count, "GET ID"), "big")
        self._wait_ack("GET ID")
        return pid

    def erase_all(self, extended: bool) -> None:
        if extended:
            self._command(CMD_EXTENDED_ERASE, "EXTENDED ERASE")
            self._t.write(bytes((0xFF, 0xFF, 0x00)))
        else:
            self._command(CMD_ERASE, "ERASE")
            self._t.write(bytes((0xFF, 0x00)))
        self._wait_ack(_("стирання flash"), ERASE_TIMEOUT_S)

    def write(self, address: int, data: bytes) -> None:
        if not 0 < len(data) <= BLOCK_SIZE:
            raise ValueError("block must be 1..256 bytes")
        self._command(CMD_WRITE, "WRITE MEMORY")
        self._send_address(address, "WRITE MEMORY")
        frame = bytes((len(data) - 1,)) + data
        self._t.write(frame + bytes((_checksum(frame),)))
        self._wait_ack(_("запис 0x{address:08X}").format(address=address))

    def read(self, address: int, size: int) -> bytes:
        if not 0 < size <= BLOCK_SIZE:
            raise ValueError("block must be 1..256 bytes")
        self._command(CMD_READ, "READ MEMORY")
        self._send_address(address, "READ MEMORY")
        n = size - 1
        self._t.write(bytes((n, n ^ 0xFF)))
        self._wait_ack(_("READ MEMORY: довжина"))
        return self._read_exact(size, _("читання 0x{address:08X}").format(address=address))

    def go(self, address: int) -> None:
        self._command(CMD_GO, "GO")
        self._send_address(address, "GO")


ProgressCallback = Callable[[UploadProgress], None]
LogCallback = Callable[[str], None]


def _pad(data: bytes) -> bytes:
    """Writes must be word-aligned on STM32F1; pad with the erased value 0xFF."""
    return data + b"\xff" * (-len(data) % 4)


def flash_image(
    transport: Transport,
    image: BinaryImage,
    expected_pid: int,
    flash_bytes: int,
    progress: ProgressCallback = lambda _p: None,
    log: LogCallback = lambda _s: None,
    cancel: threading.Event | None = None,
    start: bool = True,
) -> None:
    """Erase, write, verify and (optionally) start ``image`` through the bootloader."""
    cancel = cancel or threading.Event()

    def check_cancel() -> None:
        if cancel.is_set():
            raise BootloaderError(BootloaderErrorKind.CANCELLED)

    if len(image.data) > flash_bytes:
        raise BootloaderError(
            BootloaderErrorKind.TOO_LARGE,
            _("{count} байт > {flash_bytes} байт flash").format(
                count=len(image.data), flash_bytes=flash_bytes
            ),
        )
    data = _pad(image.data)
    boot = Bootloader(transport)

    progress(UploadProgress(UploadStage.CONNECTING))
    boot.sync()
    info = boot.get()
    log(
        _("Бутлоадер v{version}, команди: {commands}").format(
            version=info.version_text, commands=" ".join(f"{c:02X}" for c in info.commands)
        )
    )
    pid = boot.get_id()
    log(_("PID чипа: 0x{pid:03X}").format(pid=pid))
    if pid != expected_pid:
        raise BootloaderError(
            BootloaderErrorKind.WRONG_CHIP,
            _("очікувався PID 0x{expected_pid:03X}, отримано 0x{pid:03X}").format(
                expected_pid=expected_pid, pid=pid
            ),
        )
    check_cancel()

    progress(UploadProgress(UploadStage.ERASING))
    log(_("Стираю flash…"))
    boot.erase_all(extended=CMD_EXTENDED_ERASE in info.commands)

    blocks = [(i, data[i : i + BLOCK_SIZE]) for i in range(0, len(data), BLOCK_SIZE)]
    log(
        _("Записую {count} байт ({blocks} блоків по {block_size})…").format(
            count=len(image.data), blocks=len(blocks), block_size=BLOCK_SIZE
        )
    )
    for number, (offset, block) in enumerate(blocks, start=1):
        check_cancel()
        boot.write(image.address + offset, block)
        progress(UploadProgress(UploadStage.PROGRAMMING, number * 100 // len(blocks)))

    log(_("Перевіряю записане…"))
    for number, (offset, block) in enumerate(blocks, start=1):
        check_cancel()
        if boot.read(image.address + offset, len(block)) != block:
            raise BootloaderError(
                BootloaderErrorKind.VERIFY_FAILED,
                _("розбіжність у блоці з адреси 0x{address:08X}").format(
                    address=image.address + offset
                ),
            )
        progress(UploadProgress(UploadStage.VERIFYING, number * 100 // len(blocks)))

    if start:
        progress(UploadProgress(UploadStage.RESETTING))
        log(_("Запускаю програму з 0x{address:08X} (GO)…").format(address=image.address))
        boot.go(image.address)


def open_serial(device: str) -> Transport:
    """Open the user-selected port with the bootloader's frame format (8E1)."""
    import serial  # pyserial

    try:
        return serial.Serial(
            device,
            BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=DEFAULT_TIMEOUT_S,
            write_timeout=DEFAULT_TIMEOUT_S,
        )
    except (OSError, serial.SerialException) as exc:
        raise BootloaderError(BootloaderErrorKind.PORT_UNAVAILABLE, str(exc)) from exc
