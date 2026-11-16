from __future__ import annotations

from pathlib import Path

import pytest

from nightexpress.models.entities import (
    AdapterKind,
    BoardDefinition,
    BootloaderInfo,
    BuildProfile,
    ButtonInfo,
    ChipDefinition,
    Diagnostic,
    DiagnosticLevel,
    LedInfo,
    MemoryUsage,
    NewProjectRequest,
    OperationState,
    ProbeInfo,
    ProjectConfig,
    SerialPortInfo,
    SupportStatus,
    ToolStatus,
    UartInfo,
    UploadMethod,
    UploadMethodSupport,
    chip_model_from_marking,
    is_valid_pin,
    project_name_error,
)


def make_chip(**overrides: object) -> ChipDefinition:
    fields: dict[str, object] = {
        "id": "STM32F103C6",
        "family": "STM32F1",
        "core": "cortex-m3",
        "target_triple": "thumbv7m-none-eabi",
        "flash_kb": 32,
        "ram_kb": 10,
        "embassy_feature": "stm32f103c6",
        "probe_rs_name": "STM32F103C6",
        "dev_id": 0x412,
        "idcode_address": 0xE0042000,
        "flash_size_address": 0x1FFFF7E0,
        "default_sysclk_hz": 8_000_000,
        "bootloader": BootloaderInfo("F1", "USART1", "PA9", "PA10"),
    }
    fields.update(overrides)
    return ChipDefinition(**fields)  # type: ignore[arg-type]


PROBE = UploadMethodSupport(UploadMethod.PROBE_RS, SupportStatus.VERIFIED)
SERIAL = UploadMethodSupport(UploadMethod.SERIAL_BOOTLOADER, SupportStatus.EXPERIMENTAL)


def make_board(**overrides: object) -> BoardDefinition:
    fields: dict[str, object] = {
        "id": "stm32f103c6-minimal",
        "display_names": {"uk": "STM32F103C6 minimal"},
        "chip_id": "STM32F103C6",
        "aliases": ("STM32F103C6T6A",),
        "led": LedInfo("PC13", active_low=True),
        "button": ButtonInfo("PA0", active_low=True, on_board=False),
        "uart": UartInfo("USART1", "PA9", "PA10"),
        "upload_methods": (PROBE, SERIAL),
        "default_upload_method": UploadMethod.PROBE_RS,
        "status": SupportStatus.VERIFIED,
    }
    fields.update(overrides)
    return BoardDefinition(**fields)  # type: ignore[arg-type]


# --- marking / pins -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("marking", "model"),
    [
        ("STM32F103C6T6A", "STM32F103C6"),
        ("stm32f103c6t6a", "STM32F103C6"),
        ("STM32 F103 C6T6A", "STM32F103C6"),
        ("STM32F411CEU6", "STM32F411CE"),
        ("STM32G0B1RET6", "STM32G0B1RE"),
        ("STM32WB55RG", "STM32WB55RG"),
        ("STM32F103C6", "STM32F103C6"),
    ],
)
def test_chip_model_from_marking(marking: str, model: str) -> None:
    assert chip_model_from_marking(marking) == model


@pytest.mark.parametrize("marking", ["", "103c6", "STM32F1", "ATMEGA328P", "STM32F103"])
def test_chip_model_from_marking_rejects_non_parts(marking: str) -> None:
    assert chip_model_from_marking(marking) is None


@pytest.mark.parametrize("pin", ["PA0", "PC13", "PB15", "PK15"])
def test_valid_pins(pin: str) -> None:
    assert is_valid_pin(pin)


@pytest.mark.parametrize("pin", ["", "PC16", "PZ1", "pc13", "C13", "PA", "PA01x"])
def test_invalid_pins(pin: str) -> None:
    assert not is_valid_pin(pin)


# --- chip -----------------------------------------------------------------------------------


def test_chip_sizes() -> None:
    chip = make_chip()
    assert chip.flash_bytes == 32 * 1024
    assert chip.ram_bytes == 10 * 1024


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "STM32F103C6T6A"},  # not normalized
        {"flash_kb": 0},
        {"ram_kb": -1},
        {"target_triple": "x86_64-unknown-linux-gnu"},
        {"dev_id": 0x1412},
        {"default_sysclk_hz": 0},
    ],
)
def test_chip_validation(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_chip(**overrides)


def test_bootloader_pins_validated() -> None:
    with pytest.raises(ValueError, match=r"bootloader\.tx"):
        BootloaderInfo("F1", "USART1", "PA99", "PA10")


# --- board ----------------------------------------------------------------------------------


def test_board_method_queries() -> None:
    board = make_board()
    assert board.supports(UploadMethod.SERIAL_BOOTLOADER)
    assert board.method_status(UploadMethod.PROBE_RS) is SupportStatus.VERIFIED
    assert board.method_status(UploadMethod.SERIAL_BOOTLOADER) is SupportStatus.EXPERIMENTAL


def test_board_method_status_for_unsupported_method() -> None:
    board = make_board(upload_methods=(PROBE,))
    assert not board.supports(UploadMethod.SERIAL_BOOTLOADER)
    assert board.method_status(UploadMethod.SERIAL_BOOTLOADER) is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "Bad_Id"}, "kebab-case"),
        ({"display_names": {"uk": "  "}}, "display_name"),
        ({"upload_methods": ()}, "at least one"),
        ({"upload_methods": (PROBE, PROBE)}, "duplicate"),
        ({"upload_methods": (SERIAL,)}, "default upload method"),
    ],
)
def test_board_validation(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_board(**overrides)


def test_button_pin_validated() -> None:
    with pytest.raises(ValueError, match="button"):
        ButtonInfo("PX1", active_low=True, on_board=True)


def test_led_pin_validated() -> None:
    with pytest.raises(ValueError, match="led"):
        LedInfo("PC20", active_low=True)


# --- project / diagnostics / memory -----------------------------------------------------------


def test_project_paths() -> None:
    cfg = ProjectConfig(Path("/p/blink"), "blink", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    assert cfg.build_profile is BuildProfile.RELEASE
    assert cfg.cargo_toml == Path("/p/blink/Cargo.toml")
    assert cfg.elf_path("thumbv7m-none-eabi") == Path(
        "/p/blink/target/thumbv7m-none-eabi/release/blink"
    )


def test_project_dev_profile_uses_debug_dir() -> None:
    cfg = ProjectConfig(Path("/p"), "x", "b", UploadMethod.PROBE_RS, BuildProfile.DEV)
    assert cfg.elf_path("t").parent.name == "debug"


def test_diagnostic_location() -> None:
    d = Diagnostic(DiagnosticLevel.ERROR, "oops", Path("src/main.rs"), 3, 5)
    assert d.has_location
    assert not Diagnostic(DiagnosticLevel.WARNING, "no file").has_location


def test_diagnostic_rejects_zero_based_positions() -> None:
    with pytest.raises(ValueError, match="1-based"):
        Diagnostic(DiagnosticLevel.ERROR, "x", Path("a.rs"), 0)


def test_memory_usage_percent() -> None:
    usage = MemoryUsage(flash_used=1064, flash_total=32 * 1024, ram_used=4, ram_total=10 * 1024)
    assert usage.flash_percent == pytest.approx(3.247, abs=1e-3)
    assert usage.fits
    assert not MemoryUsage(40_000, 32_768, 0, 10_240).fits


def test_memory_usage_validation() -> None:
    with pytest.raises(ValueError):
        MemoryUsage(-1, 1, 0, 1)
    with pytest.raises(ValueError):
        MemoryUsage(0, 0, 0, 1)


# --- ports ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("vid", "pid", "kind"),
    [
        (0x1A86, 0x7523, AdapterKind.CH340),
        (0x067B, 0x2303, AdapterKind.PL2303),
        (0x0403, 0x6001, AdapterKind.FT232),
        (0x10C4, 0xEA60, AdapterKind.CP210X),
        (0x1234, 0x5678, AdapterKind.UNKNOWN),
        (None, None, AdapterKind.UNKNOWN),
    ],
)
def test_adapter_kind(vid: int | None, pid: int | None, kind: AdapterKind) -> None:
    assert AdapterKind.from_usb_ids(vid, pid) is kind


def test_serial_port_stable_id_prefers_serial_number() -> None:
    ftdi = SerialPortInfo("/dev/cu.usbserial-A50285BI", "", 0x0403, 0x6001, "A50285BI", "1-2")
    assert ftdi.stable_id == "serial:0403:6001:A50285BI"
    assert ftdi.label == "usbserial-A50285BI — FT232"


def test_serial_port_stable_id_falls_back_to_location_then_device() -> None:
    pl = SerialPortInfo("/dev/cu.usbserial-120", vid=0x067B, pid=0x2303, location="1-1.2")
    assert pl.stable_id == "serial:067b:2303@1-1.2"
    assert SerialPortInfo("COM5").stable_id == "serial:COM5"


def test_probe_selector() -> None:
    probe = ProbeInfo("STLink V2", 0x0483, 0x3748, "2C3B15157416303030303032")
    assert probe.selector == "0483:3748:2C3B15157416303030303032"
    assert probe.stable_id == "probe:0483:3748:2C3B15157416303030303032"
    assert ProbeInfo("STLink V2", 0x0483, 0x3748).selector == "0483:3748"


# --- operation state / tools ----------------------------------------------------------------


def test_operation_state_flags() -> None:
    assert OperationState.RUNNING.is_busy
    assert OperationState.FAILED.is_finished
    assert not OperationState.IDLE.is_busy


@pytest.mark.parametrize(
    ("old", "new", "allowed"),
    [
        (OperationState.IDLE, OperationState.PREPARING, True),
        (OperationState.IDLE, OperationState.SUCCEEDED, False),
        (OperationState.PREPARING, OperationState.CANCELLED, True),
        (OperationState.RUNNING, OperationState.SUCCEEDED, True),
        (OperationState.RUNNING, OperationState.IDLE, False),
        (OperationState.SUCCEEDED, OperationState.RUNNING, True),
        (OperationState.FAILED, OperationState.IDLE, True),
    ],
)
def test_operation_transitions(old: OperationState, new: OperationState, allowed: bool) -> None:
    assert old.can_transition_to(new) is allowed


def test_tool_status_blocking() -> None:
    assert ToolStatus("cargo", found=False).is_blocking
    assert not ToolStatus("probe-rs", found=False, required=False).is_blocking


# --- project names --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["blink", "my-blink", "Blink_2", "a"])
def test_valid_project_names(name: str) -> None:
    assert project_name_error(name) is None


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("", "Введіть"),
        ("2blink", "починатися"),
        ("мій", "латинської"),
        ("my blink", "латинської"),
        ("../evil", "латинської"),
        ("test", "зарезервоване"),
        ("fn", "зарезервоване"),
        ("Self", "зарезервоване"),
        ("x" * 65, "задовга"),
    ],
)
def test_invalid_project_names(name: str, fragment: str) -> None:
    error = project_name_error(name)
    assert error is not None and fragment in error


def test_new_project_request() -> None:
    request = NewProjectRequest(
        "blink", Path("/home/dev/NightExpress"), "stm32f103c6-minimal", "blink"
    )
    assert request.project_dir == Path("/home/dev/NightExpress/blink")
    with pytest.raises(ValueError):
        NewProjectRequest("bad name", Path("/x"), "b", "blink")


def test_the_serial_port_of_an_st_link_is_named() -> None:
    from nightexpress.models.entities import AdapterKind, SerialPortInfo

    port = SerialPortInfo("/dev/cu.usbmodem1103", vid=0x0483, pid=0x374B)
    assert port.adapter_kind is AdapterKind.STLINK_VCP
    assert port.label == "usbmodem1103 — ST-Link VCP"
