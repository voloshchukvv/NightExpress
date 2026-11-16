from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from nightexpress.models.entities import (
    BuildSummary,
    ButtonInfo,
    Diagnostic,
    DiagnosticLevel,
    MemoryUsage,
    UploadMethod,
)
from nightexpress.models.qt_models import (
    DiagnosticRole,
    DiagnosticsTreeModel,
    UrlRole,
    code_url,
    help_text,
)
from nightexpress.models.state import BuildModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.views.file_tree_view import FileTreeView
from nightexpress.views.panels.log_toolbar import LogToolbar
from nightexpress.views.panels.output_panel import MAX_BLOCKS, OutputPanel
from nightexpress.views.panels.problems_panel import ProblemsPanel
from nightexpress.views.panels.target_panel import NO_BUILD_TEXT, TargetPanel
from nightexpress.views.theme import palette

pytestmark = pytest.mark.ui

PROJECT = Path("/work/blink")
ERR = Diagnostic(
    DiagnosticLevel.ERROR,
    "cannot find value",
    PROJECT / "src/main.rs",
    24,
    9,
    "E0425",
    "\x1b[91merror[E0425]\x1b[0m: cannot find value",
)
WARN = Diagnostic(DiagnosticLevel.WARNING, "unused variable", PROJECT / "src/main.rs", 20, 9)
LINK = Diagnostic(DiagnosticLevel.ERROR, "linking with `rust-lld` failed")


# --- output ---------------------------------------------------------------------------------


def test_output_strips_ansi_and_follows_model(qtbot) -> None:
    panel = OutputPanel(timestamps=False)
    qtbot.addWidget(panel)
    model = BuildModel()
    panel.bind(model)
    model.append_log("\x1b[1m\x1b[92m   Compiling\x1b[0m blink v0.1.0")
    model.append_log("done")
    assert panel.plain_text() == "   Compiling blink v0.1.0\ndone"
    model.clear_log()
    assert panel.plain_text() == ""


def test_output_colours_segments(qtbot) -> None:
    panel = OutputPanel(timestamps=False)
    qtbot.addWidget(panel)
    panel.append_line("\x1b[91merror\x1b[0m: x")
    block = panel.document().firstBlock()
    fragments = []
    it = block.begin()
    while not it.atEnd():
        fragment = it.fragment()
        fragments.append((fragment.text(), fragment.charFormat().foreground().color().name()))
        it += 1
    assert fragments[0] == ("error", "#ff8a8a")


def test_output_timestamps_and_summary(qtbot) -> None:
    panel = OutputPanel()
    qtbot.addWidget(panel)
    model = BuildModel()
    panel.bind(model)
    model.append_log("Запуск збірки проєкту «Blink»…")
    model.set_summary(BuildSummary(True, "Збірку успішно завершено (6,21 с)"))
    first, last = panel.plain_text().splitlines()
    assert re.fullmatch(r"\[\d\d:\d\d:\d\d\] Запуск збірки проєкту «Blink»…", first)
    assert last.endswith("Збірку успішно завершено (6,21 с)")
    assert "\ufffc" in last  # the inline status icon (object replacement character)
    model.clear_log()
    model.set_summary(BuildSummary(False, "Збірка не вдалася"))
    assert panel.plain_text().endswith("Збірка не вдалася")


def test_output_is_bounded(qtbot) -> None:
    panel = OutputPanel()
    qtbot.addWidget(panel)
    for i in range(MAX_BLOCKS + 5):
        panel.append_line(str(i))
    assert panel.document().blockCount() == MAX_BLOCKS


# --- problems -------------------------------------------------------------------------------


HELPED = Diagnostic(
    DiagnosticLevel.WARNING,
    "unused variable: `x`",
    PROJECT / "src/led.rs",
    3,
    9,
    "unused_variables",
    "warning: unused variable: `x`\n --> src/led.rs:3:9\n  |\n"
    "  = help: if this is intentional, prefix it with an underscore: `_x`\n",
)


def _rows(model: DiagnosticsTreeModel) -> list[tuple[str, list[list[str]]]]:
    result = []
    for g in range(model.rowCount()):
        group = model.index(g, 0)
        children = [
            [model.index(r, c, group).data() for c in range(model.columnCount())]
            for r in range(model.rowCount(group))
        ]
        result.append((group.data(), children))
    return result


def test_tree_model_groups_by_file_errors_first() -> None:
    model = DiagnosticsTreeModel(PROJECT)
    model.set_diagnostics([HELPED, WARN, ERR, LINK])
    assert _rows(model) == [
        ("src/main.rs  (2)", [
            ["cannot find value", "E0425", "24:9"],
            ["unused variable", "", "20:9"],
        ]),
        ("Проєкт загалом  (1)", [["linking with `rust-lld` failed", "", ""]]),
        ("src/led.rs  (1)", [[
            "unused variable: `x`\n"
            "порада: if this is intentional, prefix it with an underscore: `_x`",
            "unused_variables",
            "3:9",
        ]]),
    ]  # fmt: skip
    index = model.index_of(ERR)
    assert index.data(DiagnosticRole) is ERR and model.diagnostic(index.siblingAtColumn(2)) is ERR
    assert model.diagnostic(model.index(0, 0)) is None  # a file row
    assert index.data(Qt.ItemDataRole.ToolTipRole) == "error[E0425]: cannot find value"
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Повідомлення"
    assert index.siblingAtColumn(1).data(UrlRole) == (
        "https://doc.rust-lang.org/error_codes/E0425.html"
    )
    assert model.index_of(HELPED).siblingAtColumn(1).data(UrlRole) is None


def test_code_urls_and_help_text() -> None:
    assert code_url("E0308") == "https://doc.rust-lang.org/error_codes/E0308.html"
    assert code_url("clippy::needless_return") == (
        "https://rust-lang.github.io/rust-clippy/master/index.html#needless_return"
    )
    assert code_url("unused_variables") is None and code_url(None) is None
    assert help_text(HELPED) == "if this is intentional, prefix it with an underscore: `_x`"
    assert help_text(ERR) is None


def test_problems_panel_counts_and_activation(qtbot) -> None:
    panel = ProblemsPanel()
    qtbot.addWidget(panel)
    model = BuildModel()
    panel.bind(model)
    assert panel.is_empty and panel.empty_title.text() == "Проблем немає"
    with qtbot.waitSignal(panel.counts_changed) as counts:
        model.set_diagnostics([WARN, ERR, LINK])
    assert counts.args == [2, 1]
    assert not panel.is_empty
    assert panel.tree.isExpanded(panel.tree_model.index(0, 0))

    with qtbot.waitSignal(panel.diagnostic_activated) as activated:
        panel.tree.doubleClicked.emit(panel.index_of(ERR, 2))
    assert activated.args == [ERR]

    received: list[Diagnostic] = []
    panel.diagnostic_activated.connect(received.append)
    panel.tree.doubleClicked.emit(panel.index_of(LINK))  # linker error has no location
    panel.tree.doubleClicked.emit(panel.tree_model.index(0, 0))  # a file row
    assert received == []

    with qtbot.waitSignal(panel.code_help_requested) as help_request:
        panel.tree.clicked.emit(panel.index_of(ERR, 1))
    assert help_request.args == ["https://doc.rust-lang.org/error_codes/E0425.html"]

    model.set_diagnostics([])
    assert panel.is_empty


# --- log links and toolbar -------------------------------------------------------------------


def test_output_links_compiler_locations(qtbot) -> None:
    panel = OutputPanel()
    qtbot.addWidget(panel)
    panel.append_line("\x1b[94m  --> \x1b[0msrc/main.rs:17:9")
    panel.append_line("   Compiling blink v0.1.0 (/work/blink)")
    block = panel.document().firstBlock()
    hrefs = set()
    it = block.begin()
    while not it.atEnd():
        fmt = it.fragment().charFormat()
        if fmt.isAnchor():
            hrefs.add((it.fragment().text(), fmt.anchorHref()))
        it += 1
    assert hrefs == {("src/main.rs:17:9", "src/main.rs:17:9")}
    second = block.next().begin()
    assert not second.fragment().charFormat().isAnchor()

    with qtbot.waitSignal(panel.location_activated) as blocker:
        panel.activate_anchor("src/main.rs:17:9")
    assert blocker.args == ["src/main.rs", 17, 9]


def test_log_toolbar_wrap_copy_and_clear(qtbot) -> None:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QPlainTextEdit

    toolbar = LogToolbar()
    qtbot.addWidget(toolbar)
    assert not toolbar.copy_button.isEnabled()  # no log in view

    log = OutputPanel(timestamps=False)
    qtbot.addWidget(log)
    log.append_line("first")
    log.append_line("second")
    toolbar.set_target(log)
    assert toolbar.copy_button.isEnabled() and not toolbar.wrap_button.isChecked()
    toolbar.show()
    toolbar.activateWindow()
    qtbot.waitUntil(toolbar.isActiveWindow)
    qtbot.mouseClick(toolbar.wrap_button, Qt.MouseButton.LeftButton)
    assert LogToolbar.wraps(log)

    plain = QPlainTextEdit()
    qtbot.addWidget(plain)
    toolbar.set_target(plain)  # the button shows the state of the log in view
    assert toolbar.wrap_button.isChecked()
    qtbot.mouseClick(toolbar.wrap_button, Qt.MouseButton.LeftButton)
    assert not LogToolbar.wraps(plain) and LogToolbar.wraps(log)
    assert not toolbar.wrap_button.hasFocus()

    toolbar.set_target(log)
    with qtbot.waitSignal(toolbar.copied):
        toolbar.copy_button.click()
    assert QGuiApplication.clipboard().text() == "first\nsecond"
    with qtbot.waitSignal(toolbar.clear_requested):
        toolbar.clear_button.click()
    toolbar.set_target(None)
    assert not toolbar.clear_button.isEnabled()


def test_log_toolbar_search(qtbot) -> None:
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    window = QWidget()
    qtbot.addWidget(window)
    toolbar = LogToolbar(window)
    log = OutputPanel(window, timestamps=False)
    layout = QVBoxLayout(window)
    layout.addWidget(toolbar)
    layout.addWidget(log)
    for line in ("Compiling cortex-m", "compiling blink", "Finished", "error: COMPILING failed"):
        log.append_line(line)
    toolbar.set_target(log)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)

    toolbar.open_search()
    assert toolbar.search_field.isVisible() and toolbar.search_button.isChecked()
    toolbar.search_field.setText("compiling")
    assert toolbar.match_count == 3  # case-insensitive
    assert toolbar.current_match == 0 and toolbar.match_label.text() == "1 з 3"
    assert len(log.extraSelections()) == 3
    toolbar.step(1)
    toolbar.step(1)
    assert log.textCursor().blockNumber() == 3
    toolbar.step(1)
    assert toolbar.current_match == 0  # wraps around
    toolbar.step(-1)
    assert toolbar.match_label.text() == "3 з 3"

    toolbar.search_field.setText("nothing like this")
    assert toolbar.match_count == 0 and toolbar.match_label.text() == "немає"
    toolbar.search_field.setText("compiling")
    qtbot.mouseClick(toolbar.search_button, Qt.MouseButton.LeftButton)
    assert not toolbar.search_field.isVisible() and log.extraSelections() == []
    assert log.hasFocus()


# --- file tree ------------------------------------------------------------------------------


def test_file_tree_hides_build_output_and_emits_files(qtbot, tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}")
    (tmp_path / "Cargo.toml").write_text("[package]")
    (tmp_path / "target").mkdir()
    tree = FileTreeView()
    qtbot.addWidget(tree)
    tree.set_root(tmp_path)

    (tmp_path.parent / f"{tmp_path.name}-sibling").mkdir(exist_ok=True)

    def names(parent) -> set[str]:
        model = tree.model()
        return {model.data(model.index(r, 0, parent)) for r in range(model.rowCount(parent))}

    # The project folder itself is the only visible root ...
    qtbot.waitUntil(lambda: names(tree.rootIndex()) == {tmp_path.name}, timeout=5000)
    # ... and its contents are listed without build output.
    qtbot.waitUntil(lambda: names(tree.index_of(tmp_path)) == {"src", "Cargo.toml"}, timeout=5000)

    with qtbot.waitSignal(tree.file_activated) as blocker:
        tree.doubleClicked.emit(tree.index_of(tmp_path / "Cargo.toml"))
    assert blocker.args == [tmp_path / "Cargo.toml"]

    emitted: list[Path] = []
    tree.file_activated.connect(emitted.append)
    tree.doubleClicked.emit(tree.index_of(tmp_path / "src"))  # directories are not "opened"
    assert emitted == []


@pytest.fixture
def tree(qtbot, tmp_path: Path) -> FileTreeView:
    root = tmp_path / "blink"
    for name in (
        "src/main.rs",
        "src/drivers/led.rs",
        "Cargo.toml",
        "Cargo.lock",
        ".cargo/config.toml",
    ):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("")
    view = FileTreeView()
    qtbot.addWidget(view)
    view.set_root(root)
    qtbot.waitUntil(lambda: view.index_of(root / "src" / "main.rs").isValid(), timeout=5000)
    return view


def _child_names(tree: FileTreeView, folder: Path) -> set[str]:
    model = tree.model()
    parent = tree.index_of(folder)
    return {model.data(model.index(r, 0, parent)) for r in range(model.rowCount(parent))}


def test_file_tree_context_menu(qtbot, tree: FileTreeView) -> None:
    root = tree.root
    assert root is not None
    main = root / "src" / "main.rs"
    actions = {a.text(): a for a in tree.context_menu(main).actions() if a.text()}
    assert list(actions)[:4] == ["Новий файл…", "Нова тека…", "Перейменувати", "Видалити"]
    assert "Скопіювати шлях" in actions and "Скопіювати відносний шлях" in actions

    # New items go next to a file, or inside a folder.
    with qtbot.waitSignal(tree.new_file_requested) as blocker:
        actions["Новий файл…"].trigger()
    assert blocker.args == [root / "src"]
    with qtbot.waitSignal(tree.delete_requested) as blocker:
        actions["Видалити"].trigger()
    assert blocker.args == [main]
    with qtbot.waitSignal(tree.path_copied) as blocker:
        actions["Скопіювати відносний шлях"].trigger()
    assert blocker.args == ["src/main.rs"]

    folder_actions = {a.text(): a for a in tree.context_menu(root / "src").actions() if a.text()}
    with qtbot.waitSignal(tree.new_folder_requested) as blocker:
        folder_actions["Нова тека…"].trigger()
    assert blocker.args == [root / "src"]

    # The project folder itself cannot be renamed or deleted from here.
    root_actions = {a.text(): a for a in tree.context_menu(root).actions() if a.text()}
    assert not root_actions["Перейменувати"].isEnabled()
    assert not root_actions["Видалити"].isEnabled()


def test_file_tree_rename_is_an_intent_and_keys_work(qtbot, tree: FileTreeView) -> None:
    root = tree.root
    assert root is not None
    main = root / "src" / "main.rs"
    index = tree.index_of(main)
    assert tree.model().flags(index) & Qt.ItemFlag.ItemIsEditable
    assert not tree.model().flags(tree.index_of(root)) & Qt.ItemFlag.ItemIsEditable

    with qtbot.waitSignal(tree.rename_requested) as blocker:
        assert not tree.model().setData(index, " app.rs ", Qt.ItemDataRole.EditRole)
    assert blocker.args == [main, "app.rs"]
    assert main.exists()  # the view itself never touches the disk

    emitted: list[object] = []
    tree.rename_requested.connect(lambda *args: emitted.append(args))
    tree.model().setData(index, "main.rs", Qt.ItemDataRole.EditRole)  # unchanged name
    assert emitted == []

    tree.setCurrentIndex(index)
    with qtbot.waitSignal(tree.delete_requested) as blocker:
        qtbot.keyClick(tree, Qt.Key.Key_Delete)
    assert blocker.args == [main]
    with qtbot.waitSignal(tree.file_activated) as blocker:
        qtbot.keyClick(tree, Qt.Key.Key_Return)
    assert blocker.args == [main]


def test_file_tree_marks_errors_unsaved_and_service_files(qtbot, tree: FileTreeView) -> None:
    root = tree.root
    assert root is not None
    model = tree.model()
    led = root / "src" / "drivers" / "led.rs"

    def colour(path: Path) -> str | None:
        value = model.data(tree.index_of(path), Qt.ItemDataRole.ForegroundRole)
        return value.name() if value is not None else None

    assert colour(root / "src" / "main.rs") is None
    assert colour(root / "Cargo.lock") == palette.TEXT_MUTED
    qtbot.waitUntil(lambda: tree.index_of(root / ".cargo" / "config.toml").isValid(), timeout=5000)
    assert colour(root / ".cargo" / "config.toml") == palette.TEXT_MUTED

    tree.set_error_files([led])
    assert tree.error_paths == {led, led.parent, root / "src"}  # folders lead to the error
    assert colour(root / "src") == palette.ERROR
    tree.set_error_files([])
    assert colour(root / "src") is None

    tree.set_dirty(led, True)
    assert tree.dirty_paths == {led}
    tree.set_dirty(led, False)
    assert tree.dirty_paths == frozenset()


def test_file_tree_can_hide_service_files(qtbot, tree: FileTreeView) -> None:
    root = tree.root
    assert root is not None
    everything = {"src", "Cargo.toml", "Cargo.lock", ".cargo"}
    qtbot.waitUntil(lambda: _child_names(tree, root) == everything, timeout=5000)
    with qtbot.waitSignal(tree.show_service_changed) as blocker:
        tree.set_show_service_files(False)
    assert blocker.args == [False]
    assert _child_names(tree, root) == {"src", "Cargo.toml"}
    tree.set_show_service_files(True)
    assert _child_names(tree, root) == everything


def test_file_tree_without_project_is_disabled(qtbot) -> None:
    tree = FileTreeView()
    qtbot.addWidget(tree)
    tree.set_root(None)
    assert not tree.isEnabled() and tree.root is None


# --- target panel ---------------------------------------------------------------------------------


@pytest.fixture
def target_panel(qtbot) -> TargetPanel:
    panel = TargetPanel()
    qtbot.addWidget(panel)
    catalog = BoardCatalog.load()
    board = catalog.boards[0]
    panel.set_board(board, catalog.chip_for(board))
    return panel


def test_target_memory_usage_bars(target_panel: TargetPanel) -> None:
    assert target_panel.flash_usage.value.text() == NO_BUILD_TEXT
    assert target_panel.flash_usage.bar.percent is None

    target_panel.set_memory_usage(MemoryUsage(1064, 32768, 4, 10240))
    assert target_panel.flash_usage.value.text() == "1\u202f064 / 32\u202f768 байт (3 %)"
    assert target_panel.ram_usage.value.text() == "4 / 10\u202f240 байт (<1 %)"
    assert target_panel.flash_usage.bar.color == palette.ACCENT

    target_panel.set_memory_usage(MemoryUsage(28000, 32768, 10000, 10240))
    assert target_panel.flash_usage.bar.color == palette.WARNING  # 85 %
    assert target_panel.ram_usage.bar.color == palette.ERROR  # 98 %

    target_panel.set_memory_usage(None)
    assert target_panel.ram_usage.bar.percent is None


def test_target_wiring_follows_the_selected_device(target_panel: TargetPanel) -> None:
    # Nothing selected: the board's default method, and the panel says so.
    assert target_panel.shown_method is UploadMethod.PROBE_RS
    assert target_panel.method.text() == "ST-Link (SWD)"
    assert "не вибрано" in target_panel.method_note.text()
    assert target_panel.wiring_rows[:2] == (("SWDIO", "PA13 (SWDIO)"), ("SWCLK", "PA14 (SWCLK)"))

    target_panel.set_upload_method(UploadMethod.SERIAL_BOOTLOADER)
    assert target_panel.method.text() == "USB-TTL (UART-бутлоадер)"
    assert target_panel.method_note.text() == ""
    # Crossed lines: adapter TX goes to the chip's RX (PA10).
    assert target_panel.wiring_rows == (("TX", "PA10"), ("RX", "PA9"), ("GND", "GND"))
    assert "BOOT0 = 1" in target_panel.wiring_note.text()


def test_target_help_link_and_pin_copy(qtbot, target_panel: TargetPanel) -> None:
    with qtbot.waitSignal(target_panel.help_requested, timeout=1000) as blocker:
        target_panel.help_link.click()
    assert blocker.args == ["stlink"]

    assert target_panel.led.text() == "PC13 · світить при низькому рівні"
    assert target_panel.uart.text() == "USART1 · TX PA9 · RX PA10"
    with qtbot.waitSignal(target_panel.pin_copied, timeout=1000) as blocker:
        target_panel.led.clicked.emit()
    assert blocker.args == ["PC13"]


def test_target_shows_only_on_board_buttons(qtbot, target_panel: TargetPanel) -> None:
    catalog = BoardCatalog.load()
    board = catalog.boards[0]
    chip = catalog.chip_for(board)
    labels = target_panel.findChildren(QLabel)
    caption = next(label for label in labels if label.text() == "Кнопка:")
    target_panel.show()
    assert not caption.isVisible() and not target_panel.button.isVisible()

    target_panel.set_board(dataclasses.replace(board, button=ButtonInfo("PB1", False, True)), chip)
    assert caption.isVisible() and target_panel.button.isVisible()
    assert target_panel.button.text() == "PB1 · на платі"
    with qtbot.waitSignal(target_panel.pin_copied, timeout=1000) as blocker:
        target_panel.button.clicked.emit()
    assert blocker.args == ["PB1"]

    for button in (ButtonInfo("PB1", False, False), None):
        target_panel.set_board(dataclasses.replace(board, button=button), chip)
        assert not caption.isVisible() and not target_panel.button.isVisible()
        with qtbot.assertNotEmitted(target_panel.pin_copied):
            target_panel.button.clicked.emit()


def test_target_last_upload_section(target_panel: TargetPanel) -> None:
    target_panel.show()
    assert not target_panel.last_upload_title.isVisible()
    target_panel.set_last_upload("о 05:02 · 1\u202f064 байти · ST-Link")
    assert target_panel.last_upload_title.isVisible()
    target_panel.set_board(None, None)
    assert target_panel.wiring_rows == ()
    assert target_panel.led.text() == "—"


def test_file_tree_highlight_is_one_shape(qtbot, tree: FileTreeView) -> None:
    """The selected row is highlighted over its name only, not the indentation in front."""
    from PySide6.QtWidgets import QApplication

    palette.apply_theme(QApplication.instance())  # the fix lives in the stylesheet
    root = tree.root
    assert root is not None
    tree.resize(320, 400)
    tree.show()
    main = root / "src" / "main.rs"
    tree.reveal_path(main)
    qtbot.wait(50)
    rect = tree.visualRect(tree.index_of(main))
    image = tree.viewport().grab().toImage()
    y = rect.center().y()
    assert image.pixelColor(rect.x() // 2, y).name() == palette.SURFACE  # indentation untouched
    highlight = image.pixelColor(rect.right() - 30, y)
    # The selection tint (exact shade depends on whether the window is active).
    assert highlight.name() != palette.SURFACE and highlight.green() > highlight.red() + 20


def test_file_tree_draws_no_focus_frame(qtbot, tree: FileTreeView, monkeypatch) -> None:
    """The highlight shows the current row; Qt's focus frame around the name is dropped."""
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

    states = []
    monkeypatch.setattr(
        QStyledItemDelegate,
        "paint",
        lambda self, painter, option, index: states.append(option.state),
    )
    root = tree.root
    assert root is not None
    index = tree.index_of(root / "src" / "main.rs")
    option = QStyleOptionViewItem()
    option.rect = tree.visualRect(index)
    option.state = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
    pixmap = QPixmap(400, 60)
    painter = QPainter(pixmap)
    tree.itemDelegate().paint(painter, option, index)
    painter.end()
    assert states and not states[0] & QStyle.StateFlag.State_HasFocus
    assert states[0] & QStyle.StateFlag.State_Selected


def test_double_click_activates_a_problem_once(qtbot) -> None:
    panel = ProblemsPanel()
    qtbot.addWidget(panel)
    model = BuildModel()
    panel.bind(model)
    model.set_diagnostics([WARN, ERR, LINK])
    received: list[Diagnostic] = []
    panel.diagnostic_activated.connect(received.append)
    index = panel.index_of(ERR, 2)
    panel.tree.doubleClicked.emit(index)  # Qt emits both for one double click
    panel.tree.activated.emit(index)
    assert received == [ERR]
    qtbot.wait(10)
    panel.tree.activated.emit(index)  # the next one (e.g. Return) works again
    assert received == [ERR, ERR]


def test_double_click_opens_a_file_once(qtbot, tree: FileTreeView) -> None:
    root = tree.root
    assert root is not None
    main = root / "src" / "main.rs"
    tree.reveal_path(main)
    qtbot.wait(50)
    opened: list[object] = []
    tree.file_activated.connect(opened.append)
    index = tree.index_of(main)
    tree.doubleClicked.emit(index)
    tree.activated.emit(index)
    assert opened == [main]


def test_log_search_follows_a_growing_and_cleared_log(qtbot) -> None:
    toolbar = LogToolbar()
    qtbot.addWidget(toolbar)
    toolbar.show()
    log = OutputPanel(timestamps=False)
    qtbot.addWidget(log)
    for line in ("warning: one", "note", "warning: two"):
        log.append_line(line)
    toolbar.set_target(log)
    toolbar.open_search()
    toolbar.search_field.setText("warning")
    toolbar.step(1)
    assert toolbar.match_label.text() == "2 з 2"
    position = log.textCursor().position()

    log.append_line("warning: three")
    qtbot.waitUntil(lambda: toolbar.match_label.text() == "2 з 3", timeout=2000)
    assert log.textCursor().position() == position  # the view is not moved by a refresh
    assert len(log.extraSelections()) == 3

    log.clear()
    qtbot.waitUntil(lambda: toolbar.match_label.text() == "немає", timeout=2000)
    assert toolbar.match_count == 0 and log.extraSelections() == []

    toolbar.close_search()
    log.append_line("warning: four")
    qtbot.wait(250)
    assert toolbar.match_count == 0  # a closed search does not run in the background


def test_output_links_windows_absolute_paths(qtbot) -> None:
    panel = OutputPanel()
    qtbot.addWidget(panel)
    panel.append_line(r"  --> C:\Users\dev\blink\src\main.rs:17:9")
    panel.append_line("  ::: C:/Users/dev/blink/src/lib.rs:3:1")
    hrefs = []
    block = panel.document().firstBlock()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.isAnchor():
                hrefs.append(fmt.anchorHref())
            it += 1
        block = block.next()
    assert hrefs == [r"C:\Users\dev\blink\src\main.rs:17:9", "C:/Users/dev/blink/src/lib.rs:3:1"]
    with qtbot.waitSignal(panel.location_activated) as blocker:
        panel.activate_anchor(hrefs[0])
    assert blocker.args == [r"C:\Users\dev\blink\src\main.rs", 17, 9]


def test_settings_tell_a_broken_tool_from_a_missing_one(qtbot) -> None:
    from pathlib import Path

    from PySide6.QtWidgets import QWidget

    from nightexpress.models.entities import ToolStatus
    from nightexpress.views.settings_panel import AppSettingsPanel

    host = QWidget()
    qtbot.addWidget(host)
    panel = AppSettingsPanel(host)
    broken = ToolStatus(
        "cargo",
        False,
        path=Path("/Users/dev/.cargo/bin/cargo"),
        install_hint="cargo знайдено, але він не працює: no default toolchain",
    )
    panel.set_statuses([broken, ToolStatus("probe-rs", False, install_hint="cargo install …")])
    text = panel.tools.text()
    assert "<b style" in text and "cargo</b> — не працює" in text
    assert "probe-rs</b> — не знайдено" in text and text.count("не знайдено") == 1


def test_file_tree_shows_the_build_folder_on_request(qtbot, tree: FileTreeView) -> None:
    root = tree.root
    assert root is not None
    (root / "target" / "debug").mkdir(parents=True)
    usual = {"src", "Cargo.toml", "Cargo.lock", ".cargo"}
    qtbot.wait(200)  # the file system watcher has seen the new folder; it stays hidden
    assert not tree.shows_build_dir and _child_names(tree, root) == usual
    with qtbot.waitSignal(tree.show_build_dir_changed) as blocker:
        tree.set_show_build_dir(True)
    assert blocker.args == [True]
    qtbot.waitUntil(lambda: _child_names(tree, root) == usual | {"target"}, timeout=5000)
    assert tree.is_service(root / "target" / "debug")  # drawn muted, like the service files
    tree.set_show_build_dir(False)
    assert _child_names(tree, root) == usual


def test_a_change_of_the_language_keeps_the_picked_uart_port(qtbot) -> None:
    from nightexpress import i18n
    from nightexpress.models.entities import SerialPortInfo
    from nightexpress.views.panels.uart_panel import UartPanel

    panel = UartPanel()
    qtbot.addWidget(panel)
    first = SerialPortInfo("/dev/cu.a", vid=0x067B, pid=0x2303, serial_number="A")
    second = SerialPortInfo("/dev/cu.b", vid=0x0403, pid=0x6001, serial_number="B")
    panel.set_ports([first, second], first.stable_id)
    panel.port_combo.setCurrentIndex(panel.port_combo.findData(second.stable_id))  # the user
    i18n.switch("en")  # the source_language fixture puts Ukrainian back
    assert panel.current_port_id == second.stable_id
    assert panel.port_combo.itemText(0) == "Choose an adapter"

    panel.set_ports([], None)
    i18n.switch("uk")
    texts = [panel.port_combo.itemText(row) for row in range(panel.port_combo.count())]
    assert texts == ["Виберіть адаптер", "Адаптерів не знайдено"]


def test_problems_are_copied_as_compiler_lines(qtbot, tmp_path: Path) -> None:
    from PySide6.QtGui import QGuiApplication

    panel = ProblemsPanel()
    qtbot.addWidget(panel)
    panel.set_project_dir(tmp_path)
    panel.set_diagnostics(
        (
            Diagnostic(
                DiagnosticLevel.ERROR,
                "cannot find value `x`",
                tmp_path / "src/main.rs",
                12,
                5,
                "E0425",
            ),
            Diagnostic(DiagnosticLevel.WARNING, "unused import", tmp_path / "src/main.rs", 3),
            Diagnostic(DiagnosticLevel.ERROR, "could not compile `blink`"),
        )
    )
    toolbar = LogToolbar()
    qtbot.addWidget(toolbar)
    toolbar.set_target(panel)
    with qtbot.waitSignal(toolbar.copied):
        toolbar.copy_button.click()
    assert QGuiApplication.clipboard().text().splitlines() == [
        "src/main.rs:12:5: error[E0425]: cannot find value `x`",
        "src/main.rs:3: warning: unused import",
        "error: could not compile `blink`",
    ]
