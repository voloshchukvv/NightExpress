"""Upload targets: debug probes (ST-Link), USB-serial adapters and boards in USB DFU mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

DEFAULT_BAUD_RATE = 115200


class AdapterKind(Enum):
    CH340 = "ch340"
    PL2303 = "pl2303"
    FT232 = "ft232"
    CP210X = "cp210x"
    STLINK_VCP = "stlink_vcp"  # the serial port of an ST-Link on a Nucleo/Discovery board
    UNKNOWN = "unknown"

    @property
    def display_name(self) -> str:
        return _ADAPTER_NAMES[self]

    @classmethod
    def from_usb_ids(cls, vid: int | None, pid: int | None) -> AdapterKind:
        if vid is None or pid is None:
            return cls.UNKNOWN
        return _KNOWN_ADAPTERS.get((vid, pid), cls.UNKNOWN)


_ADAPTER_NAMES = {
    AdapterKind.CH340: "CH340",
    AdapterKind.PL2303: "PL2303",
    AdapterKind.FT232: "FT232",
    AdapterKind.CP210X: "CP210x",
    AdapterKind.STLINK_VCP: "ST-Link VCP",
    AdapterKind.UNKNOWN: "USB-serial",
}

_KNOWN_ADAPTERS: dict[tuple[int, int], AdapterKind] = {
    (0x1A86, 0x7523): AdapterKind.CH340,
    (0x1A86, 0x5523): AdapterKind.CH340,  # CH341
    (0x1A86, 0x55D4): AdapterKind.CH340,  # CH9102
    (0x067B, 0x2303): AdapterKind.PL2303,
    (0x0403, 0x6001): AdapterKind.FT232,
    (0x0403, 0x6015): AdapterKind.FT232,  # FT231X
    (0x10C4, 0xEA60): AdapterKind.CP210X,
    (0x0483, 0x374B): AdapterKind.STLINK_VCP,  # ST-LINK/V2-1 (Nucleo-64)
    (0x0483, 0x3752): AdapterKind.STLINK_VCP,  # ST-LINK/V2-1 without mass storage
    (0x0483, 0x374E): AdapterKind.STLINK_VCP,  # STLINK-V3
    (0x0483, 0x374F): AdapterKind.STLINK_VCP,
    (0x0483, 0x3753): AdapterKind.STLINK_VCP,
    (0x0483, 0x3754): AdapterKind.STLINK_VCP,
}


@dataclass(frozen=True)
class SerialPortInfo:
    device: str  # e.g. "/dev/cu.usbserial-120" or "COM5"
    description: str = ""
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    location: str | None = None
    # Another attached adapter has the same VID/PID/serial/location: only the port name differs.
    shares_usb_identity: bool = False

    @property
    def adapter_kind(self) -> AdapterKind:
        return AdapterKind.from_usb_ids(self.vid, self.pid)

    @property
    def stable_id(self) -> str:
        """Identity that survives re-plugging (device names may change)."""
        if self.vid is not None and self.pid is not None:
            usb = f"{self.vid:04x}:{self.pid:04x}"
            if self.shares_usb_identity:
                return f"serial:{usb}@{self.device}"
            if self.serial_number:
                return f"serial:{usb}:{self.serial_number}"
            if self.location:
                return f"serial:{usb}@{self.location}"
            return f"serial:{usb}"
        return f"serial:{self.device}"

    @property
    def label(self) -> str:
        short = self.device.removeprefix("/dev/cu.").removeprefix("/dev/")
        return f"{short} — {self.adapter_kind.display_name}"


@dataclass(frozen=True)
class ProbeInfo:
    name: str  # e.g. "STLink V2" as printed by `probe-rs list`
    vid: int
    pid: int
    serial_number: str | None = None

    @property
    def selector(self) -> str:
        """Value for `probe-rs --probe`."""
        base = f"{self.vid:04x}:{self.pid:04x}"
        return f"{base}:{self.serial_number}" if self.serial_number else base

    @property
    def stable_id(self) -> str:
        return f"probe:{self.selector}"

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class DfuDeviceInfo:
    """A chip in its ROM bootloader on USB ("STM32 BOOTLOADER"), as ``dfu-util -l`` lists it."""

    vid: int
    pid: int
    serial_number: str | None = None
    path: str = ""  # the USB path, e.g. "1-1" (changes with the socket)

    @property
    def usb_id(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}"

    @property
    def selector(self) -> tuple[str, ...]:
        """dfu-util arguments choosing this device."""
        args = ("-d", self.usb_id)
        if self.serial_number:
            return (*args, "-S", self.serial_number)
        return (*args, "-p", self.path) if self.path else args

    @property
    def stable_id(self) -> str:
        # The bootloader's serial number comes from the chip's unique ID: it stays the same in
        # every socket, so the project finds its board again after the next BOOT0 + Reset.
        if self.serial_number:
            return f"dfu:{self.usb_id}:{self.serial_number}"
        return f"dfu:{self.usb_id}@{self.path}"

    @property
    def label(self) -> str:
        return "STM32 BOOTLOADER (USB DFU)"


UploadTarget = ProbeInfo | SerialPortInfo | DfuDeviceInfo
