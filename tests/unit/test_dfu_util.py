"""Parsing dfu-util output and choosing the device (no USB, no processes)."""

from __future__ import annotations

from pathlib import Path

from nightexpress.models.entities import DfuDeviceInfo, UploadProgress, UploadStage
from nightexpress.services.dfu_util import (
    download_args,
    parse_devices,
    parse_progress,
    readback_args,
)

LISTING = [
    "dfu-util 0.11",
    'Found DFU: [0483:df11] ver=2200, devnum=5, cfg=1, intf=0, path="1-1", alt=1, '
    'name="@Option Bytes  /0x1FFFC000/01*016 e", serial="356D35693131"',
    'Found DFU: [0483:df11] ver=2200, devnum=5, cfg=1, intf=0, path="1-1", alt=0, '
    'name="@Internal Flash  /0x08000000/04*016Kg,01*064Kg,03*128Kg", serial="356D35693131"',
    'Found DFU: [1209:db42] ver=0100, devnum=7, cfg=1, intf=0, path="1-3", alt=0, '
    'name="UNKNOWN", serial="X"',
    'Found DFU: [0483:df11] ver=2200, devnum=9, cfg=1, intf=0, path="1-2", alt=0, '
    'name="@Internal Flash  /0x08000000/04*016Kg", serial="UNKNOWN"',
]


def test_one_entry_per_stm32_bootloader() -> None:
    assert parse_devices(LISTING) == [
        DfuDeviceInfo(0x0483, 0xDF11, "356D35693131", "1-1"),
        DfuDeviceInfo(0x0483, 0xDF11, None, "1-2"),  # no usable serial: told apart by path
    ]
    assert parse_devices(["dfu-util 0.11", "No DFU capable USB device available"]) == []


def test_device_is_addressed_by_serial_or_else_by_path() -> None:
    with_serial = DfuDeviceInfo(0x0483, 0xDF11, "356D35693131", "1-1")
    assert with_serial.selector == ("-d", "0483:df11", "-S", "356D35693131")
    assert with_serial.stable_id == "dfu:0483:df11:356D35693131"
    without = DfuDeviceInfo(0x0483, 0xDF11, None, "1-2")
    assert without.selector == ("-d", "0483:df11", "-p", "1-2")
    assert without.stable_id == "dfu:0483:df11@1-2"


def test_progress_lines() -> None:
    assert parse_progress("Erase   \t[=========                ]  36%        45056 bytes") == (
        UploadProgress(UploadStage.ERASING, 36)
    )
    assert parse_progress("Download\t[=========================] 100%   1064 bytes") == (
        UploadProgress(UploadStage.PROGRAMMING, 100)
    )
    assert parse_progress("Upload  \t[                         ]   0%  0 bytes") == (
        UploadProgress(UploadStage.VERIFYING, 0)
    )
    assert parse_progress("File downloaded successfully") is None


def test_flash_writes_then_reads_back_and_leaves() -> None:
    device = DfuDeviceInfo(0x0483, 0xDF11, "356D35693131", "1-1")
    image, out = Path("/tmp/x/firmware.bin"), Path("/tmp/x/readback.bin")
    assert download_args(device, image, 0x0800_0000) == [
        *device.selector, "-a", "0", "-s", "0x08000000", "-D", str(image),
    ]  # fmt: skip
    assert readback_args(device, out, 0x0800_0000, 1064) == [
        *device.selector, "-a", "0", "-s", "0x08000000:1064:leave", "-U", str(out),
    ]  # fmt: skip
