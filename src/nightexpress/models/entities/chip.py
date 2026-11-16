"""Microcontroller model definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nightexpress.models.entities.gpio import require_pin

# STM32 part number: "STM32" + 4-char line (F103, G0B1, WB55) + pin-count letter + flash code,
# then optional package / temperature / revision suffixes (e.g. T6A in STM32F103C6T6A).
_MARKING_RE = re.compile(r"^(STM32[A-Z0-9]{4}[A-Z][A-Z0-9])[A-Z0-9]*$")


def chip_model_from_marking(marking: str) -> str | None:
    """Reduce a full part marking to the model id used in the catalog.

    >>> chip_model_from_marking("stm32f103c6t6a")
    'STM32F103C6'
    """
    cleaned = re.sub(r"[\s\-_]", "", marking).upper()
    match = _MARKING_RE.match(cleaned)
    return match.group(1) if match else None


@dataclass(frozen=True)
class BootloaderInfo:
    """System-memory (ROM) UART bootloader of the chip."""

    family: str  # stm32loader-style family code, e.g. "F1"
    usart: str  # e.g. "USART1"
    tx_pin: str
    rx_pin: str

    def __post_init__(self) -> None:
        require_pin(self.tx_pin, "bootloader.tx")
        require_pin(self.rx_pin, "bootloader.rx")


@dataclass(frozen=True)
class ChipDefinition:
    id: str  # model id, e.g. "STM32F103C6"
    family: str  # e.g. "STM32F1"
    core: str  # e.g. "cortex-m3"
    target_triple: str  # e.g. "thumbv7m-none-eabi"
    flash_kb: int
    ram_kb: int
    embassy_feature: str  # e.g. "stm32f103c6"
    probe_rs_name: str  # name in `probe-rs chip list`, may differ from the marking
    dev_id: int  # expected DEV_ID from DBGMCU_IDCODE, e.g. 0x412
    idcode_address: int  # DBGMCU_IDCODE register address
    flash_size_address: int  # flash size register (KB, 16-bit)
    default_sysclk_hz: int  # core clock right after embassy_stm32::init(Default::default())
    bootloader: BootloaderInfo | None

    def __post_init__(self) -> None:
        if chip_model_from_marking(self.id) != self.id:
            raise ValueError(f"chip id {self.id!r} is not a normalized model id")
        if self.flash_kb <= 0 or self.ram_kb <= 0:
            raise ValueError(f"{self.id}: flash_kb and ram_kb must be positive")
        if not self.target_triple.startswith("thumbv"):
            raise ValueError(f"{self.id}: unexpected target triple {self.target_triple!r}")
        if self.default_sysclk_hz <= 0:
            raise ValueError(f"{self.id}: default_sysclk_hz must be positive")
        if not 0 < self.dev_id <= 0xFFF:
            raise ValueError(f"{self.id}: dev_id must be a 12-bit value")

    @property
    def flash_bytes(self) -> int:
        return self.flash_kb * 1024

    @property
    def ram_bytes(self) -> int:
        return self.ram_kb * 1024


@dataclass(frozen=True)
class I2cPinChoice:
    """One way to wire an I2C bus: the peripheral and its SCL/SDA pins."""

    peripheral: str  # e.g. "I2C1"
    scl_pin: str
    sda_pin: str


@dataclass(frozen=True)
class I2cBusPins:
    """The pins one I2C peripheral of a chip can use (from embassy's chip data)."""

    peripheral: str
    scl: tuple[str, ...]
    sda: tuple[str, ...]
    # STM32F1: the pins move in pairs (AFIO remap), only these (SCL, SDA) work. Empty: any
    # SCL with any SDA.
    pairs: tuple[tuple[str, str], ...] = ()

    def choices(self) -> list[I2cPinChoice]:
        pairs = self.pairs or tuple((scl, sda) for scl in self.scl for sda in self.sda)
        return [I2cPinChoice(self.peripheral, scl, sda) for scl, sda in pairs]


@dataclass(frozen=True)
class ChipPins:
    """What the pins of a chip can do: every GPIO, the analog inputs and the I2C buses."""

    gpio: tuple[str, ...]
    adc: dict[str, str]  # pin -> analog input, e.g. "PA0" -> "ADC1_IN0"
    i2c: tuple[I2cBusPins, ...]

    def i2c_choices(self) -> list[I2cPinChoice]:
        return [choice for bus in self.i2c for choice in bus.choices()]
