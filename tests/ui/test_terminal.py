"""The "Terminal" tab end to end: real QtWebEngine + xterm.js + a real shell in a PTY."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from nightexpress.controllers.terminal_controller import TerminalController
from nightexpress.models.entities import ProjectConfig, UploadMethod
from nightexpress.models.state import ProjectModel
from nightexpress.views.main_window import BUILD_TAB, TERMINAL_TAB, MainWindow
from nightexpress.views.panels.terminal_panel import TerminalView, terminal_theme, xterm_installed

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not xterm_installed(), reason="run scripts/fetch_xterm.py first"),
]

READY_TIMEOUT = 20_000


def js(qtbot, view: TerminalView, script: str) -> Any:
    box: list[Any] = []
    view._page.runJavaScript(f"JSON.stringify({script})", 0, box.append)
    qtbot.waitUntil(lambda: bool(box), timeout=5000)
    return json.loads(box[0])


def screen(qtbot, view: TerminalView) -> str:
    return js(qtbot, view, "window.stmr.text()")


def wait_screen(qtbot, view: TerminalView, text: str, timeout: int = 5000) -> None:
    qtbot.waitUntil(lambda: text in screen(qtbot, view), timeout=timeout)


@pytest.fixture
def view(qtbot, dispose) -> TerminalView:
    widget = TerminalView()
    dispose(widget)
    widget.resize(700, 300)
    widget.show()
    with qtbot.waitSignal(widget.ready, timeout=READY_TIMEOUT):
        pass
    return widget


def test_theme_uses_app_palette() -> None:
    theme = terminal_theme()
    assert theme["background"] == "#15181b" and len(theme) == 5 + 16


def test_view_shows_bytes_and_reports_input(qtbot, view: TerminalView) -> None:
    cols, rows = view.size
    assert cols > 40 and rows > 5  # fitted to the widget, not the 80x24 default
    view.write("при".encode()[:5])  # a UTF-8 character split between two writes
    view.write("при".encode()[5:] + "\x1b[31mвіт\x1b[0m\r\nline 2".encode())
    wait_screen(qtbot, view, "привіт\nline 2")

    with qtbot.waitSignal(view.input_received, timeout=1000) as typed:
        view._bridge.input("ls -la\r")
    assert typed.args == [b"ls -la\r"]

    with qtbot.waitSignal(view.resized, timeout=5000) as resized:
        view.resize(500, 200)
    assert resized.args[0] < cols

    view.write_text("a\nb")  # application messages get CRLF
    wait_screen(qtbot, view, "a\nb")
    view.reset()
    qtbot.waitUntil(lambda: screen(qtbot, view) == "", timeout=5000)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell")
def test_terminal_tab_runs_a_shell_in_the_project(qtbot, dispose, tmp_path: Path) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    window.resize(1200, 800)
    window.show()
    project_model = ProjectModel()
    controller = TerminalController(window, project_model, shell=["/bin/sh", "-i"])
    view = window.terminal_panel
    with qtbot.waitSignal(view.ready, timeout=READY_TIMEOUT):
        pass

    project = tmp_path / "blink"
    project.mkdir()
    project_model.open_project(
        ProjectConfig(project, "blink", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    )
    assert not controller.session.running  # nothing is started until the tab is shown

    window.bottom_tabs.setCurrentIndex(TERMINAL_TAB)
    qtbot.waitUntil(lambda: controller.session.running, timeout=5000)
    view._bridge.input("echo dir=$(pwd)\r")
    wait_screen(qtbot, view, f"dir={project.resolve()}")

    # The shell ends: Enter starts a new one.
    with qtbot.waitSignal(controller.session.exited, timeout=5000):
        view._bridge.input("exit\r")
    wait_screen(qtbot, view, "Натисніть Enter")
    view._bridge.input("x")  # anything but Enter is ignored
    assert not controller.session.running
    view._bridge.input("\r")
    qtbot.waitUntil(lambda: controller.session.running, timeout=5000)

    # Another tab and back: the same shell keeps running.
    window.bottom_tabs.setCurrentIndex(BUILD_TAB)
    window.bottom_tabs.setCurrentIndex(TERMINAL_TAB)
    assert controller.session.running

    project_model.close_project()
    assert not controller.session.running
    controller.shutdown()


@pytest.mark.skipif(sys.platform != "darwin", reason="Cmd shortcuts are macOS-only in the terminal")
@pytest.mark.parametrize(
    ("key", "command"),
    [
        ("b", "verify"),
        ("1", "focus_tree"),
        ("=", "zoom_in"),
        ("-", "zoom_out"),
        ("0", "zoom_reset"),
    ],
)
def test_app_shortcuts_work_with_terminal_focus(
    qtbot, view: TerminalView, key: str, command: str
) -> None:
    received: list[bytes] = []
    view.input_received.connect(received.append)
    init = json.dumps({"key": key, "metaKey": True, "bubbles": True})
    script = (
        "(() => { const area = document.querySelector('.xterm-helper-textarea'); area.focus();"
        f" area.dispatchEvent(new KeyboardEvent('keydown', {init})); }})()"
    )
    with qtbot.waitSignal(view.command_requested, timeout=5000) as blocker:
        view._page.runJavaScript(script)
    assert blocker.args == [command]
    assert received == []  # the shell does not get the key


def test_toolbar_searches_copies_and_clears_the_terminal(qtbot, view: TerminalView) -> None:
    from PySide6.QtGui import QGuiApplication

    from nightexpress.views.panels.log_toolbar import LogToolbar

    view.write_text("Compiling blink\nFinished\ncompiling again\n")
    wait_screen(qtbot, view, "compiling again")
    toolbar = LogToolbar()
    qtbot.addWidget(toolbar)
    toolbar.show()
    toolbar.set_target(view)
    assert toolbar.tools == {"search", "copy", "clear"}
    assert toolbar.wrap_button.isHidden()  # the terminal wraps lines itself

    toolbar.open_search()
    toolbar.search_field.setText("compiling")
    qtbot.waitUntil(lambda: toolbar.match_count == 2, timeout=5000)  # case-insensitive
    assert toolbar.match_label.text() in {"1 з 2", "2 з 2"}
    first = toolbar.current_match
    toolbar.step(1)
    qtbot.waitUntil(lambda: toolbar.current_match == 1 - first, timeout=5000)
    toolbar.search_field.setText("nothing like this")
    qtbot.waitUntil(lambda: toolbar.match_label.text() == "немає", timeout=5000)
    toolbar.close_search()

    with qtbot.waitSignal(toolbar.copied, timeout=5000):
        toolbar.copy_button.click()
    assert "Finished\ncompiling again" in QGuiApplication.clipboard().text()

    view.clear()  # what the clear button asks the window to do
    qtbot.waitUntil(lambda: "Finished" not in screen(qtbot, view), timeout=5000)
