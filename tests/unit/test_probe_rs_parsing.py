from __future__ import annotations

import pytest

from nightexpress.models.entities import SerialPortInfo
from nightexpress.services.probe_rs import (
    ProbeRsErrorKind,
    classify_error,
    describe_dev_id,
    parse_probe_list,
    parse_read_word,
)
from nightexpress.services.serial_port_service import SerialPortService

# Recorded from probe-rs 0.32.0 on the developer's machine.
LIST_ONE = """The following debug probes were found:
[0]: STLink V2 -- 0483:3748:2C3B15157416303030303032 (ST-LINK)
"""
LIST_NONE = "No debug probes were found.\n"


def test_parse_one_probe() -> None:
    [probe] = parse_probe_list(LIST_ONE)
    assert (probe.name, probe.vid, probe.pid) == ("STLink V2", 0x0483, 0x3748)
    assert probe.serial_number == "2C3B15157416303030303032"
    assert probe.selector == "0483:3748:2C3B15157416303030303032"


def test_parse_no_probes_and_variants() -> None:
    assert parse_probe_list(LIST_NONE) == []
    probes = parse_probe_list(
        "[0]: CMSIS-DAP v2 -- 2e8a:000c:E66058388B6B2B31 (CMSIS-DAP)\n"
        "[1]: J-Link -- 1366:0101\n"
        "garbage line\n"
    )
    assert [(p.name, p.serial_number) for p in probes] == [
        ("CMSIS-DAP v2", "E66058388B6B2B31"),
        ("J-Link", None),
    ]


def test_parse_read_word() -> None:
    assert parse_read_word("e0042000: 10006412\n") == 0x10006412
    assert parse_read_word("WARN something\ne0042000: 0\n") == 0
    assert parse_read_word("Error: nope") is None


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Error: No connected probes were found.", ProbeRsErrorKind.PROBE_NOT_FOUND),
        ("Error: The debug probe is already in use.", ProbeRsErrorKind.PROBE_UNAVAILABLE),
        ("USB error: Access denied (insufficient permissions)", ProbeRsErrorKind.PROBE_UNAVAILABLE),
        ("Error: Connecting to the chip was unsuccessful.", ProbeRsErrorKind.TARGET_NOT_RESPONDING),
        ('Error: The chip "X" was not found in the database.', ProbeRsErrorKind.UNKNOWN_CHIP),
        ("Error: Verification failed", ProbeRsErrorKind.VERIFY_FAILED),
        ("Error: something completely new", ProbeRsErrorKind.UNKNOWN),
        ("Error: Failed to attach to the core.", ProbeRsErrorKind.TARGET_NOT_RESPONDING),
        # Paths and words that only look like a connection problem.
        ("Error: Failed to open target/thumbv7m-none-eabi/debug/app", ProbeRsErrorKind.UNKNOWN),
        ("Error: could not read attachments.bin", ProbeRsErrorKind.UNKNOWN),
    ],
)
def test_classify_error(text: str, kind: ProbeRsErrorKind) -> None:
    error = classify_error(text)
    assert error.kind is kind
    assert error.hint
    assert error.details == text


def test_describe_dev_id() -> None:
    assert "low-density" in describe_dev_id(0x412)
    assert "0x7FF" in describe_dev_id(0x7FF)


class FakePort:
    def __init__(self, device: str, vid: int | None, pid: int | None, **extra: object) -> None:
        self.device, self.vid, self.pid = device, vid, pid
        self.description = extra.get("description", "")
        self.serial_number = extra.get("serial_number")
        self.location = extra.get("location")


def test_serial_ports_from_metadata_only() -> None:
    service = SerialPortService(
        lambda: [
            FakePort("/dev/cu.usbserial-A50285BI", 0x0403, 0x6001, serial_number="A50285BI"),
            FakePort("/dev/cu.Bluetooth-Incoming-Port", None, None),
            FakePort("/dev/cu.usbserial-120", 0x067B, 0x2303, location="1-1"),
        ]
    )
    ports = service.list_ports()
    assert [p.device for p in ports] == ["/dev/cu.usbserial-120", "/dev/cu.usbserial-A50285BI"]
    assert all(isinstance(p, SerialPortInfo) for p in ports)
    assert ports[1].stable_id == "serial:0403:6001:A50285BI"
