"""ProbeRsService, PortsController and UploadController with fake cargo / probe-rs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from nightexpress.controllers.build_controller import BuildController
from nightexpress.controllers.busy_tracker import BusyTracker
from nightexpress.controllers.ports_controller import PortsController, connection_name
from nightexpress.controllers.rtt_controller import RttController
from nightexpress.controllers.upload_controller import UploadController
from nightexpress.models.entities import (
    MemoryUsage,
    OperationState,
    ProbeInfo,
    ProjectConfig,
    SerialPortInfo,
    UploadMethod,
)
from nightexpress.models.state import BuildModel, PortsModel, ProjectModel, Screen, UploadModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.cargo_message_parser import strip_ansi
from nightexpress.services.cargo_service import CargoService
from nightexpress.services.dfu_util import DfuService
from nightexpress.services.probe_rs import ProbeRsErrorKind, ProbeRsService
from nightexpress.services.rtt_service import RttService
from nightexpress.views.main_window import FLASH_TAB, MainWindow

pytestmark = pytest.mark.ui

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FAKE_CARGO = str(FIXTURES / "fake_cargo.py")
FAKE_PROBE_RS = str(FIXTURES / "fake_probe_rs.py")
FAKE_DFU_UTIL = str(FIXTURES / "fake_dfu_util.py")
CATALOG = BoardCatalog.load()
STLINK = ProbeInfo("STLink V2", 0x0483, 0x3748, "2C3B15157416303030303032")
FT232 = SerialPortInfo("/dev/cu.usbserial-A50285BI", vid=0x0403, pid=0x6001, serial_number="A50")


def probe_service(scenario: str, parent) -> ProbeRsService:
    return ProbeRsService((sys.executable, FAKE_PROBE_RS, scenario), parent=parent)


# --- ProbeRsService -------------------------------------------------------------------------------


def test_service_lists_probes(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    service = probe_service("two", window)
    with qtbot.waitSignal(service.probes_listed, timeout=10_000) as blocker:
        assert service.list_probes()
    assert [p.serial_number for p in blocker.args[0]] == [
        "2C3B15157416303030303032",
        "0000000000000000AAAAAA",
    ]


def test_service_missing_probe_rs_lists_nothing(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    service = ProbeRsService(("/nonexistent/probe-rs",), parent=window)
    with qtbot.waitSignal(service.probes_listed, timeout=10_000) as blocker:
        service.list_probes()
    assert blocker.args == [[]]


@pytest.mark.parametrize(
    ("scenario", "ok", "kind"),
    [
        ("one", True, None),
        ("wrong_chip", False, None),
        ("none", False, ProbeRsErrorKind.PROBE_NOT_FOUND),
        ("not_responding", False, ProbeRsErrorKind.TARGET_NOT_RESPONDING),
    ],
)
def test_service_check_chip(qtbot, dispose, scenario: str, ok: bool, kind) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    service = probe_service(scenario, window)
    lines: list[str] = []
    service.log_line.connect(lines.append)
    with qtbot.waitSignal(service.chip_checked, timeout=10_000) as blocker:
        service.check_chip("STM32F103C6", STLINK.selector, 0xE0042000, 0x412)
    check = blocker.args[0]
    assert check.ok is ok
    assert (check.error.kind if check.error else None) is kind
    expected = f"--chip STM32F103C6 --non-interactive --probe {STLINK.selector} b32 0xE0042000 1"
    # stdout and stderr arrive through separate pipes, so their relative order is not fixed.
    assert f"ARGS read {expected}" in lines


# --- PortsController ------------------------------------------------------------------------------


class FakeSerial:
    def __init__(self, ports: list[SerialPortInfo]) -> None:
        self.ports = ports

    def list_ports(self) -> list[SerialPortInfo]:
        return list(self.ports)


class PortsEnv:
    def __init__(self, qtbot, dispose, settings_store, scenario: str, serial: list[SerialPortInfo]):
        self.qtbot = qtbot
        self.window = MainWindow("NightExpress", "0.1.0")
        dispose(self.window)
        self.ports = PortsModel()
        self.project = ProjectModel()
        self.settings = settings_store
        self.service = probe_service(scenario, self.window)
        self.serial = FakeSerial(serial)
        self.probe_rs_installed = True
        self.controller = PortsController(
            self.window,
            self.ports,
            self.project,
            self.service,
            self.serial,  # type: ignore[arg-type]
            self.settings,
            lambda: (sys.executable, FAKE_PROBE_RS, scenario) if self.probe_rs_installed else None,
            refresh_interval_ms=0,
        )

    def refresh(self) -> None:
        if self.probe_rs_installed:
            with self.qtbot.waitSignal(self.service.probes_listed, timeout=10_000):
                self.controller.refresh()
        else:
            self.controller.refresh()


def test_single_device_is_selected_automatically(qtbot, dispose, settings_store) -> None:
    env = PortsEnv(qtbot, dispose, settings_store, "one", [])
    env.refresh()
    assert env.ports.selected == STLINK
    assert env.window.toolbar.connection_button.text() == "ST-Link підключено"
    assert env.window.connection_status.text() == "ST-Link"
    assert env.window.target_view.probe.text() == "ST-Link"
    assert env.window.target_view.shown_method is UploadMethod.PROBE_RS
    assert env.window.target_view.method_note.text() == ""  # a device is selected


def test_several_devices_need_a_choice_which_is_remembered(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    env = PortsEnv(qtbot, dispose, settings_store, "one", [FT232])
    project = ProjectConfig(tmp_path, "blink", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    env.project.open_project(project)
    qtbot.waitUntil(lambda: len(env.ports.targets) == 2, timeout=10_000)
    assert env.ports.selected is None  # ST-Link + FT232: never guess
    assert env.window.toolbar.connection_button.text() == "Вибрати пристрій"

    env.window.toolbar.port_selected.emit(STLINK.stable_id)
    assert env.ports.selected == STLINK
    assert settings_store.project_upload_target(tmp_path) == STLINK.stable_id

    env.ports.select(None)
    env.project.close_project()
    env.project.open_project(project)  # reopening restores the remembered device
    qtbot.waitUntil(lambda: env.ports.selected == STLINK, timeout=10_000)


def test_disconnect_is_not_undone_by_auto_selection(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    env = PortsEnv(qtbot, dispose, settings_store, "one", [])
    project = ProjectConfig(tmp_path, "blink", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    env.project.open_project(project)
    qtbot.waitUntil(lambda: env.ports.selected == STLINK, timeout=10_000)  # the only device

    env.window.toolbar.port_selected.emit(None)  # "Disconnect"
    assert env.ports.selected is None
    env.refresh()  # the periodic refresh must not pick the single device again
    assert env.ports.selected is None
    assert env.window.toolbar.connection_button.text() == "Вибрати пристрій"

    env.project.close_project()
    env.project.open_project(project)  # remembered for the project, too
    env.refresh()
    assert env.ports.selected is None

    env.window.toolbar.port_selected.emit(STLINK.stable_id)  # an explicit choice ends it
    assert not settings_store.project_device_detached(tmp_path)
    env.ports.select(None)
    env.refresh()
    assert env.ports.selected == STLINK


def test_device_menu_opens_inside_the_window(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    window.resize(1200, 800)
    window.show()
    window.set_screen(Screen.EDITOR)  # the toolbar lives on the editor page
    button = window.toolbar.connection_button
    menu = window.toolbar.port_menu
    menu.addAction("FT232 «ser2» · usbserial-A50285BI — a long adapter name")
    menu.popup(button.mapToGlobal(button.rect().bottomLeft()))  # where Qt would put it
    qtbot.waitUntil(menu.isVisible, timeout=2000)
    button_right = button.mapToGlobal(button.rect().topRight()).x()
    assert menu.geometry().right() <= button_right + 1  # opens leftwards from the button
    assert menu.x() >= window.mapToGlobal(window.rect().topLeft()).x()
    menu.hide()


PL2303 = SerialPortInfo("/dev/cu.usbserial-120", vid=0x067B, pid=0x2303, location="1-1")


def test_new_device_is_offered_but_never_selected(qtbot, dispose, settings_store, tmp_path) -> None:
    env = PortsEnv(qtbot, dispose, settings_store, "one", [FT232, PL2303])
    env.probe_rs_installed = False
    env.project.open_project(
        ProjectConfig(tmp_path, "blink", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    )
    assert env.window.toasts.toasts == ()  # what is attached at start is not "new"

    env.probe_rs_installed = True  # an ST-Link is plugged in
    env.refresh()
    assert env.ports.selected is None  # the choice stays with the user
    [toast] = env.window.toasts.toasts
    assert toast.title_text == "Знайдено: STLink V2"
    env.refresh()
    assert len(env.window.toasts.toasts) == 1  # offered once, not on every refresh

    toast.action_button.click()
    assert env.ports.selected == STLINK
    assert settings_store.project_upload_target(tmp_path) == STLINK.stable_id


def test_new_device_is_not_offered_when_one_is_selected(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    env = PortsEnv(qtbot, dispose, settings_store, "one", [FT232])
    env.probe_rs_installed = False
    env.project.open_project(
        ProjectConfig(tmp_path, "blink", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    )
    assert env.ports.selected == FT232  # the only device
    env.probe_rs_installed = True
    env.refresh()
    assert env.ports.selected == FT232
    assert env.window.toasts.toasts == ()


def test_without_probe_rs_only_serial_adapters(qtbot, dispose, settings_store) -> None:
    env = PortsEnv(qtbot, dispose, settings_store, "one", [FT232])
    env.probe_rs_installed = False
    env.refresh()
    assert env.ports.targets == (FT232,)


def test_connection_name() -> None:
    assert connection_name(STLINK) == "ST-Link"
    assert connection_name(FT232) == "FT232"
    assert connection_name(None) is None


# --- UploadController -----------------------------------------------------------------------------


class UploadEnv:
    def __init__(
        self,
        qtbot,
        dispose,
        tmp_path: Path,
        probe_scenario: str = "one",
        with_rtt: bool = False,
        board_id: str = "stm32f103c6-minimal",
    ) -> None:
        self.qtbot = qtbot
        self.window = MainWindow("NightExpress", "0.1.0")
        dispose(self.window)
        self.errors: list[tuple[str, str]] = []
        self.window.show_error = lambda title, text: self.errors.append((title, text))
        self.project = ProjectModel()
        self.build = BuildModel()
        self.upload = UploadModel()
        self.ports = PortsModel()
        self.cargo_scenario = "success"
        self.probe_scenario = probe_scenario
        self.probe_rs_installed = True
        self.dfu_scenario = "one"
        self.dfu_installed = True
        self.dfu = DfuService(parent=self.window)
        self.builds = BuildController(
            self.window,
            self.project,
            self.build,
            CATALOG,
            CargoService(parent=self.window),
            lambda: (sys.executable, FAKE_CARGO, self.cargo_scenario),
            save_all=lambda: True,
            open_file=lambda _p: True,
            read_memory=lambda *_a: MemoryUsage(1064, 32768, 4, 10240),
        )
        self.service = ProbeRsService(parent=self.window)
        self.rtt: RttController | None = None
        if with_rtt:
            self.rtt = RttController(
                self.window,
                self.project,
                self.upload,
                RttService(parent=self.window),
                lambda: (sys.executable, FAKE_PROBE_RS, self.probe_scenario),
            )
        self.controller = UploadController(
            self.window,
            self.project,
            self.upload,
            self.ports,
            CATALOG,
            self.builds,
            self.service,
            lambda: (
                (sys.executable, FAKE_PROBE_RS, self.probe_scenario)
                if self.probe_rs_installed
                else None
            ),
            install_hint="brew install probe-rs-tools",
            probe_user=self.rtt,
            dfu=self.dfu,
            dfu_command=lambda: (
                (sys.executable, FAKE_DFU_UTIL, self.dfu_scenario) if self.dfu_installed else None
            ),
            dfu_install_hint="brew install dfu-util",
            dfu_install_command="brew install dfu-util",
        )
        if self.rtt is not None:
            self.controller.probe_flashed.connect(self.rtt.on_flashed)
        self.busy = BusyTracker(self.window, self.build, self.upload)
        self.project_dir = tmp_path / "blinky"
        self.project_dir.mkdir()
        self.window.set_project("blinky", self.project_dir)
        self.project.open_project(
            ProjectConfig(self.project_dir, "blinky", board_id, UploadMethod.PROBE_RS)
        )
        self.ports.set_targets([STLINK, FT232])
        self.ports.select(STLINK.stable_id)

    def run(self) -> OperationState:
        with self.qtbot.waitSignal(
            self.upload.state_changed,
            timeout=30_000,
            check_params_cb=lambda state: state.is_finished,
        ) as blocker:
            assert self.controller.upload()
        return blocker.args[0]

    def log(self) -> str:
        return strip_ansi("\n".join(self.upload.log))


@pytest.fixture
def upload_env(qtbot, dispose, tmp_path: Path) -> UploadEnv:
    return UploadEnv(qtbot, dispose, tmp_path)


def test_build_and_flash_success(upload_env: UploadEnv) -> None:
    states: list[OperationState] = []
    upload_env.upload.state_changed.connect(states.append)
    assert upload_env.run() is OperationState.SUCCEEDED
    assert states == [OperationState.PREPARING, OperationState.RUNNING, OperationState.SUCCEEDED]
    log = upload_env.log()
    assert "Програматор: STLink V2 (0483:3748:2C3B15157416303030303032)" in log
    assert "IDCODE 0x10006412 ✓" in log
    assert "ARGS download --chip STM32F103C6 --non-interactive --probe" in log
    assert "--verify --reset --disable-progressbars" in log
    assert upload_env.upload.summary is not None and upload_env.upload.summary.success
    assert upload_env.upload.summary.message.startswith("Прошивку завершено й перевірено (")
    assert upload_env.window.bottom_tabs.currentIndex() == FLASH_TAB
    assert "Прошивку завершено" in upload_env.window.flash_panel.plain_text()
    assert upload_env.window.toolbar.upload_button.isEnabled()
    status = upload_env.window.activity_status
    assert status.text().startswith("Прошито за ") and status.property("state") == "ok"
    target = upload_env.window.target_view
    assert target.flash_usage.value.text().startswith("1\u202f064 / 32\u202f768 байт")
    assert re.fullmatch(r"о \d\d:\d\d · 1\u202f064 байти · ST-Link", target.last_upload.text())
    assert upload_env.window.toolbar.running is None


@pytest.mark.parametrize(
    ("scenario", "title", "hint"),
    [
        ("wrong_chip", "підключено інший чип", "medium-density"),
        ("verify_fail", "перевірка не пройшла", "не збігається"),
        ("busy", "ST-Link зайнятий", "STM32CubeIDE"),
        ("not_responding", "чип не відповідає", "SWDIO/SWCLK"),
        ("none", "ST-Link не знайдено", "USB-кабель"),
    ],
)
def test_flash_failures_are_explained(
    qtbot, dispose, tmp_path: Path, scenario, title, hint
) -> None:
    env = UploadEnv(qtbot, dispose, tmp_path, scenario)
    assert env.run() is OperationState.FAILED
    assert env.upload.summary is not None and not env.upload.summary.success
    assert title in env.upload.summary.message
    assert hint in env.log()


def test_build_error_stops_before_flashing(upload_env: UploadEnv) -> None:
    upload_env.cargo_scenario = "compile_error"
    assert upload_env.run() is OperationState.FAILED
    assert "помилка збірки" in upload_env.upload.summary.message
    assert "ARGS read" not in upload_env.log()


def test_cancel_during_download(qtbot, dispose, tmp_path: Path) -> None:
    env = UploadEnv(qtbot, dispose, tmp_path, "slow")
    with qtbot.waitSignal(
        env.upload.progress_changed,
        timeout=30_000,
        check_params_cb=lambda p: p is not None and p.stage.value == "programming",
    ):
        env.controller.upload()
    assert not env.window.toolbar.verify_button.isEnabled()
    with qtbot.waitSignal(
        env.upload.state_changed, timeout=10_000, check_params_cb=lambda s: s.is_finished
    ):
        env.window.stop_requested.emit()
    assert env.upload.state is OperationState.CANCELLED
    assert env.upload.summary.message == "Прошивання зупинено"
    assert env.window.toolbar.verify_button.isEnabled()


def test_closing_the_project_stops_the_download(qtbot, dispose, tmp_path: Path) -> None:
    env = UploadEnv(qtbot, dispose, tmp_path, "slow")
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


def test_preconditions(upload_env: UploadEnv) -> None:
    menus: list[bool] = []
    upload_env.window.show_device_menu = lambda: menus.append(True)
    upload_env.ports.select(None)
    assert not upload_env.controller.upload()
    assert menus == [True]  # devices are attached: offer the choice instead of an error
    assert upload_env.errors == []
    assert upload_env.window.toasts.toasts == ()

    upload_env.ports.set_targets([])
    assert not upload_env.controller.upload()
    [toast] = upload_env.window.toasts.toasts
    assert toast.title_text == "Пристрій не знайдено"
    assert toast.action_button is not None
    with upload_env.qtbot.waitSignal(upload_env.window.help_requested, timeout=1000) as blocker:
        toast.action_button.click()
    assert blocker.args == ["stlink"]
    assert menus == [True]  # nothing to choose from: no menu
    upload_env.ports.set_targets([STLINK, FT232])

    upload_env.ports.select(FT232.stable_id)
    assert not upload_env.controller.upload()
    assert upload_env.errors[-1][0] == "USB-TTL"

    upload_env.ports.select(STLINK.stable_id)
    upload_env.probe_rs_installed = False
    assert not upload_env.controller.upload()
    assert upload_env.errors[-1] == ("probe-rs не знайдено", "brew install probe-rs-tools")
    assert upload_env.upload.state is OperationState.IDLE


def test_plain_build_does_not_trigger_flashing(qtbot, upload_env: UploadEnv) -> None:
    with qtbot.waitSignal(upload_env.builds.build_finished, timeout=30_000):
        upload_env.builds.verify()
    assert upload_env.upload.state is OperationState.IDLE


def test_stop_during_the_build_phase_is_said_once(qtbot, dispose, tmp_path: Path) -> None:
    env = UploadEnv(qtbot, dispose, tmp_path)
    env.cargo_scenario = "slow"
    with qtbot.waitSignal(
        env.build.state_changed, timeout=10_000, check_params_cb=lambda s: s.is_busy
    ):
        env.controller.upload()
    qtbot.wait(200)  # cargo is running
    with qtbot.waitSignal(
        env.upload.state_changed, timeout=10_000, check_params_cb=lambda s: s.is_finished
    ):
        env.window.stop_requested.emit()
    assert env.upload.state is OperationState.CANCELLED
    assert [line for line in env.build.log if "Зупиняю" in line] == ["Зупиняю…"]
    assert "Зупиняю" not in env.log()


def test_log_of_the_last_upload_survives_a_build_that_cannot_start(upload_env: UploadEnv) -> None:
    assert upload_env.run() is OperationState.SUCCEEDED
    log, summary = upload_env.log(), upload_env.upload.summary
    upload_env.builds._save_all = lambda: False  # e.g. the user cancelled the save dialog
    assert not upload_env.controller.upload()
    assert upload_env.upload.state is OperationState.SUCCEEDED
    assert upload_env.log() == log and upload_env.upload.summary == summary


def test_project_folder_gone_fails_cleanly(upload_env: UploadEnv) -> None:
    """cargo cannot start in a missing folder, and Qt says so inside start()."""
    upload_env.project_dir.rmdir()
    states: list[OperationState] = []
    upload_env.upload.state_changed.connect(states.append)
    upload_env.controller.upload()
    upload_env.qtbot.waitUntil(lambda: upload_env.upload.state.is_finished, timeout=10_000)
    assert upload_env.upload.state is OperationState.FAILED
    assert OperationState.PREPARING in states
    assert not upload_env.controller.is_busy


def test_build_without_an_executable_is_explained(upload_env: UploadEnv) -> None:
    upload_env.cargo_scenario = "library"
    assert upload_env.run() is OperationState.FAILED
    assert "не створила виконуваного файлу" in upload_env.log()
    assert "Збірка не вдалася" not in upload_env.log()
    assert "немає виконуваного файлу" in upload_env.upload.summary.message


def test_build_and_flash_in_english(qtbot, dispose, tmp_path: Path) -> None:
    from nightexpress import i18n

    i18n.install("en")  # the source_language fixture puts Ukrainian back
    env = UploadEnv(qtbot, dispose, tmp_path)
    assert env.run() is OperationState.SUCCEEDED
    log = env.log()
    assert "Probe: STLink V2 (0483:3748:2C3B15157416303030303032)" in log
    assert "IDCODE 0x10006412 ✓" in log
    assert not re.search(r"[А-Яа-яІіЇїЄєҐґ]", log), log
    assert env.upload.summary is not None
    assert env.upload.summary.message.startswith("Flashing finished and verified (")
    assert env.window.activity_status.text().startswith("Flashed in ")
    assert env.window.toolbar.upload_button.text() == "Build and flash"
    target = env.window.target_view
    assert target.flash_usage.value.text().startswith("1,064 / 32,768 bytes (3%)")
    assert re.fullmatch(r"at \d\d:\d\d · 1,064 bytes · ST-Link", target.last_upload.text())


def test_results_on_screen_follow_a_change_of_the_language(upload_env: UploadEnv) -> None:
    from PySide6.QtWidgets import QApplication
    from tests.ui.test_translation_coverage import CYRILLIC, shown_texts

    from nightexpress import i18n
    from nightexpress.app import install_qt_translation
    from nightexpress.controllers.language_controller import switch_language

    def qt_translation(language: str) -> None:
        install_qt_translation(QApplication.instance(), language)

    assert upload_env.run() is OperationState.SUCCEEDED
    window = upload_env.window
    assert window.activity_status.text().startswith("Прошито за ")
    try:
        switch_language("en", qt_translation, [window])
        assert window.activity_status.text().startswith("Flashed in ")
        target = window.target_view
        assert re.fullmatch(r"at \d\d:\d\d · 1,064 bytes · ST-Link", target.last_upload.text())
        assert target.flash_usage.value.text().startswith("1,064 / 32,768 bytes (3%)")
        assert [t for t in shown_texts(window) if CYRILLIC.search(t)] == []
    finally:
        switch_language(i18n.SOURCE_LANGUAGE, qt_translation, [window])
    assert window.activity_status.text().startswith("Прошито за ")
    assert target.flash_usage.value.text().startswith("1\u202f064 / 32\u202f768 байт")


# --- RTT -----------------------------------------------------------------------------------


def test_rtt_attaches_after_flashing_and_steps_aside_for_the_next_flash(
    qtbot, dispose, tmp_path: Path, monkeypatch
) -> None:
    from nightexpress.controllers import rtt_controller
    from nightexpress.views.main_window import RTT_TAB

    monkeypatch.setattr(rtt_controller, "has_rtt", lambda _elf: True)  # a defmt program
    env = UploadEnv(qtbot, dispose, tmp_path, with_rtt=True)
    panel = env.window.rtt_panel
    assert env.run() is OperationState.SUCCEEDED
    assert env.rtt is not None
    qtbot.waitUntil(lambda: "tick 2" in panel.output.plain_text(), timeout=10_000)
    assert env.rtt.is_attached and panel.connect_button.isChecked()
    assert env.window.bottom_tabs.currentIndex() == RTT_TAB
    assert panel.status.text().startswith("Підключено: STM32F103C6 через STLink V2")
    log = panel.output.plain_text()
    assert "--rtt-channel-mode no-block-skip" in log  # the program never hangs after we go

    # The next flash needs the probe: the reader stops first, then starts again.
    assert env.run() is OperationState.SUCCEEDED
    flash_log = env.log()
    assert "ARGS read" in flash_log
    qtbot.waitUntil(panel.connect_button.isChecked, timeout=10_000)  # attached and running

    panel.connect_button.click()  # the user detaches
    qtbot.waitUntil(lambda: not env.rtt.is_attached, timeout=10_000)
    assert not panel.connect_button.isChecked() and panel.status.text() == "Відключено"
    panel.connect_button.click()  # ... and attaches again to the same program
    qtbot.waitUntil(panel.connect_button.isChecked, timeout=10_000)
    env.project.close_project()  # another project: the board runs something else now
    qtbot.waitUntil(lambda: not env.rtt.is_attached, timeout=10_000)
    assert panel.output.plain_text() == ""


def test_a_program_without_rtt_is_not_attached(qtbot, dispose, tmp_path: Path) -> None:
    env = UploadEnv(qtbot, dispose, tmp_path, with_rtt=True)
    assert env.run() is OperationState.SUCCEEDED  # the fake ELF has no _SEGGER_RTT
    assert env.rtt is not None and not env.rtt.is_attached
    assert "defmt-rtt" in env.window.rtt_panel.status.text()
