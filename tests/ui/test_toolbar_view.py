from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from nightexpress.models.entities import BuildProfile, ProbeInfo, SerialPortInfo
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.views.toolbar_view import NO_DEVICE_TEXT, ToolbarView, board_label

pytestmark = pytest.mark.ui

STLINK = ProbeInfo("STLink V2", 0x0483, 0x3748, "2C3B15157416303030303032")
STLINK_2 = ProbeInfo("STLink V2", 0x0483, 0x3748, "0000000000000000AAAAAA")
PL2303 = SerialPortInfo("/dev/cu.usbserial-120", vid=0x067B, pid=0x2303, location="1-1")
FT232 = SerialPortInfo("/dev/cu.usbserial-A50285BI", vid=0x0403, pid=0x6001, serial_number="A50")
BOARDS = BoardCatalog.load().boards


@pytest.fixture
def toolbar(qtbot) -> ToolbarView:
    view = ToolbarView()
    qtbot.addWidget(view)
    return view


def items(combo) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def menu_texts(toolbar: ToolbarView) -> list[str]:
    return [a.text() for a in toolbar.port_menu.actions() if not a.isSeparator()]


def device_actions(toolbar: ToolbarView) -> list:
    return [a for a in toolbar.port_menu.actions() if a.data()]


def test_build_buttons_emit_intents(qtbot, toolbar: ToolbarView) -> None:
    assert toolbar.verify_button.text() == "Зібрати"
    assert toolbar.upload_button.text() == "Зібрати й прошити"
    for button, signal in (
        (toolbar.verify_button, toolbar.verify_requested),
        (toolbar.upload_button, toolbar.upload_requested),
    ):
        with qtbot.waitSignal(signal, timeout=1000):
            qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def test_board_combo_shows_chip_marking(toolbar: ToolbarView) -> None:
    toolbar.set_boards(BOARDS)
    assert items(toolbar.board_combo) == [
        "STM32F103C6T6A",
        "STM32F103C8T6",
        "STM32F401CCU6",
        "STM32F411CEU6",
        "STM32F103RBT6",
        "STM32F401RET6",
        "STM32F411RET6",
        "STM32F446RET6",
        "STM32L476RGT6",
    ]
    assert board_label(BOARDS[0]) == "STM32F103C6T6A"
    assert toolbar.current_board_id == "stm32f103c6-minimal"


def test_target_combo_lists_only_chip_targets(toolbar: ToolbarView) -> None:
    toolbar.set_targets(["thumbv7m-none-eabi"], "thumbv7m-none-eabi")
    assert items(toolbar.target_combo) == ["thumbv7m-none-eabi"]


def test_target_is_plain_text_without_a_list(qtbot, toolbar: ToolbarView) -> None:
    """One target per chip: clicking it must not open a list with that single item."""
    toolbar.show()
    combo = toolbar.target_combo
    assert combo.focusPolicy() is Qt.FocusPolicy.NoFocus
    combo.showPopup()
    assert not combo.view().isVisible()
    toolbar.profile_combo.showPopup()  # ordinary combos still open
    assert toolbar.profile_combo.view().isVisible()
    toolbar.profile_combo.hidePopup()


def test_profile_selection(qtbot, toolbar: ToolbarView) -> None:
    assert items(toolbar.profile_combo) == ["debug", "release"]
    assert toolbar.current_profile is BuildProfile.RELEASE
    toolbar.set_profile(BuildProfile.DEV)
    assert toolbar.current_profile is BuildProfile.DEV
    with qtbot.waitSignal(toolbar.profile_selected) as blocker:
        toolbar.profile_combo.setCurrentIndex(1)  # user picks "release" ...
        toolbar.profile_combo.activated.emit(1)  # ... which Qt reports as "activated"
    assert blocker.args == [BuildProfile.RELEASE]


def test_project_combo(qtbot, toolbar: ToolbarView, tmp_path: Path) -> None:
    a, b = tmp_path / "Blink", tmp_path / "uart_test"
    toolbar.set_projects([(a, "Blink"), (b, "uart_test")], a)
    assert items(toolbar.project_combo) == ["Blink", "uart_test", "Відкрити…"]
    assert toolbar.current_project == a
    with qtbot.waitSignal(toolbar.project_selected) as blocker:
        toolbar.project_combo.activated.emit(1)
    assert blocker.args == [b]
    with qtbot.waitSignal(toolbar.open_project_requested, timeout=1000):
        toolbar.project_combo.activated.emit(2)


def test_connection_menu_never_auto_selects(qtbot, toolbar: ToolbarView) -> None:
    toolbar.set_ports([STLINK, PL2303, FT232], {FT232.stable_id: "інший пристрій"})
    assert toolbar.current_port_id is None
    assert toolbar.connection_button.text() == NO_DEVICE_TEXT
    assert menu_texts(toolbar) == [
        "Програматори",
        "STLink V2",
        "USB-TTL адаптери",
        "PL2303 · usbserial-120",
        "FT232 «інший пристрій» · usbserial-A50285BI",
    ]
    headers = [a for a in toolbar.port_menu.actions() if a.text() in menu_texts(toolbar)[::2]]
    assert all(not a.isEnabled() for a in headers[:1])  # group titles cannot be picked

    with qtbot.waitSignal(toolbar.port_selected) as blocker:
        device_actions(toolbar)[0].trigger()
    assert blocker.args == [STLINK.stable_id]
    assert toolbar.connection_button.text() == "ST-Link підключено"
    assert "Від'єднати" in menu_texts(toolbar)


def test_selected_port_survives_refresh_until_unplugged(toolbar: ToolbarView) -> None:
    toolbar.set_ports([STLINK, PL2303])
    toolbar.set_current_port(PL2303.stable_id)
    assert toolbar.connection_button.text() == "PL2303 підключено"
    toolbar.set_ports([PL2303, FT232])
    assert toolbar.current_port_id == PL2303.stable_id
    toolbar.set_ports([FT232])
    assert toolbar.current_port_id is None


def test_probe_serial_only_when_several_probes(toolbar: ToolbarView) -> None:
    toolbar.set_ports([STLINK, STLINK_2])
    assert menu_texts(toolbar) == ["Програматори", "STLink V2 · 303032", "STLink V2 · AAAAAA"]


def test_empty_port_menu_and_refresh_request(qtbot, toolbar: ToolbarView) -> None:
    assert menu_texts(toolbar) == ["Пристроїв не знайдено"]
    with qtbot.waitSignal(toolbar.ports_refresh_requested, timeout=1000):
        toolbar.port_menu.aboutToShow.emit()


def test_busy_disables_commands(toolbar: ToolbarView) -> None:
    toolbar.set_busy(True)
    assert not toolbar.verify_button.isEnabled()
    assert not toolbar.upload_button.isEnabled()
    assert toolbar.connection_button.isEnabled()
    toolbar.set_busy(False)
    assert toolbar.upload_button.isEnabled()


def test_running_command_button_becomes_stop(qtbot, toolbar: ToolbarView) -> None:
    toolbar.set_busy(True, "upload")
    toolbar.set_busy(True, "upload")  # a new stage of the same operation
    assert toolbar.upload_button.text() == "Зупинити"
    assert toolbar.upload_button.isEnabled()
    assert not toolbar.verify_button.isEnabled()  # the other command stays blocked
    assert toolbar.verify_button.text() == "Зібрати"
    with (
        qtbot.assertNotEmitted(toolbar.upload_requested),
        qtbot.waitSignal(toolbar.stop_requested, timeout=1000),
    ):
        toolbar.upload_button.click()

    toolbar.set_busy(False)
    assert toolbar.running is None
    assert toolbar.upload_button.text() == "Зібрати й прошити"
    with qtbot.waitSignal(toolbar.upload_requested, timeout=1000):
        toolbar.upload_button.click()


def test_stop_button_keeps_its_width(qtbot, toolbar: ToolbarView) -> None:
    toolbar.show()
    qtbot.waitExposed(toolbar)
    widths = (toolbar.verify_button.width(), toolbar.upload_button.width())
    toolbar.set_busy(True, "upload")
    qtbot.wait(50)
    assert (toolbar.verify_button.width(), toolbar.upload_button.width()) == widths
    toolbar.set_busy(True, "verify")
    qtbot.wait(50)
    assert (toolbar.verify_button.width(), toolbar.upload_button.width()) == widths
    assert toolbar.verify_button.text() == "Зупинити"
    hint = toolbar.verify_button.sizeHint().width()
    assert toolbar.verify_button.width() >= hint  # the label is not clipped
    assert toolbar.upload_button.text() == "Зібрати й прошити"


def test_connection_button_invites_to_choose(toolbar: ToolbarView) -> None:
    assert NO_DEVICE_TEXT == "Вибрати пристрій"  # a call to action, not a bare statement
    assert toolbar.connection_button.text() == NO_DEVICE_TEXT
    assert toolbar.connection_button.toolTip()
    assert toolbar.target_combo.property("readOnly") is True
    assert "визначається чипом" in toolbar.target_combo.toolTip()


def test_uart_port_list_without_adapters_says_so(qtbot) -> None:
    from nightexpress.views.panels.uart_panel import NO_PORT_TEXT, NO_PORTS_FOUND_TEXT, UartPanel

    panel = UartPanel()
    qtbot.addWidget(panel)
    combo = panel.port_combo
    panel.set_ports([], None)
    # The box prompts; the list shows one unselectable note instead of repeating the prompt.
    assert combo.currentText() == NO_PORT_TEXT and panel.current_port_id is None
    assert combo.view().isRowHidden(0) and combo.itemText(1) == NO_PORTS_FOUND_TEXT
    assert not combo.model().item(1).isEnabled()

    panel.set_ports([PL2303], PL2303.stable_id)
    assert combo.count() == 2 and not combo.view().isRowHidden(0)
    assert panel.current_port_id == PL2303.stable_id
