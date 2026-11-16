from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from nightexpress.models.entities import Diagnostic, DiagnosticLevel, RecentProject
from nightexpress.models.entities.font import FontArea
from nightexpress.models.state import Screen
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.views.dialogs.shortcuts_dialog import ShortcutsDialog, key_caps
from nightexpress.views.editor.monaco_editor import EditorAction
from nightexpress.views.main_window import (
    BUILD_TAB,
    FLASH_TAB,
    PROBLEMS_TAB,
    TERMINAL_TAB,
    UART_TAB,
    MainWindow,
    native_shortcut,
)
from nightexpress.views.toast import MAX_TOASTS, Toast, ToastKind

pytestmark = pytest.mark.ui

PROJECT = Path("/work/blink")


@pytest.fixture
def window(dispose) -> MainWindow:
    w = MainWindow("NightExpress", "0.1.0")
    dispose(w)
    return w


@pytest.mark.parametrize(
    ("action", "signal"),
    [
        ("new", "new_project_requested"),
        ("open", "open_project_requested"),
        ("save", "save_requested"),
        ("save_all", "save_all_requested"),
        ("close_project", "close_project_requested"),
        ("verify", "verify_requested"),
        ("upload", "upload_requested"),
        ("serial_monitor", "serial_monitor_requested"),
        ("clean", "clean_requested"),
        ("new_file", "new_file_requested"),
        ("quick_open", "quick_open_requested"),
        ("home", "home_requested"),
        ("reveal", "reveal_project_requested"),
        ("check_tools", "tools_dialog_requested"),
        ("settings", "settings_requested"),
        ("about", "about_requested"),
    ],
)
def test_menu_actions_emit_intents(qtbot, window: MainWindow, action: str, signal: str) -> None:
    window.set_project("blink", PROJECT)
    with qtbot.waitSignal(getattr(window, signal), timeout=1000):
        window.actions_by_name[action].trigger()


def test_help_topics(qtbot, window: MainWindow) -> None:
    with qtbot.waitSignal(window.help_requested) as blocker:
        window.actions_by_name["help_stlink"].trigger()
    assert blocker.args == ["stlink"]


def test_shortcut_reaching_qt_and_monaco_acts_once(window: MainWindow) -> None:
    received: list[str] = []
    window.upload_requested.connect(lambda: received.append("upload"))
    window.actions_by_name["upload"].trigger()  # Qt shortcut
    window.editor_tabs.command_requested.emit("upload")  # same key seen by Monaco
    assert received == ["upload"]


def test_toolbar_buttons_are_routed(qtbot, window: MainWindow) -> None:
    with qtbot.waitSignal(window.verify_requested, timeout=1000):
        window.toolbar.verify_button.click()


def test_project_dependent_actions(window: MainWindow) -> None:
    window.set_project(None, None)
    assert not window.actions_by_name["upload"].isEnabled()
    assert window.windowTitle() == "NightExpress"
    window.set_project("blink", PROJECT)
    assert window.actions_by_name["upload"].isEnabled()
    assert window.windowTitle() == "blink — NightExpress"


def test_recent_projects_everywhere(qtbot, window: MainWindow) -> None:
    window.set_recent_projects([])
    assert not window.recent_menu.isEnabled()
    projects = [
        RecentProject(PROJECT, "blink", "STM32F103C6T6A"),
        RecentProject(Path("/work/other"), "other"),
    ]
    window.set_recent_projects(projects)
    actions = window.recent_menu.actions()
    assert len(actions) == 2
    with qtbot.waitSignal(window.open_recent_requested) as blocker:
        actions[1].trigger()
    assert blocker.args == [Path("/work/other")]
    assert [c.title.text() for c in window.welcome.home.recent.cards] == ["blink", "other"]
    assert window.toolbar.project_combo.itemText(0) == "blink"


def test_screens_switch_menus_and_status_bar(window: MainWindow) -> None:
    window.show()
    assert window.current_screen is Screen.WELCOME

    def menus() -> list[str]:
        return [a.text() for a in window.menu_bar.actions() if a.isVisible()]

    # Editing, panels and builds have nothing to act on here.
    assert menus() == ["Файл", "Довідка"]
    assert not window.build_menu.menuAction().isVisible()
    assert window.version_text == "NightExpress 0.1.0" and window._version_label.isVisible()
    assert not window.problems_status.isVisible()

    window.set_screen(Screen.EDITOR)
    assert menus() == ["Файл", "Правка", "Перегляд", "Збірка", "Налагодження", "Довідка"]
    assert window.build_menu.menuAction().isVisible()
    assert window.debug_menu.menuAction().isVisible()
    assert not window._version_label.isVisible()
    assert window.problems_status.isVisible() and window.connection_status.isVisible()


def test_board_fills_toolbar_and_target_panel(window: MainWindow) -> None:
    catalog = BoardCatalog.load()
    board = catalog.boards[0]
    window.toolbar.set_boards(catalog.boards)
    window.set_board(board, catalog.chip_for(board))
    assert window.toolbar.current_board_id == board.id
    assert window.toolbar.target_combo.currentText() == "thumbv7m-none-eabi"
    target = window.target_view
    assert target.chip_title.text() == "STM32F103C6T6A"
    assert (target.core.text(), target.flash.text(), target.ram.text()) == (
        "ARM Cortex-M3",
        "32 KB",
        "10 KB",
    )
    window.set_board(None, None)
    assert target.chip_title.text() == "—"


def test_connection_and_problem_counter(window: MainWindow) -> None:
    window.set_connection("ST-Link")
    assert window.connection_status.text() == "ST-Link"
    assert window.connection_dot.property("connected") is True
    assert window.target_view.probe.text() == "ST-Link"
    window.set_connection(None)
    assert window.target_view.probe.text() == "не вибрано"

    window.problems_panel.counts_changed.emit(2, 1)
    assert window.problems_status.text() == "2 помилки, 1 попередження"
    assert window.bottom_tabs.tabText(PROBLEMS_TAB) == "Проблеми (3)"
    window.problems_panel.counts_changed.emit(0, 0)
    assert window.problems_status.text() == "0 помилок"
    assert window.bottom_tabs.tabText(PROBLEMS_TAB) == "Проблеми"


def test_bottom_tabs_and_panel_toggles(window: MainWindow) -> None:
    assert [window.bottom_tabs.tabText(i) for i in range(6)] == [
        "Проблеми",
        "Збірка",
        "Прошивання",
        "UART",
        "RTT",
        "Термінал",
    ]
    window.show()
    window.set_screen(Screen.EDITOR)
    window.actions_by_name["toggle_target"].trigger()
    assert not window.target_panel.isVisible()
    window.actions_by_name["toggle_bottom"].trigger()
    assert not window.bottom_panel.isVisible()
    window.show_problems()  # showing a tab brings the bottom panel back
    assert window.bottom_panel.isVisible()
    assert window.bottom_tabs.currentIndex() == PROBLEMS_TAB


def test_tools_status(window: MainWindow) -> None:
    window.set_tools_status("Rust: готовий", ok=True)
    assert window.tools_status.text() == "Rust: готовий"
    window.set_tools_status("Rust: не знайдено cargo", ok=False)
    assert window.tools_status.property("state") == "error"


def test_tools_status_opens_tools_dialog(qtbot, window: MainWindow) -> None:
    with qtbot.waitSignal(window.tools_dialog_requested, timeout=1000):
        window.tools_status.clicked.emit()


def test_activity_message(qtbot, window: MainWindow) -> None:
    window.show()
    window.set_screen(Screen.EDITOR)
    window.show_message("Збірка…", 0)
    assert window.activity_status.text() == "Збірка…"
    assert window.connection_status.isVisible()  # QStatusBar.showMessage() would hide it
    window.show_message("Готово", 50)
    qtbot.waitUntil(lambda: window.activity_status.text() == "", timeout=2000)


def test_activity_shows_spinner_and_progress(window: MainWindow) -> None:
    window.show_activity("Збираю…")
    assert window.activity_status.text() == "Збираю…"
    assert window.activity_spinner.is_spinning
    assert window.activity_bar.is_active and window.activity_bar.percent is None

    window.show_activity("Записую", 45)
    assert window.activity_status.text() == "Записую 45 %"
    assert window.activity_bar.percent == 45


def test_result_is_coloured_and_opens_its_tab(qtbot, window: MainWindow) -> None:
    window.show()
    window.set_screen(Screen.EDITOR)
    window.show_activity("Збираю…")
    window.show_bottom_tab(UART_TAB)
    window.show_result("Збірка не вдалася: 2 помилки", False, PROBLEMS_TAB)
    assert not window.activity_spinner.is_spinning
    assert not window.activity_bar.is_active
    assert window.activity_status.property("state") == "error"
    qtbot.mouseClick(window.activity_status, Qt.MouseButton.LeftButton)
    assert window.bottom_tabs.currentIndex() == PROBLEMS_TAB

    window.show_result("Зібрано за 9,6 с · flash 3 %", True)
    assert window.activity_status.property("state") == "ok"


def test_busy_end_clears_stage_text_but_keeps_result(window: MainWindow) -> None:
    window.set_busy(True, "verify")
    assert window.toolbar.running == "verify"
    window.show_activity("Збираю…")
    window.set_busy(False)
    assert window.activity_status.text() == ""
    assert not window.activity_spinner.is_spinning
    assert window.toolbar.running is None

    window.set_busy(True, "upload")
    window.show_result("Прошито за 1,5 с", True)
    window.set_busy(False)
    assert window.activity_status.text() == "Прошито за 1,5 с"


def test_toolbar_stop_button_requests_stop(qtbot, window: MainWindow) -> None:
    window.set_busy(True, "verify")
    with qtbot.waitSignal(window.stop_requested, timeout=1000):
        window.toolbar.verify_button.click()


def test_tab_badge_only_on_a_tab_not_in_view(window: MainWindow) -> None:
    window.show()
    window.set_screen(Screen.EDITOR)
    window.show_bottom_tab(BUILD_TAB)
    window.notify_tab(BUILD_TAB)
    assert window.tab_badge(BUILD_TAB) is None  # already looking at it

    window.notify_tab(FLASH_TAB, error=True)
    assert window.tab_badge(FLASH_TAB) is True
    window.bottom_tabs.setCurrentIndex(FLASH_TAB)
    assert window.tab_badge(FLASH_TAB) is None


def test_errors_are_toasts_not_modal_dialogs(qtbot, window: MainWindow) -> None:
    window.show()
    window.show_error("Rust не знайдено", "Не знайдено cargo.")  # must not block
    [toast] = window.toasts.toasts
    assert toast.kind is ToastKind.ERROR
    assert toast.isVisible()
    assert toast.geometry().right() < window.width()
    assert toast.geometry().bottom() < window.height() - window.statusBar().height()

    window.show_error("Rust не знайдено", "Не знайдено cargo.")  # the same again: no pile-up
    assert len(window.toasts.toasts) == 1
    window.toasts.toasts[0].close_button.click()
    assert window.toasts.toasts == ()


def test_toast_text_is_equally_far_from_its_top_and_bottom(qtbot, window: MainWindow) -> None:
    window.show()
    long_text = "TX адаптера → PA10, RX адаптера → PA9, GND → GND, рівень 3.3 V. " * 3
    plain = window.notify("Як підключити USB-TTL", long_text, ToastKind.WARNING)
    acting = window.notify("Пристрій не вибрано", long_text, ToastKind.WARNING, "Вибрати", print)
    short = window.notify("Збірку завершено", "", ToastKind.SUCCESS)
    qtbot.wait(20)

    def edges(toast: Toast, widget: QWidget) -> tuple[int, int]:
        top = widget.mapTo(toast, widget.rect().topLeft()).y()
        return top, toast.height() - top - widget.height()

    for toast in (plain, acting, short):
        # Exactly as tall as its wrapped text: no empty band inside.
        assert toast.height() == toast.heightForWidth(toast.width())
        assert toast.title.height() == toast.title.fontMetrics().height()
    for toast in (plain, acting):
        assert toast.text.height() == toast.text.heightForWidth(toast.text.width())
    assert edges(plain, plain.title)[0] == edges(plain, plain.text)[1]
    assert edges(acting, acting.title)[0] == edges(acting, acting.action_button)[1]
    above, below = edges(short, short.title)
    assert above == below
    # The close button is as far from the top edge as from the right one.
    above = edges(plain, plain.close_button)[0]
    assert above == plain.width() - plain.close_button.geometry().right() - 1


def test_toast_action_and_limits(window: MainWindow) -> None:
    window.show()
    clicked: list[bool] = []
    toast = window.notify(
        "Пристрій не вибрано", "", ToastKind.WARNING, "Вибрати", lambda: clicked.append(True)
    )
    assert toast.action_button is not None
    toast.action_button.click()
    assert clicked == [True]
    assert window.toasts.toasts == ()  # acting on a toast closes it

    for number in range(MAX_TOASTS + 2):
        window.notify(f"Повідомлення {number}")
    titles = [t.title_text for t in window.toasts.toasts]
    assert titles == [f"Повідомлення {n}" for n in range(2, MAX_TOASTS + 2)]
    tops = [t.geometry().top() for t in window.toasts.toasts]
    assert tops == sorted(tops)  # stacked without overlap, the newest at the bottom

    window.set_busy(True, "verify")  # a new attempt makes old messages stale
    assert window.toasts.toasts == ()


def test_info_toast_hides_itself_but_error_stays(qtbot, window: MainWindow, monkeypatch) -> None:
    monkeypatch.setattr("nightexpress.views.toast.AUTO_HIDE_MS", 30)
    window.show()
    window.notify("Знайдено ST-Link")
    window.show_error("Помилка", "текст")
    qtbot.waitUntil(lambda: len(window.toasts.toasts) == 1, timeout=2000)
    assert window.toasts.toasts[0].kind is ToastKind.ERROR


def test_status_bar_device_opens_the_device_menu(qtbot, window: MainWindow) -> None:
    window.show()
    window.set_screen(Screen.EDITOR)
    opened: list[bool] = []
    window.toolbar.show_port_menu = lambda: opened.append(True)
    qtbot.mouseClick(window.connection_status, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: opened == [True], timeout=1000)


def test_tab_actions_need_a_project_and_reach_the_tabs(qtbot, window: MainWindow) -> None:
    for key in ("quick_open", "close_tab", "next_tab", "previous_tab"):
        assert not window.actions_by_name[key].isEnabled()
    window.set_project("blink", PROJECT)
    assert window.actions_by_name["close_tab"].isEnabled()
    assert window.actions_by_name["close_tab"].shortcut().toString() == "Ctrl+W"
    assert window.actions_by_name["quick_open"].shortcut().toString() == "Ctrl+P"

    window.set_screen(Screen.EDITOR)
    main, cargo = PROJECT / "src" / "main.rs", PROJECT / "Cargo.toml"
    window.editor_tabs.open_file(main, "a")
    window.editor_tabs.open_file(cargo, "b")
    window._command("next_tab")
    assert window.editor_tabs.current_path == main
    window._command("previous_tab")
    assert window.editor_tabs.current_path == cargo
    with qtbot.waitSignal(window.editor_tabs.tab_close_requested) as blocker:
        window.actions_by_name["close_tab"].trigger()
    assert blocker.args == [cargo]


def test_project_panel_menu_and_copy_feedback(window: MainWindow) -> None:
    texts = [a.text() for a in window.project_menu.actions()]
    assert texts == [
        "Показати службові файли",
        "Показати теку збірки (target)",
        "Згорнути все",
    ]
    assert window.show_service_action.isChecked()
    assert not window.show_build_dir_action.isChecked()
    window.file_tree.path_copied.emit("src/main.rs")
    assert window.activity_status.text() == "Скопійовано: src/main.rs"


def test_reveal_in_tree_shows_the_project_panel(window: MainWindow) -> None:
    window.actions_by_name["toggle_project"].trigger()
    assert window.project_panel.isHidden()
    window.reveal_in_tree(PROJECT / "src" / "main.rs")
    assert not window.project_panel.isHidden()
    assert window.actions_by_name["toggle_project"].isChecked()


def test_log_toolbar_follows_the_bottom_tab(window: MainWindow) -> None:
    toolbar = window.log_toolbar
    assert toolbar.target is window.output_panel  # the build tab is shown first
    window.show_bottom_tab(FLASH_TAB)
    assert toolbar.target is window.flash_panel
    window.show_uart()
    assert toolbar.target is window.uart_panel.output
    window.show_problems()  # only "copy all" makes sense for the list of problems
    assert toolbar.target is window.problems_panel and toolbar.tools == {"copy"}
    assert toolbar.clear_button.isHidden() and toolbar.search_button.isHidden()
    window.show_bottom_tab(TERMINAL_TAB)
    assert toolbar.target is window.terminal_panel and toolbar.wrap_button.isHidden()

    window.show_output()
    window.output_panel.append_line("old build")
    toolbar.clear_button.click()
    assert window.output_panel.plain_text() == ""

    window.show_uart()
    window.uart_panel.append_data(b"hello\n")
    cleared: list[bool] = []
    window.uart_panel.clear_requested.connect(lambda: cleared.append(True))
    toolbar.clear_button.click()
    assert cleared == [True] and window.uart_panel.text() == ""


def test_bottom_panel_shortcut_and_maximize(window: MainWindow) -> None:
    toggle = window.actions_by_name["toggle_bottom"]
    assert toggle.shortcut().toString() == "Ctrl+J"
    window._command("toggle_bottom")
    assert window.bottom_panel.isHidden() and not toggle.isChecked()
    window._last_command = ("", 0.0)
    window._command("toggle_bottom")
    assert not window.bottom_panel.isHidden()

    window.set_bottom_maximized(True)
    assert window.bottom_maximized and window.top_splitter.isHidden()
    toggle.trigger()  # hiding a maximized panel brings the editor back
    assert window.bottom_panel.isHidden() and not window.top_splitter.isHidden()


def test_unfinished_features_say_so(qtbot, window: MainWindow) -> None:
    assert window.actions_by_name["debug_start"].text() == "Почати налагодження (згодом)"
    external = window.actions_by_name["external_terminal"]
    assert not external.isEnabled()
    window.set_project("blink", PROJECT)
    with qtbot.waitSignal(window.external_terminal_requested):
        external.trigger()


def test_examples_menu(qtbot, window: MainWindow) -> None:
    menu = window.examples_menu
    assert menu.title() == "Приклади" and menu.isEnabled()
    assert [a.text() for a in menu.actions()] == ["Для цієї плати ще немає прикладів"]
    assert not menu.actions()[0].isEnabled()

    catalog = BoardCatalog.load()
    window.set_examples(catalog.examples, None)
    examples = ["Blink…", "Serial Hello…", "Button…", "RTT Hello…"]
    assert [a.text() for a in menu.actions()] == examples
    assert "UART" in menu.actions()[1].toolTip()

    board = catalog.get_board("stm32f103c6-minimal")
    window.set_examples(catalog.examples_for(board.id), board)
    header, separator, *items = menu.actions()
    assert header.text() == f"Для плати: {board.display_name}" and not header.isEnabled()
    assert separator.isSeparator() and len(items) == 4
    with qtbot.waitSignal(window.example_requested, timeout=1000) as blocker:
        items[2].trigger()
    assert blocker.args == ["button"]


def test_log_toolbar_sits_on_the_tab_row(qtbot, window: MainWindow) -> None:
    window.resize(1100, 800)
    window.show()
    window.set_screen(Screen.EDITOR)
    tabs, toolbar = window.bottom_tabs.tabBar(), window.log_toolbar
    assert window.bottom_tabs.cornerWidget(Qt.Corner.TopRightCorner) is None
    qtbot.waitUntil(lambda: toolbar.geometry().right() == window.bottom_tabs.width() - 1)
    # Same row as the tab titles: not pushed down the way a macOS corner widget is.
    assert toolbar.y() == 0 and toolbar.height() == tabs.height()
    assert tabs.geometry().right() < toolbar.x()

    closed_x = toolbar.x()
    window.bottom_tabs.setCurrentIndex(1)
    window.log_toolbar.open_search()  # wider toolbar: the tabs make room instead of hiding
    qtbot.waitUntil(lambda: tabs.geometry().right() < toolbar.x() < closed_x)
    assert toolbar.geometry().right() == window.bottom_tabs.width() - 1


def test_status_bar_cursor_position(qtbot, window: MainWindow) -> None:
    window.show()
    window.set_screen(Screen.EDITOR)
    assert not window.cursor_status.isVisible()  # no file open yet
    window.editor_tabs.open_file(PROJECT / "src/main.rs", "fn main() {}\n")
    window.set_cursor_position(17, 9)
    assert window.cursor_status.isVisible()
    assert window.cursor_status.text() == "Ряд 17, Стовп 9"

    triggered: list[EditorAction] = []
    window.editor_tabs.trigger_action = triggered.append  # type: ignore[method-assign]
    window.cursor_status.clicked.emit()
    assert triggered == [EditorAction.GO_TO_LINE]

    readme = PROJECT / "README.md"
    window.editor_tabs.open_file(readme, "# README")
    window.editor_tabs.markdown_toggle.click()
    assert not window.cursor_status.isVisible()
    window.editor_tabs.markdown_toggle.click()
    assert window.cursor_status.isVisible()
    window.editor_tabs.close_file(readme)

    window.set_screen(Screen.WELCOME)
    assert not window.cursor_status.isVisible()
    window.set_screen(Screen.EDITOR)
    assert window.cursor_status.isVisible()
    window.editor_tabs.close_file(PROJECT / "src/main.rs")  # the last file: nothing to point at
    assert not window.cursor_status.isVisible()


def test_status_bar_problems_open_the_tab(qtbot, window: MainWindow) -> None:
    window.show()
    window.set_screen(Screen.EDITOR)
    window.show_bottom_tab(BUILD_TAB)
    window.problems_status.clicked.emit()
    assert window.bottom_tabs.currentIndex() == PROBLEMS_TAB


def test_f8_walks_through_problems(qtbot, window: MainWindow) -> None:
    window.problems_panel.set_project_dir(PROJECT)
    first = Diagnostic(DiagnosticLevel.ERROR, "mismatched types", PROJECT / "src/main.rs", 17, 9)
    second = Diagnostic(DiagnosticLevel.WARNING, "unused", PROJECT / "src/main.rs", 20, 5)
    other = Diagnostic(DiagnosticLevel.ERROR, "bad key", PROJECT / "Cargo.toml", 3, 1)
    nowhere = Diagnostic(DiagnosticLevel.ERROR, "linking failed")  # no location: skipped
    window.problems_panel.set_diagnostics((first, second, other, nowhere))

    seen: list[Diagnostic] = []
    window.problems_panel.diagnostic_activated.connect(seen.append)
    for _ in range(4):
        window.actions_by_name["next_problem"].trigger()
        qtbot.wait(30)  # F8 pressed in series: only the echo of one key press is dropped
    window.actions_by_name["previous_problem"].trigger()
    assert len(seen) == 5 and seen[3] == seen[0]  # three locations, then it wraps around
    assert set(seen[:3]) == {first, second, other} and seen[4] == seen[2]

    window.problems_panel.set_diagnostics(())
    messages: list[str] = []
    window.show_message = lambda text, timeout_ms=5000: messages.append(text)  # type: ignore[method-assign]
    qtbot.wait(350)
    window.actions_by_name["next_problem"].trigger()
    assert messages == ["Проблем немає"]


def test_focus_file_tree_shows_the_project_panel(qtbot, window: MainWindow) -> None:
    window.show()
    window.set_screen(Screen.EDITOR)
    window.actions_by_name["toggle_project"].trigger()
    assert not window.project_panel.isVisible()
    window.focus_file_tree()
    assert window.project_panel.isVisible()
    assert window.actions_by_name["toggle_project"].isChecked()


def test_shortcuts_page_lists_the_menus(qtbot, window: MainWindow) -> None:
    window.set_screen(Screen.EDITOR)
    groups = dict(window.shortcut_groups())
    assert ("Зібрати", native_shortcut("Ctrl+B")) in groups["Збірка"]
    assert ("Наступна проблема", "F8") in groups["Перегляд"]
    assert ("Перейти до дерева файлів", native_shortcut("Ctrl+1")) in groups["Перегляд"]
    # Monaco's editing keys are listed under "Editor"; the menu adds only rustfmt.
    assert groups["Правка"] == [("Форматувати документ", native_shortcut("Shift+Alt+F"))]
    assert groups["Редактор"] and groups["Термінал"]

    dialog = ShortcutsDialog(window.shortcut_groups(), window)
    qtbot.addWidget(dialog)
    rows = dialog.rows()
    assert rows["Файл"]["Перейти до файлу"] == native_shortcut("Ctrl+P")
    assert list(rows)[-2:] == ["Редактор", "Термінал"]

    # Every key is a cap of its own; the search keeps the matching commands and their groups.
    assert key_caps("⇧⌘S") == [["⇧", "⌘", "S"]]
    assert key_caps("Ctrl+Shift+S") == [["Ctrl", "Shift", "S"]]
    assert key_caps("Ctrl++") == [["Ctrl", "+"]]
    assert key_caps("⌥↑ / ⌥↓") == [["⌥", "↑"], ["⌥", "↓"]]
    dialog.search.setText("дерева")
    assert dialog.rows() == {"Перегляд": {"Перейти до дерева файлів": native_shortcut("Ctrl+1")}}
    dialog.search.setText("такої команди немає")
    assert dialog.rows() == {} and not dialog.empty.isHidden()


def test_zoom_shortcuts_emit_font_steps(qtbot, window: MainWindow) -> None:
    steps: list[tuple[FontArea, int]] = []
    window.font_size_step_requested.connect(lambda area, step: steps.append((area, step)))
    for name in ("zoom_in", "zoom_out", "zoom_reset"):
        window.actions_by_name[name].trigger()
    # Nothing in particular has the focus: the keys are about the code editor.
    assert steps == [(FontArea.EDITOR, 1), (FontArea.EDITOR, -1), (FontArea.EDITOR, 0)]
    shortcuts = [s.toString() for s in window.actions_by_name["zoom_in"].shortcuts()]
    assert shortcuts == ["Ctrl+=", "Ctrl++"]


def test_menus_show_their_shortcuts(window: MainWindow) -> None:
    # Qt hides them on macOS for menus outside the system menu bar, and ours is in the window.
    for action in window.actions_by_name.values():
        assert action.isShortcutVisibleInContextMenu(), action.text()


def test_layout_state_round_trip(qtbot, dispose) -> None:
    first = MainWindow("NightExpress", "0.1.0")
    dispose(first)
    first.resize(1400, 900)
    first.show()
    first.set_screen(Screen.EDITOR)
    first.top_splitter.setSizes([250, 800, 300])
    first.actions_by_name["toggle_target"].trigger()  # hide "Target"
    first.show_bottom_tab(UART_TAB)
    state = first.layout_state()
    assert state["panels"] == {
        "toggle_project": True,
        "toggle_target": False,
        "toggle_bottom": True,
    }
    assert state["bottom_tab"] == "uart"  # by name: indexes move when a tab is added

    second = MainWindow("NightExpress", "0.1.0")
    dispose(second)
    second.resize(1400, 900)
    second.show()
    second.set_screen(Screen.EDITOR)
    second.restore_layout(state)
    assert not second.target_panel.isVisible()
    assert not second.actions_by_name["toggle_target"].isChecked()
    assert second.bottom_tabs.currentIndex() == UART_TAB
    assert second.top_splitter.sizes()[0] == first.top_splitter.sizes()[0]
    # Layouts saved before the RTT tab kept the index: 4 was the terminal then.
    second.restore_layout({"bottom_tab": 4})
    assert second.bottom_tabs.currentIndex() == TERMINAL_TAB


def test_layout_saved_with_the_bottom_panel_maximized_keeps_the_editor_height(
    qtbot, dispose
) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    window.resize(1400, 900)
    window.show()
    window.set_screen(Screen.EDITOR)
    before = window.main_splitter.sizes()
    window.set_bottom_maximized(True)
    qtbot.waitUntil(lambda: window.main_splitter.sizes()[0] == 0)
    assert window.layout_state()["main"] == before
    window.set_bottom_maximized(False)
    assert window.layout_state()["main"] == window.main_splitter.sizes()


def test_sizes_saved_for_an_older_layout_are_dropped(window: MainWindow) -> None:
    top, main = window.top_splitter.sizes(), window.main_splitter.sizes()
    window.restore_layout({"top": [320, 830, 370], "main": [560, 320], "bottom_tab": UART_TAB})
    assert (window.top_splitter.sizes(), window.main_splitter.sizes()) == (top, main)
    assert window.bottom_tabs.currentIndex() == UART_TAB  # the rest of the state still applies


def test_broken_layout_state_keeps_defaults(window: MainWindow) -> None:
    sizes = window.main_splitter.sizes()
    window.restore_layout({"top": "wide", "main": [1, 2, 3], "panels": [1], "bottom_tab": 99})
    assert window.main_splitter.sizes() == sizes
    assert window.bottom_tabs.currentIndex() == BUILD_TAB
    assert all(window.actions_by_name[k].isChecked() for k in MainWindow.PANEL_KEYS)


def test_every_text_area_has_a_font_size_of_its_own(window: MainWindow) -> None:
    logs = (window.output_panel, window.flash_panel)
    uart = window.uart_panel.output
    before = uart.font().pointSize()
    window.set_font_size(FontArea.LOGS, 18)
    # Qt's monospace looks a point larger than the editor's, so 18 in the settings is 17 here.
    assert [widget.font().pointSize() for widget in logs] == [17, 17]
    assert uart.font().pointSize() == before  # the UART monitor is another area
    window.set_font_size(FontArea.UART, 12)
    assert uart.font().pointSize() == 11
    assert [widget.font().pointSize() for widget in logs] == [17, 17]


def test_font_size_keys_act_on_the_area_with_the_focus(qtbot, window: MainWindow) -> None:
    window.set_screen(Screen.EDITOR)
    window.show()
    window.activateWindow()  # a window that is not active has no focus widget at all
    qtbot.waitUntil(window.isActiveWindow, timeout=2000)
    assert window.focused_font_area() is FontArea.EDITOR
    for tab, widget, area in (
        (UART_TAB, window.uart_panel.output, FontArea.UART),
        (BUILD_TAB, window.output_panel, FontArea.LOGS),
        (FLASH_TAB, window.flash_panel, FontArea.LOGS),
        (TERMINAL_TAB, window.terminal_panel, FontArea.TERMINAL),
    ):
        window.show_bottom_tab(tab)
        widget.setFocus()
        qtbot.waitUntil(lambda area=area: window.focused_font_area() is area, timeout=2000)
    window.toolbar.project_combo.setFocus()  # anywhere else: the keys are about the code
    qtbot.waitUntil(lambda: window.focused_font_area() is FontArea.EDITOR, timeout=2000)


def test_only_the_echo_of_a_repeatable_command_is_dropped(qtbot, window: MainWindow) -> None:
    steps: list[int] = []
    saves: list[bool] = []
    window.font_size_step_requested.connect(lambda _area, step: steps.append(step))
    window.save_requested.connect(lambda: saves.append(True))
    window._command("zoom_in")
    window._command("zoom_in")  # the same key press arriving from Monaco as well
    qtbot.wait(40)
    window._command("zoom_in")  # the next press of a quick series
    assert steps == [1, 1]
    window._command("save")
    qtbot.wait(40)
    window._command("save")
    assert saves == [True]


def test_about_dialog_describes_the_app_and_its_licenses(
    qtbot, window: MainWindow, monkeypatch
) -> None:
    from pathlib import Path

    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QLabel

    from nightexpress.views.dialogs.about_dialog import COMPONENTS, AboutDialog

    window.actions_by_name["about"].trigger()
    dialog = window.findChild(AboutDialog)
    assert dialog is not None and dialog.isVisible()
    assert dialog.version.text() == f"Версія {window.version_text.rsplit(' ', 1)[1]}"
    assert "Rust на STM32" in dialog.description.text() and "MIT" in dialog.copyright.text()
    assert "LGPL-3.0" in dialog.qt_note.text() and "STMicroelectronics" in dialog.trademarks.text()
    assert "0BSD" in dialog.templates_note.text()
    # Every component is a row: name and holder on the left, the licenses as caps.
    notices = (Path(__file__).parents[2] / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    expected = [component for _section, components in COMPONENTS for component in components]
    assert [(r.name, r.url, r.licenses, r.holder) for r in dialog.rows] == expected
    names = " ".join(row.name for row in dialog.rows).lower()
    for component in ("PySide6", "Monaco Editor", "xterm.js", "Lucide"):
        assert component.lower() in names and component in notices
    # Every runtime dependency of the package is credited in both places.
    import re
    import tomllib

    pyproject = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    for requirement in pyproject["project"]["dependencies"]:
        package = re.match(r"[A-Za-z0-9_.-]+", requirement).group(0)
        assert package.lower() in names, package
        assert package.lower() in notices.lower(), package
    caps = [cap.text() for cap in dialog.rows[0].findChildren(QLabel, "keyCap")]
    assert caps == ["LGPL-3.0"]
    assert "Vadym Voloshchuk" in dialog.copyright.text()
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    qtbot.mouseClick(dialog.rows[1], Qt.MouseButton.LeftButton)
    assert opened == ["https://github.com/microsoft/monaco-editor"]
    dialog.close_button.click()
    assert not dialog.isVisible()
