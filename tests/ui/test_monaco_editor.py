"""MonacoEditor end to end: real QtWebEngine + Monaco, driven through its Python API."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QColor

from nightexpress.app import build_app
from nightexpress.models.entities import Diagnostic, DiagnosticLevel
from nightexpress.views.editor.monaco_editor import MonacoEditor, language_for, monaco_installed

pytestmark = [
    pytest.mark.ui,
    pytest.mark.usefixtures("no_debug_probes"),
    pytest.mark.skipif(not monaco_installed(), reason="run scripts/fetch_monaco.py first"),
]

READY_TIMEOUT = 20_000
MAIN = Path("/work/blink/src/main.rs")
CARGO = Path("/work/blink/Cargo.toml")
SOURCE = "fn main() {\n    let x = 1;\n}\n"


def dispose(qtbot, widget: MonacoEditor) -> None:
    """Delete the page before QtWebEngine's profile goes away at interpreter exit."""
    widget.close()
    widget.deleteLater()
    qtbot.wait(10)


@pytest.fixture
def editor(qtbot) -> Iterator[MonacoEditor]:
    widget = MonacoEditor()
    widget.resize(800, 500)
    widget.show()
    with qtbot.waitSignal(widget.ready, timeout=READY_TIMEOUT):
        pass
    yield widget
    dispose(qtbot, widget)


def js(qtbot, editor: MonacoEditor, script: str) -> Any:
    """Evaluate a JavaScript expression in the editor page and return its JSON value."""
    box: list[Any] = []
    editor._page.runJavaScript(f"JSON.stringify({script})", 0, box.append)
    qtbot.waitUntil(lambda: bool(box), timeout=5000)
    return json.loads(box[0])


def wait_js(qtbot, editor: MonacoEditor, script: str, expected: Any, timeout: int = 5000) -> None:
    """Poll a JavaScript expression until it equals ``expected``."""
    deadline = time.monotonic() + timeout / 1000
    value = js(qtbot, editor, script)
    while value != expected and time.monotonic() < deadline:
        qtbot.wait(50)
        value = js(qtbot, editor, script)
    assert value == expected


@pytest.mark.parametrize(
    ("name", "language"),
    [
        ("main.rs", "rust"),
        ("Cargo.toml", "toml"),
        ("README.md", "markdown"),
        ("memory.x", "plaintext"),
    ],
)
def test_language_for(name: str, language: str) -> None:
    assert language_for(Path(name)) == language


def test_loads_without_console_errors(editor: MonacoEditor) -> None:
    assert editor.is_ready
    assert [m for m in editor.console_messages if m[0] == "error"] == []


def test_calls_before_ready_are_queued(qtbot) -> None:
    widget = MonacoEditor()
    widget.open_document(MAIN, SOURCE)  # queued until Monaco has loaded
    with qtbot.waitSignal(widget.ready, timeout=READY_TIMEOUT):
        widget.show()
    value = js(qtbot, widget, "monaco.editor.getModels()[0].getValue()")
    dispose(qtbot, widget)
    assert value == SOURCE


def test_bracket_pair_guides_setting(qtbot, dispose, settings_store, tmp_path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\n'
        '[package.metadata.nightexpress]\nboard = "stm32f103c6-minimal"\n',
        encoding="utf-8",
    )
    source = tmp_path / "src" / "main.rs"
    source.parent.mkdir()
    source.write_text(SOURCE, encoding="utf-8")
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    window = controller.window
    dispose(window)
    controller.start()
    assert controller.projects.open_project(tmp_path)

    editor = window.editor_tabs.editor
    qtbot.waitUntil(lambda: editor.is_ready, timeout=READY_TIMEOUT)
    wait_js(qtbot, editor, "!!document.querySelector('.view-lines .view-line')", True)
    has_guides = "!!document.querySelector('.bracket-indent-guide')"
    window.actions_by_name["settings"].trigger()
    dialog = window.settings_dialog
    assert dialog.isVisible()
    dialog.tabs.setCurrentIndex(1)
    checkbox = dialog.application.options["bracket_pair_guides"]
    welcome_checkbox = window.welcome.settings.options["bracket_pair_guides"]
    assert not checkbox.isChecked() and not welcome_checkbox.isChecked()
    assert not js(qtbot, editor, has_guides)

    checkbox.click()
    assert settings_store.bracket_pair_guides() and welcome_checkbox.isChecked()
    wait_js(qtbot, editor, has_guides, True)
    checkbox.click()
    assert not settings_store.bracket_pair_guides() and not welcome_checkbox.isChecked()
    wait_js(qtbot, editor, has_guides, False)

    # The minimap is on by default (in an editor wide enough for it) and can be switched off.
    window.resize(1500, 900)
    shown = "monaco.editor.getEditors()[0].getOption(monaco.editor.EditorOption.minimap).enabled"
    minimap = dialog.application.options["minimap"]
    assert minimap.isChecked() and settings_store.minimap()
    wait_js(qtbot, editor, shown, True)
    minimap.click()
    assert (
        not settings_store.minimap() and not window.welcome.settings.options["minimap"].isChecked()
    )
    wait_js(qtbot, editor, shown, False)
    minimap.click()
    wait_js(qtbot, editor, shown, True)
    dialog.reject()


def test_open_switch_and_close_documents(qtbot, editor: MonacoEditor) -> None:
    editor.open_document(MAIN, SOURCE)
    editor.open_document(CARGO, '[package]\nname = "blink"\n')
    assert editor.documents == (MAIN, CARGO)
    assert editor.active_document == CARGO
    assert js(qtbot, editor, "monaco.editor.getModels().length") == 2
    languages = js(qtbot, editor, "monaco.editor.getModels().map(m => m.getLanguageId()).join(',')")
    assert languages == "rust,toml"

    editor.show_document(MAIN)
    editor.close_document(CARGO)
    assert editor.documents == (MAIN,)
    wait_js(qtbot, editor, "monaco.editor.getModels().length", 1)


def test_user_edit_updates_mirror_and_emits(qtbot, editor: MonacoEditor) -> None:
    editor.open_document(MAIN, SOURCE)
    with qtbot.waitSignal(editor.content_changed, timeout=5000) as blocker:
        js(qtbot, editor, "(monaco.editor.getModels()[0].setValue('fn main() {}\\n'), true)")
    assert blocker.args == [MAIN, "fn main() {}\n"]
    assert editor.text(MAIN) == "fn main() {}\n"


def test_replace_text_keeps_mirror_in_sync_without_signal(qtbot, editor: MonacoEditor) -> None:
    editor.open_document(MAIN, SOURCE)
    emitted: list[Any] = []
    editor.content_changed.connect(lambda *a: emitted.append(a))
    editor.replace_text(MAIN, "// reloaded\n")
    wait_js(qtbot, editor, "monaco.editor.getModels()[0].getValue()", "// reloaded\n")
    assert editor.text(MAIN) == "// reloaded\n"
    assert emitted == []  # the mirror already matched, so nothing is reported as a user edit


def test_diagnostics_become_markers(qtbot, editor: MonacoEditor) -> None:
    editor.open_document(MAIN, SOURCE)
    editor.set_diagnostics(
        [
            Diagnostic(DiagnosticLevel.WARNING, "unused variable: `x`", MAIN, 2, 9),
            Diagnostic(DiagnosticLevel.ERROR, "linker failed"),  # no location -> ignored
        ]
    )
    script = (
        "monaco.editor.getModelMarkers({}).map(m => [m.severity, m.startLineNumber, m.message])"
    )
    wait_js(qtbot, editor, script, [[4, 2, "unused variable: `x`"]])

    editor.set_diagnostics([])
    wait_js(qtbot, editor, script, [])


def test_reveal_moves_cursor(qtbot, editor: MonacoEditor) -> None:
    editor.open_document(MAIN, SOURCE)
    with qtbot.waitSignal(editor.cursor_moved, timeout=5000) as blocker:
        editor.reveal(MAIN, 2, 5)
    assert blocker.args == [2, 5]


def test_shortcut_commands_reach_python(qtbot, editor: MonacoEditor) -> None:
    # Monaco key bindings call bridge.commandRequested(name); simulate that call from JS.
    with qtbot.waitSignal(editor.command_requested, timeout=5000) as blocker:
        editor._page.runJavaScript(
            "new QWebChannel(qt.webChannelTransport,"
            " c => c.objects.bridge.commandRequested('upload'))"
        )
    assert blocker.args == ["upload"]
    received: list[str] = []
    editor.command_requested.connect(received.append)
    editor._bridge.commandRequested("rm -rf /")  # unknown commands are dropped
    assert received == []


def test_unknown_document_operations_raise(editor: MonacoEditor) -> None:
    with pytest.raises(KeyError):
        editor.show_document(MAIN)
    with pytest.raises(KeyError):
        editor.reveal(MAIN, 1)
    editor.close_document(MAIN)  # closing an unknown document is a no-op


@pytest.mark.parametrize(
    ("key", "code", "key_code", "modifiers", "command"),
    [
        ("F8", "F8", 119, {}, "next_problem"),
        ("F8", "F8", 119, {"shiftKey": True}, "previous_problem"),
        ("1", "Digit1", 49, {"cmd": True}, "focus_tree"),
        ("=", "Equal", 187, {"cmd": True}, "zoom_in"),
        ("-", "Minus", 189, {"cmd": True}, "zoom_out"),
        ("0", "Digit0", 48, {"cmd": True}, "zoom_reset"),
        ("F", "KeyF", 70, {"shiftKey": True, "altKey": True}, "format"),
    ],
)
def test_new_shortcuts_pressed_in_the_editor(
    qtbot,
    editor: MonacoEditor,
    key: str,
    code: str,
    key_code: int,
    modifiers: dict[str, bool],
    command: str,
) -> None:
    """A real keydown in Monaco's input area: the key binding itself is exercised."""
    editor.open_document(MAIN, SOURCE)
    mods = dict(modifiers)
    if mods.pop("cmd", False):
        mods["metaKey" if sys.platform == "darwin" else "ctrlKey"] = True
    init = {"key": key, "code": code, "keyCode": key_code, "bubbles": True, **mods}
    script = (
        "(() => { const area = document.querySelector('#container textarea'); area.focus();"
        f" area.dispatchEvent(new KeyboardEvent('keydown', {json.dumps(init)})); }})()"
    )
    with qtbot.waitSignal(editor.command_requested, timeout=5000) as blocker:
        editor._page.runJavaScript(script)
    assert blocker.args == [command]


def test_editor_starts_in_the_theme_and_can_be_recoloured(qtbot, editor: MonacoEditor) -> None:
    import dataclasses

    from nightexpress.views.theme import palette

    background = (
        "getComputedStyle(document.querySelector('.monaco-editor'))"
        ".getPropertyValue('--vscode-editor-background').trim()"
    )
    editor.open_document(MAIN, SOURCE)  # Monaco builds its DOM with the first document
    # Asked from Python before the editor was created: no colours live in editor.js.
    wait_js(qtbot, editor, "!!document.querySelector('.monaco-editor')", True)
    assert js(qtbot, editor, background) == palette.EDITOR
    other = dataclasses.replace(
        palette.current(), dark=False, editor_colors={"editor.background": "#102030"}
    )
    editor.set_theme(other)
    wait_js(qtbot, editor, background, "#102030")
    editor.set_theme()  # back to the application's theme
    wait_js(qtbot, editor, background, palette.EDITOR)
    assert not [message for level, message in editor._page.console if level == "error"]


def test_every_bundled_theme_colours_the_editor(qtbot, editor: MonacoEditor) -> None:
    from nightexpress.services.theme_catalog import ThemeCatalog

    def variable(name: str) -> str:
        return (
            "getComputedStyle(document.querySelector('.monaco-editor'))"
            f".getPropertyValue('--vscode-{name}').trim()"
        )

    editor.open_document(MAIN, SOURCE)
    wait_js(qtbot, editor, "!!document.querySelector('.monaco-editor')", True)
    for theme in ThemeCatalog.load().themes:
        editor.set_theme(theme)
        wait_js(qtbot, editor, variable("editor-background"), theme.color("editor"))
        assert (
            js(qtbot, editor, variable("editorCursor-foreground"))
            == theme.editor_colors["editorCursor.foreground"]
        ), theme.id
    editor.set_theme()
    assert not [message for level, message in editor._page.console if level == "error"]


def test_editor_context_menu_is_opaque_and_themed(qtbot, editor: MonacoEditor) -> None:
    from nightexpress.services.theme_catalog import ThemeCatalog

    editor.open_document(MAIN, SOURCE)
    wait_js(qtbot, editor, "!!document.querySelector('.view-line span span')", True)
    editor._page.runJavaScript(
        """{
          const token = document.querySelector('.view-line span span');
          const rect = token.getBoundingClientRect();
          token.dispatchEvent(new MouseEvent('contextmenu', {
            bubbles: true, button: 2, clientX: rect.left + 1, clientY: rect.top + rect.height / 2,
          }));
        }"""
    )
    surface = "document.querySelector('.monaco-menu-container > .monaco-scrollable-element')"
    wait_js(qtbot, editor, f"!!{surface}", True)
    style = f"getComputedStyle({surface})"
    label = f"{surface}.querySelector('.action-label:not(.separator)')"
    assert js(qtbot, editor, f"getComputedStyle({label}).paddingInlineStart") == "8px"
    for end in (".action-menu-item > .action-label:last-child", ".keybinding"):
        item = f"{surface}.querySelector('{end}')"
        assert js(qtbot, editor, f"getComputedStyle({item}).paddingInlineEnd") == "8px"

    def rgb(color: str) -> str:
        red, green, blue, _ = QColor(color).getRgb()
        return f"rgb({red}, {green}, {blue})"

    for theme in ThemeCatalog.load().themes:
        editor.set_theme(theme)
        expected = [rgb(theme.color("surface")), rgb(theme.color("text"))]
        wait_js(qtbot, editor, f"[{style}.backgroundColor, {style}.color]", expected)


def test_command_palette_looks_like_quick_open(qtbot, editor: MonacoEditor) -> None:
    from nightexpress.services.theme_catalog import ThemeCatalog
    from nightexpress.views.editor.monaco_editor import EditorAction

    editor.open_document(MAIN, SOURCE)
    editor.trigger_action(EditorAction.COMMAND_PALETTE)
    widget = "document.querySelector('.quick-input-widget')"
    row = f"{widget}.querySelector('.monaco-list-row.focused')"
    wait_js(qtbot, editor, f"!!({widget} && {row})", True)
    assert js(qtbot, editor, f"getComputedStyle({widget}).borderRadius") == "8px"
    assert js(qtbot, editor, f"getComputedStyle({row}).outlineStyle") == "none"
    # Rows are roomier than Monaco's fixed 22 px.
    assert js(qtbot, editor, f"{row}.getBoundingClientRect().height") > 26

    def rgb(color: str) -> str:
        red, green, blue, _ = QColor(color).getRgb()
        return f"rgb({red}, {green}, {blue})"

    box = f"{widget}.querySelector('.monaco-inputbox')"
    colors = (
        f"[getComputedStyle({widget}).backgroundColor, getComputedStyle({widget}).color,"
        f" getComputedStyle({row}).backgroundColor, getComputedStyle({box}).borderColor]"
    )
    for theme in ThemeCatalog.load().themes:
        editor.set_theme(theme)
        expected = [
            rgb(theme.color(token)) for token in ("surface", "text", "accent_soft", "focus")
        ]
        wait_js(qtbot, editor, colors, expected)
