"""AN3155 flasher against the simulated bootloader in tests/fixtures/fake_bootloader.py."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from nightexpress.models.entities import UploadProgress, UploadStage
from nightexpress.services.elf_tools import BinaryImage, elf_to_bin
from nightexpress.services.serial_bootloader import (
    ACK,
    Bootloader,
    BootloaderError,
    BootloaderErrorKind,
    flash_image,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from fake_bootloader import FLASH_BASE, FakeBootloader

BLINKY = elf_to_bin(Path(__file__).resolve().parents[1] / "fixtures" / "elf" / "blinky.elf")
FLASH = 32 * 1024


def flash(device: FakeBootloader, image: BinaryImage = BLINKY, **kwargs) -> list[UploadProgress]:
    events: list[UploadProgress] = []
    flash_image(device, image, 0x412, FLASH, progress=events.append, **kwargs)
    return events


@pytest.mark.parametrize("extended", [False, True])
def test_flash_blinky(extended: bool) -> None:
    device = FakeBootloader(extended_erase=extended)
    events = flash(device)
    size = len(BLINKY.data)
    assert device.erased
    assert bytes(device.flash[:size]) == BLINKY.data
    assert device.started_at == FLASH_BASE
    assert (0x44 if extended else 0x43) in device.commands
    stages = [e.stage for e in events]
    assert stages[0] is UploadStage.CONNECTING and stages[-1] is UploadStage.RESETTING
    programming = [e.percent for e in events if e.stage is UploadStage.PROGRAMMING]
    assert programming[-1] == 100 and programming == sorted(programming)
    assert [e.percent for e in events if e.stage is UploadStage.VERIFYING][-1] == 100


def test_odd_length_image_is_padded_with_erased_bytes() -> None:
    device = FakeBootloader()
    flash(device, BinaryImage(FLASH_BASE, b"\x01\x02\x03\x04\x05"))
    assert bytes(device.flash[:8]) == b"\x01\x02\x03\x04\x05\xff\xff\xff"


def test_without_go_the_program_is_not_started() -> None:
    device = FakeBootloader()
    flash(device, start=False)
    assert device.started_at is None


def test_log_reports_version_and_pid() -> None:
    lines: list[str] = []
    flash_image(FakeBootloader(), BLINKY, 0x412, FLASH, log=lines.append)
    assert lines[0].startswith("Бутлоадер v2.2")
    assert lines[1] == "PID чипа: 0x412"


@pytest.mark.parametrize(
    ("device", "kind"),
    [
        (FakeBootloader(silent=True), BootloaderErrorKind.NO_RESPONSE),
        (FakeBootloader(pid=0x410), BootloaderErrorKind.WRONG_CHIP),
        (FakeBootloader(protected=True), BootloaderErrorKind.PROTECTED),
        (FakeBootloader(nack_erase=True), BootloaderErrorKind.NACK),
        (FakeBootloader(corrupt_read=True), BootloaderErrorKind.VERIFY_FAILED),
    ],
)
def test_failures(device: FakeBootloader, kind: BootloaderErrorKind) -> None:
    with pytest.raises(BootloaderError) as info:
        flash(device)
    assert info.value.kind is kind
    assert info.value.hint


def test_wrong_chip_is_detected_before_erasing() -> None:
    device = FakeBootloader(pid=0x410)
    with pytest.raises(BootloaderError):
        flash(device)
    assert not device.erased


def test_image_larger_than_flash() -> None:
    with pytest.raises(BootloaderError) as info:
        flash(FakeBootloader(), BinaryImage(FLASH_BASE, b"\x00" * (FLASH + 4)))
    assert info.value.kind is BootloaderErrorKind.TOO_LARGE


def test_cancel_in_the_middle_of_writing() -> None:
    cancel = threading.Event()
    device = FakeBootloader(on_write=lambda address: address >= FLASH_BASE + 256 and cancel.set())
    with pytest.raises(BootloaderError) as info:
        flash(device, cancel=cancel)
    assert info.value.kind is BootloaderErrorKind.CANCELLED
    assert device.started_at is None


def test_sync_accepts_nack_when_already_synchronised() -> None:
    device = FakeBootloader()
    device.synced = True  # a previous session already sent 0x7F
    Bootloader(device).sync()  # the fake answers NACK to a stray 0x7F command byte pair


def test_unexpected_reply_byte() -> None:
    class Garbage(FakeBootloader):
        def read(self, size: int = 1) -> bytes:
            data = super().read(size)
            return b"\x42" if data == bytes((ACK,)) and self.synced and self.commands else data

    with pytest.raises(BootloaderError) as info:
        flash(Garbage())
    assert info.value.kind is BootloaderErrorKind.NO_RESPONSE


def test_block_size_is_validated() -> None:
    boot = Bootloader(FakeBootloader())
    with pytest.raises(ValueError):
        boot.write(FLASH_BASE, b"")
    with pytest.raises(ValueError):
        boot.read(FLASH_BASE, 257)


def test_open_serial_reports_unavailable_port() -> None:
    from nightexpress.services.serial_bootloader import open_serial

    with pytest.raises(BootloaderError) as info:
        open_serial("/dev/nightexpress-no-such-port")  # never touches a real adapter
    assert info.value.kind is BootloaderErrorKind.PORT_UNAVAILABLE
