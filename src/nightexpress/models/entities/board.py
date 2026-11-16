"""Development board definitions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from nightexpress.i18n import FALLBACK_LANGUAGE, localized
from nightexpress.models.entities.gpio import require_pin

_BOARD_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class UploadMethod(Enum):
    PROBE_RS = "probe-rs"
    SERIAL_BOOTLOADER = "serial-bootloader"
    USB_DFU = "usb-dfu"  # the ROM bootloader over the chip's own USB (STM32F4: dfu-util)


class SupportStatus(Enum):
    VERIFIED = "verified"  # checked on real hardware
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True)
class LedInfo:
    pin: str
    active_low: bool

    def __post_init__(self) -> None:
        require_pin(self.pin, "led")


@dataclass(frozen=True)
class ButtonInfo:
    pin: str
    active_low: bool  # pressed = LOW: the button goes to GND, the pin is pulled up
    on_board: bool  # False: the board has no user button, the user wires one to ``pin``

    def __post_init__(self) -> None:
        require_pin(self.pin, "button")


@dataclass(frozen=True)
class UartInfo:
    peripheral: str  # e.g. "USART1"
    tx_pin: str
    rx_pin: str

    def __post_init__(self) -> None:
        require_pin(self.tx_pin, "uart.tx")
        require_pin(self.rx_pin, "uart.rx")


@dataclass(frozen=True)
class I2cInfo:
    """The I2C bus that modules (sensors, displays) are wired to on this board."""

    peripheral: str  # e.g. "I2C1"
    scl_pin: str
    sda_pin: str

    def __post_init__(self) -> None:
        require_pin(self.scl_pin, "i2c.scl")
        require_pin(self.sda_pin, "i2c.sda")


@dataclass(frozen=True)
class UploadMethodSupport:
    method: UploadMethod
    status: SupportStatus


@dataclass(frozen=True)
class BoardDefinition:
    id: str  # kebab-case, e.g. "stm32f103c6-minimal"
    display_names: Mapping[str, str]  # {language: name}, see localized_texts
    chip_id: str
    aliases: tuple[str, ...]  # part markings (STM32F103C6T6A), shown and searched
    led: LedInfo | None
    button: ButtonInfo | None
    uart: UartInfo | None
    upload_methods: tuple[UploadMethodSupport, ...]
    default_upload_method: UploadMethod
    status: SupportStatus
    notes: str = ""
    i2c: I2cInfo | None = None  # the bus for modules; None: no I2C described
    # What people call the board ("Blue Pill", "Nucleo-F401RE"), all of them searched. The
    # first one is shown in brackets after the name: "STM32F103C6 (Blue Pill)".
    nicknames: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        """The name in the current interface language, with the usual nickname."""
        return self._with_nickname(localized(self.display_names))

    @property
    def display_name_en(self) -> str | None:
        """The English name for generated projects, whose files are always in English."""
        name = self.display_names.get(FALLBACK_LANGUAGE)
        return self._with_nickname(name) if name else None

    def _with_nickname(self, name: str) -> str:
        return f"{name} ({self.nicknames[0]})" if self.nicknames else name

    def __post_init__(self) -> None:
        if not _BOARD_ID_RE.match(self.id):
            raise ValueError(f"board id {self.id!r} must be kebab-case")
        if not self.display_name.strip():
            raise ValueError(f"{self.id}: display_name is empty")
        if not self.upload_methods:
            raise ValueError(f"{self.id}: at least one upload method is required")
        methods = [m.method for m in self.upload_methods]
        if len(set(methods)) != len(methods):
            raise ValueError(f"{self.id}: duplicate upload methods")
        if self.default_upload_method not in methods:
            raise ValueError(f"{self.id}: default upload method is not in upload_methods")

    def supports(self, method: UploadMethod) -> bool:
        return any(m.method is method for m in self.upload_methods)

    def method_status(self, method: UploadMethod) -> SupportStatus | None:
        for m in self.upload_methods:
            if m.method is method:
                return m.status
        return None
