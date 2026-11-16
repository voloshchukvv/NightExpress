"""Real hardware: flash reference/blinky_f103c6 through the UART bootloader.

The port is never guessed (other adapters may be wired to other devices). Put the board into
bootloader mode (BOOT0 = 1, Reset) and run:

    STMR_UART_PORT=/dev/cu.usbserial-120 .venv/bin/python -m pytest -m hardware tests/hardware
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.elf_tools import elf_to_bin
from nightexpress.services.serial_bootloader import Bootloader, flash_image, open_serial

pytestmark = pytest.mark.hardware

ELF = (
    Path(__file__).resolve().parents[2]
    / "reference/blinky_f103c6/target/thumbv7m-none-eabi/release/blinky_f103c6"
)


def test_flash_blinky_over_uart() -> None:
    port = os.environ.get("STMR_UART_PORT")
    if not port:
        pytest.skip("set STMR_UART_PORT to the adapter wired to the STM32")
    if not ELF.is_file():
        pytest.skip("build reference/blinky_f103c6 first (scripts/check.sh)")
    chip = BoardCatalog.load().get_chip("STM32F103C6")
    image = elf_to_bin(ELF)
    transport = open_serial(port)
    try:
        flash_image(transport, image, chip.dev_id, chip.flash_bytes, start=False)
        boot = Bootloader(transport)
        assert boot.read(image.address, 16) == image.data[:16]
        boot.go(image.address)
    finally:
        transport.close()
