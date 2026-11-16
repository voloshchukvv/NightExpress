""" "Build and flash" through a USB-TTL adapter, against the simulated bootloader."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel

from nightexpress.controllers.build_controller import BuildController
from nightexpress.controllers.busy_tracker import BusyTracker
from nightexpress.controllers.ports_controller import PortsController
from nightexpress.controllers.upload_controller import UploadController
from nightexpress.models.entities import (
    MemoryUsage,
    OperationState,
    ProjectConfig,
    SerialPortInfo,
    UploadMethod,
)
from nightexpress.models.state import BuildModel, PortsModel, ProjectModel, UploadModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.cargo_service import CargoService
from nightexpress.services.elf_tools import elf_to_bin
from nightexpress.services.probe_rs import ProbeRsService
from nightexpress.services.serial_bootloader import BootloaderError, BootloaderErrorKind
from nightexpress.services.serial_flash_service import SerialFlashService
from nightexpress.views.dialogs.boot_mode_dialog import BootModeDialog
from nightexpress.views.main_window import FLASH_TAB, MainWindow

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
sys.path.insert(0, str(FIXTURES))
from fake_bootloader import FakeBootloader  # noqa: E402

pytestmark = pytest.mark.ui

CATALOG = BoardCatalog.load()
PL2303 = SerialPortInfo("/dev/cu.usbserial-120", vid=0x067B, pid=0x2303, location="1-1")
FT232 = SerialPortInfo("/dev/cu.usbserial-A50285BI", vid=0x0403, pid=0x6001, serial_number="A50")
BLINKY_ELF = FIXTURES / "elf" / "blinky.elf"


class SerialEnv:
    def __init__(self, qtbot, dispose, tmp_path: Path, settings_store, device: FakeBootloader):
        self.qtbot = qtbot
        self.window = MainWindow("NightExpress", "0.1.0")
        dispose(self.window)
        self.errors: list[tuple[str, str]] = []
        self.window.show_error = lambda title, text: self.errors.append((title, text))
        self.dialogs: list[tuple[str, str, str]] = []
        self.dialog_answer = (True, False)
        self.window.ask_boot_mode = self._ask
        self.device = device
        self.opened: list[str] = []
        self.settings = settings_store
        self.project = ProjectModel()
        self.build = BuildModel()
        self.upload = UploadModel()
        self.ports = PortsModel()
        builds = BuildController(
            self.window,
            self.project,
            self.build,
            CATALOG,
            CargoService(parent=self.window),
            lambda: (sys.executable, str(FIXTURES / "fake_cargo.py"), "success"),
            save_all=lambda: True,
            open_file=lambda _p: True,
            read_memory=lambda *_a: MemoryUsage(1064, 32768, 4, 10240),
        )
        # The fake cargo reports an ELF path that does not exist; serve the fixture ELF instead.
        builds.build_finished.connect(lambda r: object.__setattr__(r, "elf_path", BLINKY_ELF))
        self.serial = SerialFlashService(open_transport=self._open, parent=self.window)
        self.controller = UploadController(
            self.window,
            self.project,
            self.upload,
            self.ports,
            CATALOG,
            builds,
            ProbeRsService(parent=self.window),
            lambda: None,  # probe-rs is not needed for UART uploads
            serial_flash=self.serial,
            settings=self.settings,
        )
        BusyTracker(self.window, self.build, self.upload)
        project_dir = tmp_path / "blinky"
        project_dir.mkdir()
        self.window.set_project("blinky", project_dir)
        self.project.open_project(
            ProjectConfig(
                project_dir, "blinky", "stm32f103c6-minimal", UploadMethod.SERIAL_BOOTLOADER
            )
        )
        self.ports.set_targets([PL2303, FT232])
        self.ports.select(PL2303.stable_id)
        self.window.toolbar.set_ports([PL2303, FT232])

    def _ask(self, port_text: str, tx: str, rx: str) -> tuple[bool, bool]:
        self.dialogs.append((port_text, tx, rx))
        return self.dialog_answer

    def _open(self, device: str) -> FakeBootloader:
        self.opened.append(device)
        if device != PL2303.device:
            raise AssertionError(f"opened the wrong port: {device}")
        return self.device

    def run(self) -> OperationState:
        with self.qtbot.waitSignal(
            self.upload.state_changed, timeout=30_000, check_params_cb=lambda s: s.is_finished
        ) as blocker:
            assert self.controller.upload()
        return blocker.args[0]

    def log(self) -> str:
        return "\n".join(self.upload.log)


def make(qtbot, dispose, tmp_path, settings_store, **device) -> SerialEnv:
    return SerialEnv(qtbot, dispose, tmp_path, settings_store, FakeBootloader(**device))


def test_uart_upload_success(qtbot, dispose, tmp_path, settings_store) -> None:
    env = make(qtbot, dispose, tmp_path, settings_store)
    stages: list[str] = []
    env.upload.progress_changed.connect(lambda p: p and stages.append(p.stage.value))
    assert env.run() is OperationState.SUCCEEDED

    image = elf_to_bin(BLINKY_ELF)
    assert bytes(env.device.flash[: len(image.data)]) == image.data
    assert env.device.started_at == 0x08000000 and env.device.closed
    assert env.opened == [PL2303.device]  # only the selected adapter is ever opened
    assert env.dialogs == [("usbserial-120 — PL2303", "PA9", "PA10")]
    assert {"connecting", "erasing", "programming", "verifying", "resetting"} <= set(stages)
    assert "поверніть BOOT0 = 0 і натисніть Reset" in env.log()
    assert "Програму запущено" not in env.log()  # GO alone may not start it on the F1
    assert env.upload.summary.message.startswith("Прошивку завершено й перевірено (")
    assert env.window.bottom_tabs.currentIndex() == FLASH_TAB


def test_dialog_cancel_and_dont_show_again(qtbot, dispose, tmp_path, settings_store) -> None:
    env = make(qtbot, dispose, tmp_path, settings_store)
    env.dialog_answer = (False, False)
    assert env.run() is OperationState.CANCELLED
    assert env.opened == []  # nothing is sent to the port when the user cancels

    env.dialog_answer = (True, True)  # "Do not show again"
    assert env.run() is OperationState.SUCCEEDED
    assert settings_store.boot_dialog_hidden()
    assert env.run() is OperationState.SUCCEEDED
    assert len(env.dialogs) == 2  # the third upload did not ask


def test_silent_bootloader_is_explained(qtbot, dispose, tmp_path, settings_store) -> None:
    env = make(qtbot, dispose, tmp_path, settings_store, silent=True)
    env.device.timeout = 0.01
    assert env.run() is OperationState.FAILED
    assert env.upload.summary.message == "Прошивання не вдалося: бутлоадер не відповідає"
    assert "BOOT0 = 1" in env.log() and "PA10" in env.log()


def test_port_unavailable(qtbot, dispose, tmp_path, settings_store) -> None:
    env = make(qtbot, dispose, tmp_path, settings_store)

    def busy(_device: str) -> FakeBootloader:
        raise BootloaderError(BootloaderErrorKind.PORT_UNAVAILABLE, "[Errno 16] Resource busy")

    env.serial._open = busy
    assert env.run() is OperationState.FAILED
    assert "порт недоступний" in env.upload.summary.message
    assert "Resource busy" in env.log()


def test_cancel_during_uart_upload(qtbot, dispose, tmp_path, settings_store) -> None:
    env = make(qtbot, dispose, tmp_path, settings_store, on_write=lambda _a: time.sleep(0.2))
    with qtbot.waitSignal(
        env.upload.progress_changed,
        timeout=30_000,
        check_params_cb=lambda p: p is not None and p.stage.value == "programming",
    ):
        env.controller.upload()
    with qtbot.waitSignal(
        env.upload.state_changed, timeout=10_000, check_params_cb=lambda s: s.is_finished
    ):
        env.window.stop_requested.emit()
    assert env.upload.state is OperationState.CANCELLED
    assert env.device.started_at is None


def test_boot_mode_dialog_names_the_port(qtbot) -> None:
    dialog = BootModeDialog("usbserial-120 — PL2303 — до STM32", "PA9", "PA10")
    qtbot.addWidget(dialog)
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "usbserial-120 — PL2303 — до STM32" in texts
    assert "BOOT0 = 1" in texts
    assert dialog.flash_button.text() == "Прошити"


# --- adapters: re-plugging and naming ----------------------------------------------------------


class FakeSerialList:
    def __init__(self, ports: list[SerialPortInfo]) -> None:
        self.ports = ports

    def list_ports(self) -> list[SerialPortInfo]:
        return list(self.ports)


def test_remembered_adapter_found_after_replugging(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    ports, project = PortsModel(), ProjectModel()
    ftdi = SerialPortInfo(
        "/dev/cu.usbserial-A50285BI", vid=0x0403, pid=0x6001, serial_number="A50285BI"
    )
    serial = FakeSerialList([ftdi, PL2303])
    controller = PortsController(
        window, ports, project, ProbeRsService(parent=window), serial, settings_store,  # type: ignore[arg-type]
        lambda: None, refresh_interval_ms=0,
    )  # fmt: skip
    config = ProjectConfig(tmp_path, "blink", "stm32f103c6-minimal", UploadMethod.SERIAL_BOOTLOADER)
    project.open_project(config)
    assert ports.selected is None  # two adapters, nothing remembered
    controller.select(ftdi.stable_id)

    # Unplug: the remembered adapter disappears and nothing is selected instead of it.
    serial.ports = [PL2303]
    controller.refresh()
    assert ports.selected is None

    # Plugged into another USB socket: new device name, same identity -> found again.
    serial.ports = [PL2303, SerialPortInfo("/dev/cu.usbserial-A50285BI-2", vid=0x0403, pid=0x6001,
                                           serial_number="A50285BI")]  # fmt: skip
    controller.refresh()
    assert ports.selected is not None and ports.selected.device.endswith("-2")

    # Naming the adapter shows the name in the menu.
    window.ask_text = lambda *args, **kwargs: "інший пристрій"
    window.toolbar.rename_port_requested.emit(ftdi.stable_id)
    assert settings_store.adapter_label(ftdi.stable_id) == "інший пристрій"
    texts = [a.text() for a in window.toolbar.port_menu.actions()]
    assert any("«інший пристрій»" in t for t in texts)


def test_remembered_adapter_is_never_replaced_by_another(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    """PL2303 (to the STM32) unplugged, FT232 (other device) left alone: do not pick FT232."""
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    ports, project = PortsModel(), ProjectModel()
    serial = FakeSerialList([PL2303, FT232])
    controller = PortsController(
        window, ports, project, ProbeRsService(parent=window), serial, settings_store,  # type: ignore[arg-type]
        lambda: None, refresh_interval_ms=0,
    )  # fmt: skip
    project.open_project(
        ProjectConfig(tmp_path, "blink", "stm32f103c6-minimal", UploadMethod.SERIAL_BOOTLOADER)
    )
    controller.select(PL2303.stable_id)
    serial.ports = [FT232]
    controller.refresh()
    assert ports.selected is None
    assert window.toolbar.connection_button.text() == "Вибрати пристрій"


def test_adapter_unplugged_mid_transfer(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)

    class Unplugged(FakeBootloader):
        def write(self, data: bytes) -> int:
            if self.erased:
                raise OSError(6, "Device not configured")
            return super().write(data)

    service = SerialFlashService(open_transport=lambda _d: Unplugged(), parent=window)
    with qtbot.waitSignal(service.finished, timeout=10_000) as blocker:
        service.flash(PL2303.device, elf_to_bin(BLINKY_ELF), 0x412, 32 * 1024)
        with pytest.raises(RuntimeError, match="already running"):
            service.flash(PL2303.device, elf_to_bin(BLINKY_ELF), 0x412, 32 * 1024)
    error = blocker.args[0]
    assert error.kind is BootloaderErrorKind.PORT_UNAVAILABLE
    assert "Device not configured" in error.details
    assert not service.is_busy


def test_monitor_is_released_during_uart_upload(qtbot, dispose, tmp_path, settings_store) -> None:
    env = make(qtbot, dispose, tmp_path, settings_store)

    class Monitor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def release(self, device: str) -> bool:
            self.calls.append(f"release {device}")
            return True

        def restore(self) -> None:
            self.calls.append("restore")

    monitor = Monitor()
    env.controller._monitor = monitor
    assert env.run() is OperationState.SUCCEEDED
    assert monitor.calls == [f"release {PL2303.device}", "restore"]
    assert "Монітор UART тимчасово відключено" in env.log()


def test_device_of_one_project_is_not_used_by_another(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    ports, project = PortsModel(), ProjectModel()
    controller = PortsController(
        window, ports, project, ProbeRsService(parent=window), FakeSerialList([PL2303, FT232]),  # type: ignore[arg-type]
        settings_store, lambda: None, refresh_interval_ms=0,
    )  # fmt: skip
    first, second = tmp_path / "first", tmp_path / "second"
    method = UploadMethod.SERIAL_BOOTLOADER
    project.open_project(ProjectConfig(first, "first", "stm32f103c6-minimal", method))
    controller.select(PL2303.stable_id)

    # The other project never chose a device: the first one's adapter must not stay selected.
    project.open_project(ProjectConfig(second, "second", "stm32f103c6-minimal", method))
    assert ports.selected is None
    assert window.toolbar.connection_button.text() == "Вибрати пристрій"

    project.open_project(ProjectConfig(first, "first", "stm32f103c6-minimal", method))
    assert ports.selected == PL2303


def test_identical_clone_adapters_are_listed_separately(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    """Two CH340 clones: no serial number, no location — the same USB identity."""
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    ports, project = PortsModel(), ProjectModel()
    one = SerialPortInfo("/dev/cu.wchusbserial10", vid=0x1A86, pid=0x7523)
    two = SerialPortInfo("/dev/cu.wchusbserial20", vid=0x1A86, pid=0x7523)
    serial = FakeSerialList([one])
    controller = PortsController(
        window, ports, project, ProbeRsService(parent=window), serial, settings_store,  # type: ignore[arg-type]
        lambda: None, refresh_interval_ms=0,
    )  # fmt: skip
    controller.refresh()
    assert [t.stable_id for t in ports.targets] == ["serial:1a86:7523"]

    serial.ports = [one, two]
    controller.refresh()  # used to raise "duplicate upload target ids"
    assert [t.device for t in ports.targets] == [one.device, two.device]
    assert len({t.stable_id for t in ports.targets}) == 2
    controller.select(ports.targets[1].stable_id)
    assert ports.selected is not None and ports.selected.device == two.device


def test_crash_in_the_flasher_still_ends_the_upload(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)

    def broken(_device: str) -> FakeBootloader:
        raise KeyError("bug")

    service = SerialFlashService(open_transport=broken, parent=window)
    with qtbot.waitSignal(service.finished, timeout=10_000) as blocker:
        service.flash(PL2303.device, elf_to_bin(BLINKY_ELF), 0x412, 32 * 1024)
    assert isinstance(blocker.args[0], BootloaderError)
    assert "bug" in blocker.args[0].details
    assert not service.is_busy


def test_closing_the_project_stops_the_uart_upload(
    qtbot, dispose, tmp_path, settings_store
) -> None:
    env = make(qtbot, dispose, tmp_path, settings_store, on_write=lambda _a: time.sleep(0.2))
    with qtbot.waitSignal(
        env.upload.progress_changed,
        timeout=30_000,
        check_params_cb=lambda p: p is not None and p.stage.value == "programming",
    ):
        env.controller.upload()
    with qtbot.waitSignal(
        env.upload.state_changed, timeout=10_000, check_params_cb=lambda s: s.is_finished
    ):
        env.project.close_project()
    assert env.upload.state is OperationState.CANCELLED
    assert env.device.started_at is None  # the program was never started


def test_shutdown_stops_the_flasher_and_frees_the_port(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    device = FakeBootloader(on_write=lambda _a: time.sleep(0.2))
    service = SerialFlashService(open_transport=lambda _d: device, parent=window)
    service.shutdown()  # nothing is running: returns at once
    with qtbot.waitSignal(
        service.progress, timeout=30_000, check_params_cb=lambda p: p.stage.value == "programming"
    ):
        service.flash(PL2303.device, elf_to_bin(BLINKY_ELF), 0x412, 32 * 1024)
    with qtbot.waitSignal(service.finished, timeout=10_000) as blocker:
        service.shutdown()  # application exit: returns only after the worker let go
        assert device.closed
    assert blocker.args[0].kind is BootloaderErrorKind.CANCELLED
    assert device.started_at is None


def test_time_in_the_boot_dialog_is_not_flashing_time(
    qtbot, dispose, tmp_path, settings_store
) -> None:
    env = make(qtbot, dispose, tmp_path, settings_store)

    def slow_reader(*_args: str) -> tuple[bool, bool]:
        time.sleep(1.5)  # the user reads the BOOT0 instructions
        return True, False

    env.window.ask_boot_mode = slow_reader
    assert env.run() is OperationState.SUCCEEDED
    seconds = float(env.upload.summary.message.split("(")[1].split(" ")[0].replace(",", "."))
    assert seconds < 1.5
