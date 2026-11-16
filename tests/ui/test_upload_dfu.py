"""Flashing over USB DFU (dfu-util) with a fake dfu-util: the service, the device menu and the
"Build and flash" pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tests.ui.test_upload import FAKE_DFU_UTIL, FAKE_PROBE_RS, STLINK, FakeSerial, UploadEnv

from nightexpress.controllers.ports_controller import PortsController, connection_name
from nightexpress.models.entities import (
    DfuDeviceInfo,
    OperationState,
    ProjectConfig,
    UploadMethod,
    UploadStage,
)
from nightexpress.models.state import PortsModel, ProjectModel
from nightexpress.services.dfu_util import DfuErrorKind, DfuService
from nightexpress.services.elf_tools import BinaryImage, elf_to_bin
from nightexpress.views.main_window import MainWindow

pytestmark = pytest.mark.ui

BLACK_PILL = "stm32f411ce-blackpill"
DEVICE = DfuDeviceInfo(0x0483, 0xDF11, "356D35693131", "1-1")
BLINKY_ELF = Path(__file__).resolve().parents[1] / "fixtures" / "elf" / "blinky.elf"


@pytest.fixture(autouse=True)
def real_image(monkeypatch) -> None:
    """The fake cargo names an ELF that is not there: flash a real one instead."""
    monkeypatch.setattr(
        "nightexpress.controllers.upload_controller.elf_to_bin", lambda _elf: elf_to_bin(BLINKY_ELF)
    )


def dfu_service(scenario: str, parent) -> DfuService:
    service = DfuService(parent=parent)
    service.set_command((sys.executable, FAKE_DFU_UTIL, scenario))
    return service


def test_service_lists_bootloaders(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    service = dfu_service("two", window)
    with qtbot.waitSignal(service.devices_listed, timeout=10_000) as blocker:
        assert service.list_devices()
    assert [d.serial_number for d in blocker.args[0]] == ["356D35693131", "AAAA00000001"]


def test_missing_dfu_util_lists_nothing(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    service = DfuService(parent=window)
    service.set_command(("/nonexistent/dfu-util",))
    with qtbot.waitSignal(service.devices_listed, timeout=10_000) as blocker:
        service.list_devices()
    assert blocker.args == [[]]


@pytest.mark.parametrize(
    ("scenario", "kind"),
    [
        ("one", None),
        ("leave_error", None),  # the device vanishes on "leave": a complete read still counts
        ("bad_verify", DfuErrorKind.VERIFY_FAILED),
        ("none", DfuErrorKind.NO_DEVICE),
        ("access", DfuErrorKind.ACCESS_DENIED),
    ],
)
def test_service_flash(qtbot, dispose, scenario: str, kind) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    service = dfu_service(scenario, window)
    stages: list[UploadStage] = []
    service.progress.connect(lambda p: stages.append(p.stage))
    with qtbot.waitSignal(service.finished, timeout=20_000) as blocker:
        service.flash(DEVICE, BinaryImage(0x0800_0000, bytes(range(256)) * 4))
    error = blocker.args[0]
    assert (error.kind if error else None) is kind
    if kind is None:
        # The bar redrawn with "\r" arrives as it goes, not only at the end.
        assert stages.count(UploadStage.PROGRAMMING) == 3
        assert UploadStage.ERASING in stages and UploadStage.VERIFYING in stages


def test_cancel(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    service = dfu_service("slow", window)
    with qtbot.waitSignal(service.finished, timeout=20_000) as blocker:
        service.flash(DEVICE, BinaryImage(0x0800_0000, b"\x01" * 64))
        qtbot.wait(300)
        service.cancel()
    assert blocker.args[0].kind is DfuErrorKind.CANCELLED


# --- the device menu -------------------------------------------------------------------------


def test_board_in_dfu_mode_is_listed_with_the_probes(qtbot, dispose, settings_store) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    ports = PortsModel()
    from nightexpress.services.probe_rs import ProbeRsService

    probe_rs = ProbeRsService(parent=window)
    controller = PortsController(
        window,
        ports,
        ProjectModel(),
        probe_rs,
        FakeSerial([]),  # type: ignore[arg-type]
        settings_store,
        lambda: (sys.executable, FAKE_PROBE_RS, "one"),
        refresh_interval_ms=0,
        dfu=dfu_service("one", window),
        dfu_command=lambda: (sys.executable, FAKE_DFU_UTIL, "one"),
    )
    with qtbot.waitSignal(probe_rs.probes_listed, timeout=10_000):
        controller.refresh()
    assert [t.stable_id for t in ports.targets] == [STLINK.stable_id, DEVICE.stable_id]
    menu = [a.text() for a in window.toolbar.port_menu.actions() if a.text()]
    assert "Плати в режимі DFU (USB)" in menu
    assert "STM32 BOOTLOADER (USB DFU)" in menu
    controller.select(DEVICE.stable_id)
    assert window.toolbar.connection_button.text() == "USB DFU підключено"
    assert window.target_view.shown_method is UploadMethod.USB_DFU
    assert window.target_view.wiring_rows == (("USB", "USB (PA11/PA12)"),)
    assert connection_name(DEVICE) == "USB DFU"


# --- Build and flash --------------------------------------------------------------------------


def dfu_env(qtbot, dispose, tmp_path: Path, board_id: str = BLACK_PILL) -> UploadEnv:
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = UploadEnv(qtbot, dispose, tmp_path, board_id=board_id)
    env.ports.set_targets([STLINK, DEVICE])
    env.ports.select(DEVICE.stable_id)
    return env


def test_build_and_flash_over_usb_dfu(qtbot, dispose, tmp_path: Path) -> None:
    env = dfu_env(qtbot, dispose, tmp_path)
    assert env.run() is OperationState.SUCCEEDED
    log = env.log()
    assert "Пристрій: STM32 BOOTLOADER (USB DFU) 356D35693131" in log
    assert "-S 356D35693131 -a 0 -s 0x08000000 -D" in log
    assert ":leave -U" in log
    assert "Записане збігається з прошивкою ✓" in log
    assert env.upload.summary is not None and env.upload.summary.success
    assert env.window.target_view.last_upload.text().endswith("· USB DFU")


def test_dfu_failure_is_explained(qtbot, dispose, tmp_path: Path) -> None:
    env = dfu_env(qtbot, dispose, tmp_path)
    env.dfu_scenario = "bad_verify"
    assert env.run() is OperationState.FAILED
    assert env.upload.summary is not None
    assert env.upload.summary.message == "Прошивання не вдалося: перевірка не пройшла"
    assert "Записане не збігається з прошивкою (адреса 0x0800" in env.log()


def test_dfu_preconditions(qtbot, dispose, tmp_path: Path) -> None:
    env = dfu_env(qtbot, dispose, tmp_path / "a", board_id="stm32f103c6-minimal")
    assert not env.controller.upload()  # the F103 bootloader has no USB DFU
    assert env.errors[-1][0] == "USB DFU"
    env = dfu_env(qtbot, dispose, tmp_path / "b")
    env.dfu_installed = False
    assert not env.controller.upload()
    assert env.errors[-1] == ("dfu-util не знайдено", "brew install dfu-util")


def test_no_device_points_a_dfu_board_to_the_usb_help(qtbot, dispose, tmp_path: Path) -> None:
    env = dfu_env(qtbot, dispose, tmp_path)
    env.ports.set_targets([])
    shown: list[tuple] = []
    env.window.notify = lambda *args: shown.append(args)
    assert not env.controller.upload()
    assert "режимі DFU" in shown[-1][1]
    env.project.open_project(
        ProjectConfig(env.project_dir, "blinky", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    )
    assert not env.controller.upload()
    assert "ST-Link" in shown[-1][1] and "DFU" not in shown[-1][1]


def test_no_device_and_no_dfu_util_suggests_installing_it(qtbot, dispose, tmp_path: Path) -> None:
    from PySide6.QtGui import QGuiApplication

    env = dfu_env(qtbot, dispose, tmp_path)
    env.ports.set_targets([])
    env.dfu_installed = False
    shown: list[tuple] = []
    env.window.notify = lambda *args: shown.append(args)
    assert not env.controller.upload()
    title, text, _kind, button, action = shown[-1]
    assert title == "Встановіть dfu-util"
    assert "brew install dfu-util" in text
    assert button == "Скопіювати команду"
    action()
    assert QGuiApplication.clipboard().text() == "brew install dfu-util"


def test_flashing_over_usb_makes_rtt_forget_the_old_program(qtbot, dispose, tmp_path: Path) -> None:
    from nightexpress.controllers.upload_controller import FlashedTarget

    tmp_path.mkdir(parents=True, exist_ok=True)
    env = UploadEnv(qtbot, dispose, tmp_path, with_rtt=True, board_id=BLACK_PILL)
    env.ports.set_targets([STLINK, DEVICE])
    env.ports.select(DEVICE.stable_id)
    assert env.rtt is not None
    # As if an earlier program had been flashed through the ST-Link and read over RTT.
    env.rtt._target = FlashedTarget(tmp_path / "old.elf", "STM32F411CE", STLINK.selector, "STLink")
    assert env.run() is OperationState.SUCCEEDED
    assert env.rtt._target is None  # "Connect" no longer attaches with the old ELF
    assert not env.rtt._service.is_running
