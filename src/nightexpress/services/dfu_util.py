"""Flashing through the chip's own USB: the ROM bootloader in DFU mode, driven by ``dfu-util``.

STM32F4 chips (Black Pill) start the bootloader when BOOT0 is held during Reset; it shows up on
USB as "STM32 BOOTLOADER" (0483:df11) and needs no adapter. A flash takes two runs of dfu-util:

1. ``-D``: erase the pages the image covers and write it;
2. ``-U ...:leave``: read the same range back (compared here: dfu-util itself does not verify)
   and leave the bootloader, so the new program starts at once.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from nightexpress.i18n import _
from nightexpress.models.entities import DfuDeviceInfo, UploadProgress, UploadStage
from nightexpress.services.elf_tools import BinaryImage
from nightexpress.services.process_runner import (
    ProcessOutcome,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)

STM32_BOOTLOADER = (0x0483, 0xDF11)
LIST_TIMEOUT_MS = 10_000
FLASH_TIMEOUT_MS = 180_000  # 512 KB with the erase of 128 KB sectors takes about a minute

# Found DFU: [0483:df11] ver=2200, devnum=5, cfg=1, intf=0, path="1-1", alt=0,
#   name="@Internal Flash  /0x08000000/04*016Kg,01*064Kg,03*128Kg", serial="356D35693131"
_FOUND_RE = re.compile(
    r"Found DFU: \[(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})\](?P<rest>.*)"
)
_FIELD_RE = re.compile(r'(?P<key>\w+)=(?:"(?P<quoted>[^"]*)"|(?P<plain>[^,\s]*))')
# Download	[=========                ]  36%        45056 bytes
_PROGRESS_RE = re.compile(r"^\s*(?P<what>Erase|Download|Upload)\s*\[[= ]*\]\s*(?P<percent>\d+)%")
_STAGES = {
    "Erase": UploadStage.ERASING,
    "Download": UploadStage.PROGRAMMING,
    "Upload": UploadStage.VERIFYING,
}


def parse_devices(lines: Sequence[str]) -> list[DfuDeviceInfo]:
    """The STM32 bootloaders in ``dfu-util -l``: one entry per chip (its flash interface)."""
    found: list[DfuDeviceInfo] = []
    for line in lines:
        match = _FOUND_RE.search(line)
        if match is None:
            continue
        vid, pid = int(match["vid"], 16), int(match["pid"], 16)
        fields = {
            m["key"]: m["quoted"] if m["quoted"] is not None else m["plain"]
            for m in _FIELD_RE.finditer(match["rest"])
        }
        if (vid, pid) != STM32_BOOTLOADER or fields.get("alt") != "0":
            continue  # other DFU devices; alt 1… are the option bytes and OTP of the same chip
        serial = fields.get("serial") or None
        if serial in ("UNKNOWN", "0"):
            serial = None
        device = DfuDeviceInfo(vid, pid, serial, fields.get("path", ""))
        if device.stable_id not in {d.stable_id for d in found}:
            found.append(device)
    return found


def parse_progress(line: str) -> UploadProgress | None:
    match = _PROGRESS_RE.match(line)
    if match is None:
        return None
    return UploadProgress(_STAGES[match["what"]], min(int(match["percent"]), 100))


def download_args(device: DfuDeviceInfo, image: Path, address: int) -> list[str]:
    return [*device.selector, "-a", "0", "-s", f"0x{address:08X}", "-D", str(image)]


def readback_args(device: DfuDeviceInfo, out: Path, address: int, length: int) -> list[str]:
    # "leave" after the upload: the bootloader jumps to the program that was just written.
    return [*device.selector, "-a", "0", "-s", f"0x{address:08X}:{length}:leave", "-U", str(out)]


class DfuErrorKind(Enum):
    NOT_INSTALLED = "not_installed"
    NO_DEVICE = "no_device"
    ACCESS_DENIED = "access_denied"
    VERIFY_FAILED = "verify_failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DfuError:
    kind: DfuErrorKind
    hint: str
    details: str = ""


def classify(result: ProcessResult) -> DfuError:
    text = "\n".join((*result.stdout_tail, *result.stderr_tail))
    details = next(
        (line.strip() for line in reversed(result.stderr_tail) if line.strip()), result.error
    )
    if result.outcome is ProcessOutcome.CANCELLED:
        return DfuError(DfuErrorKind.CANCELLED, _("Прошивання зупинено."))
    if result.outcome is ProcessOutcome.TIMED_OUT:
        return DfuError(DfuErrorKind.TIMEOUT, _("dfu-util не відповідає."), details)
    if result.outcome is ProcessOutcome.FAILED_TO_START:
        return DfuError(DfuErrorKind.NOT_INSTALLED, _("Не вдалося запустити dfu-util."), details)
    if "No DFU capable USB device" in text:
        return DfuError(
            DfuErrorKind.NO_DEVICE,
            _(
                "Плату в режимі DFU не знайдено. Затисніть BOOT0, натисніть і відпустіть Reset "
                "(NRST), потім відпустіть BOOT0."
            ),
            details,
        )
    if "LIBUSB_ERROR_ACCESS" in text or "Cannot open DFU device" in text:
        return DfuError(
            DfuErrorKind.ACCESS_DENIED,
            _(
                "Немає доступу до USB-пристрою. Linux: додайте правило udev для 0483:df11; "
                "Windows: поставте драйвер WinUSB (Zadig)."
            ),
            details,
        )
    return DfuError(DfuErrorKind.UNKNOWN, _("dfu-util завершився з помилкою."), details)


class DfuService(QObject):
    log_line = Signal(str)
    progress = Signal(object)  # UploadProgress
    devices_listed = Signal(object)  # list[DfuDeviceInfo] (empty also when dfu-util is missing)
    finished = Signal(object)  # DfuError | None

    def __init__(self, search_path: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._search_path = search_path
        self._command: tuple[str, ...] = ("dfu-util",)
        self._list_runner = ProcessRunner(self)
        self._list_runner.finished.connect(self._on_listed)
        self._runner = ProcessRunner(self)
        self._runner.stdout_line.connect(self._on_line)
        self._runner.stderr_line.connect(self._on_line)
        self._runner.finished.connect(self._on_step_finished)
        self._step = ""  # "download" | "readback" while flashing
        self._image: BinaryImage | None = None
        self._device: DfuDeviceInfo | None = None
        self._temp: tempfile.TemporaryDirectory[str] | None = None

    def set_command(self, command: Sequence[str]) -> None:
        self._command = tuple(command)

    @property
    def is_busy(self) -> bool:
        return self._runner.is_running

    # --- listing ---------------------------------------------------------------------------

    def list_devices(self) -> bool:
        """Start a listing; False if one is already running (its result is still coming)."""
        if self._list_runner.is_running:
            return False
        self._list_runner.start(self._spec(["-l"], LIST_TIMEOUT_MS))
        return True

    def _on_listed(self, result: ProcessResult) -> None:
        # dfu-util -l exits with 0 also when nothing is found; a failure means no listing.
        self.devices_listed.emit(parse_devices(result.stdout_tail) if result.ok else [])

    # --- flashing --------------------------------------------------------------------------

    def flash(self, device: DfuDeviceInfo, image: BinaryImage) -> None:
        if self._runner.is_running:
            raise RuntimeError("dfu-util is already running")
        self._cleanup()
        self._temp = tempfile.TemporaryDirectory(prefix="nightexpress-dfu-")
        firmware = Path(self._temp.name) / "firmware.bin"
        firmware.write_bytes(image.data)
        self._image, self._device, self._step = image, device, "download"
        self.log_line.emit(
            _("Записую {count} байт з адреси 0x{address:08X} (dfu-util)…").format(
                count=len(image.data), address=image.address
            )
        )
        self._runner.start(
            self._spec(download_args(device, firmware, image.address), FLASH_TIMEOUT_MS)
        )

    def cancel(self) -> None:
        self._runner.cancel()

    def shutdown(self) -> None:
        self._list_runner.shutdown()
        self._runner.shutdown()
        self._cleanup()

    def _on_line(self, line: str) -> None:
        if not line.strip():
            return
        progress = parse_progress(line)
        if progress is not None:
            self.progress.emit(progress)
        else:
            self.log_line.emit(line)

    def _on_step_finished(self, result: ProcessResult) -> None:
        step, image, device = self._step, self._image, self._device
        assert image is not None and device is not None and self._temp is not None
        readback = Path(self._temp.name) / "readback.bin"
        if step == "download":
            if not result.ok:
                self._done(classify(result))
                return
            self._step = "readback"
            self.log_line.emit(_("Перевіряю записане й запускаю програму…"))
            self.progress.emit(UploadProgress(UploadStage.VERIFYING))
            self._runner.start(
                self._spec(
                    readback_args(device, readback, image.address, len(image.data)),
                    FLASH_TIMEOUT_MS,
                )
            )
            return
        data = readback.read_bytes() if readback.is_file() else b""
        if len(data) < len(image.data):
            # Leaving the bootloader makes the device vanish: dfu-util may then report an
            # error after a complete read. Only a short read is a real failure.
            self._done(classify(result))
        elif data[: len(image.data)] != image.data:
            at = next(i for i, (a, b) in enumerate(zip(data, image.data, strict=False)) if a != b)
            self._done(
                DfuError(
                    DfuErrorKind.VERIFY_FAILED,
                    _("Записане не збігається з прошивкою (адреса 0x{address:08X}).").format(
                        address=image.address + at
                    ),
                )
            )
        else:
            self._done(None)

    def _done(self, error: DfuError | None) -> None:
        self._step = ""
        self._cleanup()
        self.finished.emit(error)

    def _cleanup(self) -> None:
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None

    def _spec(self, args: Sequence[str], timeout_ms: int) -> ProcessSpec:
        env = {"PATH": self._search_path} if self._search_path else None
        return ProcessSpec(
            self._command[0],
            (*self._command[1:], *args),
            env=env,
            timeout_ms=timeout_ms,
            carriage_return_ends_line=True,
        )
