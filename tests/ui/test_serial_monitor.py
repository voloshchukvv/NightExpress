"""Serial monitor: service (reader thread), UART panel and controller with a fake port."""

from __future__ import annotations

import queue
import threading

import pytest

from nightexpress.controllers.serial_monitor_controller import SerialMonitorController
from nightexpress.models.entities import ProjectConfig, SerialPortInfo, UploadMethod
from nightexpress.models.state import (
    DisplayMode,
    LineEnding,
    PortsModel,
    ProjectModel,
    SerialMonitorModel,
)
from nightexpress.services.serial_monitor_service import SerialMonitorService
from nightexpress.views.main_window import UART_TAB, MainWindow

pytestmark = pytest.mark.ui

FT232 = SerialPortInfo(
    "/dev/cu.usbserial-A50285BI", vid=0x0403, pid=0x6001, serial_number="A50285BI"
)
PL2303 = SerialPortInfo("/dev/cu.usbserial-120", vid=0x067B, pid=0x2303, location="1-1")


class FakePort:
    """A board on the other end: feed() simulates bytes it sends; written bytes are recorded."""

    def __init__(self) -> None:
        self.incoming: queue.Queue[bytes | Exception] = queue.Queue()
        self.written = bytearray()
        self.closed = threading.Event()

    def feed(self, data: bytes | Exception) -> None:
        self.incoming.put(data)

    def read(self, size: int = 1) -> bytes:
        if self.closed.is_set():
            return b""
        try:
            item = self.incoming.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def close(self) -> None:
        self.closed.set()


class Env:
    def __init__(self, qtbot, dispose, settings_store, tmp_path, ports: list[SerialPortInfo]):
        self.qtbot = qtbot
        self.window = MainWindow("NightExpress", "0.1.0")
        dispose(self.window)
        self.opened: list[tuple[str, int]] = []
        self.port = FakePort()
        self.fail_open: Exception | None = None
        self.service = SerialMonitorService(open_port=self._open, parent=self.window)
        self.model = SerialMonitorModel()
        self.ports = PortsModel()
        self.project = ProjectModel()
        self.settings = settings_store
        self.controller = SerialMonitorController(
            self.window, self.model, self.ports, self.project, settings_store, self.service
        )
        self.project.open_project(
            ProjectConfig(tmp_path, "blink", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
        )
        self.ports.set_targets(ports)

    def _open(self, device: str, baud: int) -> FakePort:
        if self.fail_open:
            raise self.fail_open
        self.opened.append((device, baud))
        self.port = FakePort()
        return self.port

    def close(self) -> None:
        self.service.close()


@pytest.fixture
def env(qtbot, dispose, settings_store, tmp_path):
    e = Env(qtbot, dispose, settings_store, tmp_path, [FT232])
    yield e
    e.close()


def test_single_adapter_is_preselected_and_shown(env: Env) -> None:
    panel = env.window.uart_panel
    assert env.model.port == FT232
    assert panel.current_port_id == FT232.stable_id
    assert panel.current_baud == 115200
    assert not panel.send_button.isEnabled()


def test_connect_receive_and_send(qtbot, env: Env) -> None:
    env.window.serial_monitor_requested.emit()  # menu "UART monitor"
    assert env.window.bottom_tabs.currentIndex() == UART_TAB
    assert env.opened == [(FT232.device, 115200)]
    assert env.model.is_open and env.window.uart_panel.connect_button.text() == "Відключити"

    env.port.feed(b"Hello from STM32 #0\r\n")
    env.port.feed(b"Hello from STM32 #1\r\n")
    panel = env.window.uart_panel
    qtbot.waitUntil(
        lambda: panel.text() == "Hello from STM32 #0\nHello from STM32 #1\n", timeout=3000
    )

    panel.input.setText("ping")
    panel.send_button.click()
    assert bytes(env.port.written) == b"ping\n"  # LF by default
    env.model.set_line_ending(LineEnding.CRLF)
    panel.input.setText("x")
    panel.input.returnPressed.emit()
    assert bytes(env.port.written).endswith(b"x\r\n")
    assert panel.input.text() == ""


def test_hex_mode_and_clear(qtbot, env: Env) -> None:
    env.controller.connect()
    env.window.uart_panel.display_mode_selected.emit(DisplayMode.HEX)
    env.port.feed(b"Hi\n")
    panel = env.window.uart_panel
    qtbot.waitUntil(lambda: panel.text() == "48 69 0A \n", timeout=3000)
    panel.clear_requested.emit()
    assert panel.text() == "" and env.model.buffer == b""


def test_baud_change_reopens_and_is_remembered(env: Env) -> None:
    env.controller.connect()
    env.window.uart_panel.baud_selected.emit(9600)
    assert env.opened[-1] == (FT232.device, 9600)
    assert env.settings.monitor_baud_rate() == 9600


def test_adapter_unplugged_while_connected(qtbot, env: Env) -> None:
    env.controller.connect()
    env.port.feed(OSError(6, "Device not configured"))
    qtbot.waitUntil(lambda: not env.model.is_open, timeout=3000)
    panel = env.window.uart_panel
    assert "Адаптер відключено" in panel.status.text()
    assert panel.connect_button.text() == "Підключити"


def test_open_failure_is_reported(env: Env) -> None:
    env.fail_open = OSError(16, "Resource busy")
    assert not env.controller.connect()
    assert "Resource busy" in env.window.uart_panel.status.text()
    assert not env.model.is_open


def test_two_adapters_need_a_choice_which_is_remembered(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    env = Env(qtbot, dispose, settings_store, tmp_path, [FT232, PL2303])
    try:
        assert env.model.port is None
        assert not env.controller.connect()
        assert "Виберіть USB-TTL адаптер" in env.window.uart_panel.status.text()
        env.window.uart_panel.port_selected.emit(PL2303.stable_id)
        assert env.model.port == PL2303
        assert settings_store.project_monitor_port(tmp_path) == PL2303.stable_id
        env.ports.set_targets([FT232])  # PL2303 unplugged: FT232 is NOT picked instead
        assert env.model.port is None
    finally:
        env.close()


def test_release_and_restore_for_uploads(env: Env) -> None:
    env.controller.connect()
    assert not env.controller.release("/dev/cu.other")
    assert env.controller.release(FT232.device)
    assert not env.model.is_open and env.port.closed.is_set()
    env.controller.restore()
    assert env.model.is_open
    assert env.opened == [(FT232.device, 115200), (FT232.device, 115200)]
    text = env.window.uart_panel.text()
    assert "— порт звільнено на час прошивання —" in text
    assert text.endswith("— перепідключено після прошивання —\n")


def test_sent_lines_are_recalled_with_arrows(qtbot, env: Env) -> None:
    from PySide6.QtCore import Qt

    env.controller.connect()
    field = env.window.uart_panel.input
    for line in ("led on", "led off", "led off"):
        field.setText(line)
        field.returnPressed.emit()
    assert field.history == ("led on", "led off")  # a repeated line is kept once
    field.setText("dra")
    qtbot.keyClick(field, Qt.Key.Key_Up)
    assert field.text() == "led off"
    qtbot.keyClick(field, Qt.Key.Key_Up)
    qtbot.keyClick(field, Qt.Key.Key_Up)  # already the oldest
    assert field.text() == "led on"
    qtbot.keyClick(field, Qt.Key.Key_Down)
    qtbot.keyClick(field, Qt.Key.Key_Down)
    assert field.text() == "dra"  # back to what was being typed


def test_pause_holds_data_without_losing_it(env: Env) -> None:
    panel = env.window.uart_panel
    panel.append_data(b"one\n")
    panel.pause_button.click()
    assert panel.is_paused
    panel.append_data(b"two\n")
    panel.append_data(b"three\n")
    assert panel.text() == "one\n" and panel.paused_bytes == 10
    panel.pause_button.click()
    assert panel.text() == "one\ntwo\nthree\n" and panel.paused_bytes == 0


def test_save_output_to_a_file(env: Env, tmp_path) -> None:
    panel = env.window.uart_panel
    panel.append_data(b"Hello\r\n")
    asked: list[object] = []
    target = tmp_path / "out" / "uart.log"
    target.parent.mkdir()

    def ask(title, start, file_filter=""):
        asked.append(start)
        return target

    env.window.ask_save_file = ask
    panel.save_button.click()
    assert target.read_text() == "Hello\n"
    assert asked[0].parent == tmp_path and asked[0].name.startswith("uart-")
    assert "uart.log" in env.window.activity_status.text()

    env.window.ask_save_file = lambda *args, **kwargs: None  # cancelled
    assert not env.controller.save_output()


def test_speed_and_line_ending_are_remembered_per_project(env: Env, tmp_path) -> None:
    panel = env.window.uart_panel
    panel.baud_selected.emit(9600)
    panel.line_ending_selected.emit(LineEnding.CRLF)
    assert env.settings.project_monitor_baud_rate(tmp_path) == 9600
    assert env.settings.project_monitor_line_ending(tmp_path) == "crlf"

    other = tmp_path / "other"
    other.mkdir()
    env.project.open_project(
        ProjectConfig(other, "other", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    )
    assert env.model.line_ending is LineEnding.CRLF  # nothing remembered: keeps the current one
    panel.baud_selected.emit(57600)
    panel.line_ending_selected.emit(LineEnding.LF)

    env.project.open_project(
        ProjectConfig(tmp_path, "blink", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    )
    assert env.model.baud_rate == 9600 and panel.current_baud == 9600
    assert env.model.line_ending is LineEnding.CRLF


def test_connected_state_is_visible(env: Env) -> None:
    panel = env.window.uart_panel
    assert panel.status_dot.isHidden() and panel.connect_button.objectName() == "connectButton"
    env.controller.connect()
    assert not panel.status_dot.isHidden()
    assert "FT232" in panel.status.text() or "usbserial" in panel.status.text()


def test_late_loss_of_an_old_reader_keeps_the_new_port_open(qtbot) -> None:
    ports = [FakePort(), FakePort()]
    service = SerialMonitorService(open_port=lambda _device, _baud: ports.pop(0))
    lost: list[str] = []
    service.connection_lost.connect(lost.append)
    service.open("/dev/a", 115200)
    old_stop = service._stop
    service.open("/dev/b", 115200)
    service._on_lost("unplugged", old_stop)  # queued by the first reader, delivered now
    assert service.is_open and service.device == "/dev/b" and lost == []
    service._on_lost("unplugged", service._stop)
    assert not service.is_open and lost == ["unplugged"]
