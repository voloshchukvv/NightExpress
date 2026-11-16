"""The views take every colour from the active theme."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from nightexpress.models.entities import Diagnostic, DiagnosticLevel
from nightexpress.models.entities.theme import Theme
from nightexpress.models.qt_models import DiagnosticsTreeModel
from nightexpress.services.theme_catalog import ThemeCatalog
from nightexpress.views import icons
from nightexpress.views.editor.monaco_editor import monaco_theme
from nightexpress.views.panels.log_toolbar import LogToolbar
from nightexpress.views.panels.output_panel import OutputPanel
from nightexpress.views.panels.terminal_panel import terminal_theme
from nightexpress.views.theme import palette

GRAPHITE = ThemeCatalog.load().default
MARK = "#123456"  # a colour no theme uses


def repainted(**colors: str) -> Theme:
    """Graphite with some colours replaced: stands in for "another theme"."""
    return dataclasses.replace(GRAPHITE, id="test", colors={**GRAPHITE.colors, **colors})


@pytest.fixture
def other_theme():
    """Activate a theme where every colour is MARK; graphite comes back afterwards."""
    theme = dataclasses.replace(
        GRAPHITE,
        id="test",
        colors=dict.fromkeys(GRAPHITE.colors, MARK),
        ansi=(MARK,) * 16,
        editor_colors=dict.fromkeys(GRAPHITE.editor_colors, MARK),
        editor_tokens=dict.fromkeys(GRAPHITE.editor_tokens, MARK),
    )
    palette.set_current(theme)
    yield theme
    palette.set_current(GRAPHITE)


def test_palette_names_read_the_active_theme(other_theme: Theme) -> None:
    assert palette.TEXT == palette.ACCENT == palette.color("error") == MARK
    palette.set_current(GRAPHITE)
    assert palette.TEXT == "#e6e8ea" and palette.color("accent") == "#2bb39a"
    with pytest.raises(AttributeError):
        _ = palette.NO_SUCH_COLOUR


def test_style_sheet_and_qt_palette_hold_only_theme_colours(other_theme: Theme) -> None:
    sheet = palette.build_stylesheet()
    assert set(re.findall(r"#[0-9a-fA-F]{6}\b", sheet)) == {MARK}
    assert "{" not in re.sub(r"\{[^{}]*\}", "", sheet)  # every placeholder was filled in
    qt_palette = palette.build_palette()
    assert qt_palette.color(QPalette.ColorRole.Window).name() == MARK
    assert qt_palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name() == MARK
    # The arrows and the check mark of the style sheet are drawn for this theme too.
    urls = set(re.findall(r'url\("([^"]+)"\)', sheet))
    assert {u.rsplit("/", 1)[1] for u in urls} == {
        "check.svg",
        "chevron_down.svg",
        "chevron_up.svg",
        "chevron_up_accent.svg",
    }
    for url in urls:
        text = open(url, encoding="utf-8").read()  # noqa: SIM115
        assert MARK in text and "currentColor" not in text


def test_apply_theme_restyles_the_application_and_announces_it(qtbot) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    theme = repainted(background=MARK)
    try:
        with qtbot.waitSignal(palette.signals.changed) as blocker:
            palette.apply_theme(app, theme)
        assert blocker.args == [theme] and palette.current() is theme
        assert app.palette().color(QPalette.ColorRole.Window).name() == MARK
        assert MARK in app.styleSheet()
    finally:
        palette.apply_theme(app, GRAPHITE)
    assert app.palette().color(QPalette.ColorRole.Window).name() == "#131619"


def test_icons_default_to_the_text_colour_of_the_active_theme(qtbot, other_theme: Theme) -> None:
    def colour_of(image) -> str:
        colours = {
            image.pixelColor(x, y).name()
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha() == 255
        }
        assert len(colours) == 1
        return colours.pop()

    assert colour_of(icons.image("file")) == MARK
    palette.set_current(GRAPHITE)
    assert colour_of(icons.image("file")) == "#e6e8ea"  # not the cached picture of the other theme
    assert colour_of(icons.image("file", "#ff0000")) == "#ff0000"


def test_logs_search_and_problems_use_theme_colours(qtbot, other_theme: Theme) -> None:
    log = OutputPanel(timestamps=False)
    qtbot.addWidget(log)
    log.append_line("\x1b[31merror\x1b[0m plain")
    log.append_line("  --> src/main.rs:1:1")
    block = log.document().firstBlock()
    colours = set()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            colours.add(it.fragment().charFormat().foreground().color().name())
            it += 1
        block = block.next()
    assert colours == {MARK}

    toolbar = LogToolbar()
    qtbot.addWidget(toolbar)
    toolbar.set_target(log)
    toolbar.open_search()
    toolbar.search_field.setText("plain")
    assert [s.format.background().color().name() for s in log.extraSelections()] == [MARK]

    model = DiagnosticsTreeModel(colors=palette.color)
    model.set_diagnostics([Diagnostic(DiagnosticLevel.ERROR, "boom", code="E0425")])
    message = model.index(0, 0, model.index(0, 0))
    assert model.itemFromIndex(message).foreground().color().name() == MARK
    assert model.itemFromIndex(message.siblingAtColumn(1)).foreground().color().name() == MARK


def test_terminal_and_editor_themes_are_built_from_the_theme() -> None:
    terminal = terminal_theme(GRAPHITE)
    assert terminal["background"] == "#15181b" and terminal["cursor"] == "#3fd9bd"
    assert terminal["red"] == "#f07178" and terminal["brightWhite"] == "#ffffff"
    assert len(terminal) == 5 + 16

    editor = monaco_theme(GRAPHITE)
    assert editor["base"] == "vs-dark" and editor["inherit"] is True
    assert {"token": "key", "foreground": "7fd1c1"} in editor["rules"]  # Monaco: no "#"
    assert editor["colors"]["editor.background"] == "#15181b"
    assert monaco_theme(dataclasses.replace(GRAPHITE, dark=False))["base"] == "vs"
    json.dumps(editor)  # goes to JavaScript as JSON


# --- switching while the window is open -----------------------------------------------------


def _shifted(color: str) -> str:
    """Another colour for every colour: channels rotated, so different ones stay different."""
    return "#" + color[5:7] + color[1:3] + color[3:5] + color[7:]


SHIFTED = dataclasses.replace(
    GRAPHITE,
    id="shifted",
    colors={token: _shifted(value) for token, value in GRAPHITE.colors.items()},
    ansi=tuple(_shifted(value) for value in GRAPHITE.ansi),
    editor_colors={key: _shifted(value) for key, value in GRAPHITE.editor_colors.items()},
    editor_tokens={key: _shifted(value) for key, value in GRAPHITE.editor_tokens.items()},
)


def _populated_window(dispose, project: Path):
    """A main window showing a bit of everything that keeps colours of its own."""
    from nightexpress.models.entities import BuildSummary, ToolStatus, UploadMethod
    from nightexpress.models.state import BuildModel, Screen
    from nightexpress.services.board_catalog import BoardCatalog
    from nightexpress.views.main_window import FLASH_TAB, MainWindow

    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    window.resize(1300, 850)
    catalog = BoardCatalog.load()
    board = catalog.get_board("stm32f103c6-minimal")
    window.set_project("blink", project)
    window.set_board(board, catalog.chip_for(board))
    window.set_screen(Screen.EDITOR)
    window.set_tools_status("Rust: не працює cargo", ok=False)
    window.set_connection("PL2303", UploadMethod.SERIAL_BOOTLOADER)
    window.set_problem_counts(1, 1)
    statuses = (
        ToolStatus("rustup", True, "1.28.2", path=Path("/bin/rustup")),
        ToolStatus("cargo", False, path=Path("/bin/cargo"), install_hint="зламано"),
        ToolStatus("rust-analyzer", False, required=False, install_hint="rustup component add"),
        ToolStatus("probe-rs", False, required=False, install_hint="cargo install probe-rs"),
    )
    window.welcome.set_tool_statuses(statuses)
    window.settings_dialog.application.set_statuses(statuses)
    model = BuildModel()
    window.problems_panel.bind(model)
    model.set_diagnostics(
        [
            Diagnostic(DiagnosticLevel.ERROR, "boom", code="E0425", file=project / "a.rs", line=1),
            Diagnostic(DiagnosticLevel.WARNING, "hm", code="unused", file=project / "a.rs", line=2),
            Diagnostic(DiagnosticLevel.NOTE, "see", file=project / "a.rs", line=3),
            Diagnostic(DiagnosticLevel.HELP, "try", file=project / "a.rs", line=4),
        ]
    )
    for log in (window.output_panel, window.flash_panel):
        log.timestamps = False
        log.append_line("\x1b[1m\x1b[32m   Compiling\x1b[0m blink v0.1.0")
        log.append_line("\x1b[31merror\x1b[0m: boom \x1b[34mnote\x1b[0m \x1b[93mbright\x1b[0m")
        log.append_line("  --> src/main.rs:1:1")
        log.append_summary(BuildSummary(False, "Збірка не вдалася"))
        log.append_summary(BuildSummary(True, "Готово"))
    window.uart_panel.set_status("порт зайнятий", error=True)
    window.notify_tab(FLASH_TAB, error=True)
    window.show()
    return window


def _pictures(qtbot, window) -> dict[str, object]:
    """Images of the parts drawn by Qt (the web views of the editor and terminal draw later)."""
    qtbot.wait(150)  # the file tree loads its folder
    focused = QApplication.focusWidget()
    if focused is not None:
        focused.clearFocus()  # a focus ring is not a matter of the theme
    parts = {
        "header": window.header,
        "toolbar": window.toolbar,
        "tree": window.file_tree,
        "target": window.target_view,
        "status": window.statusBar(),
        "settings": window.settings_dialog.application,
        "welcome": window.welcome,
    }
    pictures = {name: widget.grab().toImage() for name, widget in parts.items()}
    for index, name in ((0, "problems"), (1, "build"), (2, "flash"), (3, "uart")):
        window.bottom_tabs.setCurrentIndex(index)
        qtbot.wait(20)
        pictures[name] = window.bottom_tabs.currentWidget().grab().toImage()
    pictures["tabs"] = window.bottom_tabs.tabBar().grab().toImage()  # every tab visited by now
    return pictures


def test_remembered_icons_are_remade_for_the_new_theme(qtbot) -> None:
    def picture(icon) -> object:
        return icon.pixmap(32, 32).toImage()

    plain = icons.icon("file", palette.TEXT_MUTED)
    badged = icons.badged_icon("hammer", palette.TEXT_MUTED, palette.ERROR)
    fixed = icons.icon("file", "#ff0000")  # not a theme colour: stays as it is
    palette.set_current(SHIFTED)
    try:
        assert picture(icons.retinted(plain)) == picture(icons.icon("file", palette.TEXT_MUTED))
        assert picture(icons.retinted(plain)) != picture(plain)
        again = icons.badged_icon("hammer", palette.TEXT_MUTED, palette.ERROR)
        assert picture(icons.retinted(badged)) == picture(again)
        assert icons.retinted(fixed) is None
    finally:
        palette.set_current(GRAPHITE)
    # Two tokens with the same colour today stay two icons (they may part in another theme).
    same = dataclasses.replace(GRAPHITE, colors={**GRAPHITE.colors, "ok": GRAPHITE.color("text")})
    palette.set_current(same)
    try:
        ok, text = icons.icon("file", palette.OK), icons.icon("file", palette.TEXT)
        assert ok.cacheKey() != text.cacheKey()
        palette.set_current(GRAPHITE)
        assert picture(icons.retinted(ok)) == picture(icons.icon("file", palette.OK))
        assert picture(icons.retinted(ok)) != picture(icons.retinted(text))
    finally:
        palette.set_current(GRAPHITE)


def test_switching_the_theme_equals_starting_in_it(qtbot, dispose, tmp_path: Path) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    project = tmp_path / "blink"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.rs").write_text("fn main() {}\n")
    (project / "Cargo.toml").write_text("[package]\n")
    try:
        palette.apply_theme(app, GRAPHITE)
        switched = _populated_window(dispose, project)
        graphite = _pictures(qtbot, switched)
        palette.apply_theme(app, SHIFTED)  # the window is open: it has to follow
        after_switch = _pictures(qtbot, switched)

        fresh = _populated_window(dispose, project)  # built in the other theme from the start
        started = _pictures(qtbot, fresh)
        different = [name for name in started if after_switch[name] != started[name]]
        assert not different, f"left in the old colours after a switch: {different}"
        assert all(after_switch[name] != graphite[name] for name in started)

        palette.apply_theme(app, GRAPHITE)  # and back
        back = _pictures(qtbot, switched)
        assert [name for name in back if back[name] != graphite[name]] == []
    finally:
        palette.apply_theme(app, GRAPHITE)
        app.setStyleSheet("")


def test_header_bar_on_the_start_screen_and_in_the_editor(qtbot, dispose) -> None:
    """The menus lie on the window's colour with no line under them, on both screens.

    Start screen: the name block is the top of the sidebar's column (same fill, the column's
    edge runs to the top of the window, no line under the name). Editor: no fill at all, a
    short divider between the name and the menus.
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QFrame

    from nightexpress.models.state import Screen
    from nightexpress.views.header_bar import DIVIDER_HEIGHT
    from nightexpress.views.main_window import MainWindow
    from nightexpress.views.welcome_view import PAGE_MARGINS, SIDEBAR_WIDTH

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    edge = SIDEBAR_WIDTH - 1  # x of the sidebar's border line
    try:
        for theme in ThemeCatalog.load().themes:
            palette.apply_theme(app, theme)
            window = MainWindow("NightExpress", "0.1.0")
            dispose(window)
            window.resize(1300, 850)
            window.show()
            qtbot.waitExposed(window)
            header = window.header
            origin = header.mapTo(window, QPoint(0, 0))
            bottom = header.height() - 1
            surface, border, background = (
                theme.color(token) for token in ("surface", "border", "background")
            )

            def colours(window=window, origin=origin):
                picture = window.grab().toImage()
                scale = picture.devicePixelRatio()
                return lambda x, y: picture.pixelColor((origin + QPoint(x, y)) * scale).name()

            def menus_lie_on_the_window(pixel, theme=theme, header=header, bottom=bottom) -> None:
                fill = theme.color("background")
                for x in (edge + 1, edge + 8, header.width() - 8):
                    assert pixel(x, 3) == pixel(x, bottom) == fill, (theme.id, x)  # no line
                rows = range(2, header.height() - 2)
                after = range(edge + 1, header.width())
                text = next(x for x in after if any(pixel(x, y) != fill for y in rows))
                # The first menu's text starts where the text of the page below starts.
                assert abs(text - (SIDEBAR_WIDTH + PAGE_MARGINS[0])) <= 1, (theme.id, text)

            # --- start screen: one column from the top of the window to the bottom ---
            sidebar = window.welcome.findChild(QFrame, "sidebar")
            assert sidebar.mapTo(window, QPoint(sidebar.width() - 1, 0)) - origin == QPoint(
                edge, header.height()
            )
            pixel = colours()
            assert header.height() == 42
            assert pixel(4, 3) == pixel(4, bottom) == pixel(edge - 1, bottom) == surface, theme.id
            assert pixel(edge, 0) == pixel(edge, 20) == pixel(edge, bottom) == border, theme.id
            assert not header.divider.isVisible()
            menus_lie_on_the_window(pixel)

            # --- editor: nothing but the name, a short divider and the menus ---
            window.set_screen(Screen.EDITOR)
            qtbot.wait(50)
            pixel = colours()
            assert header.height() == 42  # the window below does not move
            assert pixel(4, 3) == pixel(4, bottom) == pixel(edge - 1, bottom) == background
            top = header.divider.mapTo(window, QPoint(0, 0)) - origin
            assert header.divider.isVisible() and header.divider.height() == DIVIDER_HEIGHT
            assert top.x() == edge and abs(top.y() + DIVIDER_HEIGHT / 2 - header.height() / 2) <= 1
            assert pixel(edge, top.y() + DIVIDER_HEIGHT // 2) == border, theme.id
            assert pixel(edge, top.y() - 2) == pixel(edge, bottom) == background, theme.id
            menus_lie_on_the_window(pixel)

            window.set_screen(Screen.WELCOME)  # and back
            qtbot.wait(50)
            assert colours()(edge, bottom) == border and not header.divider.isVisible()
            window.close()
    finally:
        palette.apply_theme(app, GRAPHITE)
        app.setStyleSheet("")


def test_clicked_buttons_keep_no_focus_outline(qtbot) -> None:
    """Buttons take focus from the keyboard only, so a click leaves no outline behind."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton, QVBoxLayout, QWidget

    from nightexpress.views.widgets import tool_button

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    palette.apply_theme(app, GRAPHITE)
    window = QWidget()
    qtbot.addWidget(window)
    buttons = [tool_button("copy", "Copy", window), QPushButton("Run", window), QCheckBox("x")]
    field = QLineEdit(window)
    layout = QVBoxLayout(window)
    layout.addWidget(field)
    for button in buttons:
        layout.addWidget(button)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    field.setFocus()
    for button in buttons:
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        assert field.hasFocus()
        assert button.focusPolicy() == Qt.FocusPolicy.TabFocus  # still reachable with Tab
