"""The "Terminal" tab: xterm.js (the terminal of VS Code) embedded through QtWebEngine.

The widget only shows bytes and reports keys; the shell itself lives in a PtySession owned by
the TerminalController. Output is batched (one JavaScript call per frame) so that a fast
``cargo build`` does not flood the page with thousands of calls.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from nightexpress.i18n import _
from nightexpress.models.entities.theme import Theme
from nightexpress.views.theme import palette

log = logging.getLogger(__name__)

FLUSH_INTERVAL_MS = 16
MAX_BATCH = 256 * 1024  # bytes per JavaScript call

_ANSI_NAMES = (
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow",
    "brightBlue", "brightMagenta", "brightCyan", "brightWhite",
)  # fmt: skip


def terminal_directory() -> Path:
    return Path(str(resources.files("nightexpress") / "resources" / "terminal"))


def xterm_installed(directory: Path | None = None) -> bool:
    base = directory or terminal_directory()
    files = ("xterm.js", "addon-fit.js", "addon-search.js")
    return all((base / "xterm" / name).is_file() for name in files)


def terminal_theme(theme: Theme | None = None) -> dict[str, str]:
    """xterm.js colours from the application theme (same ANSI colours as the build log)."""
    theme = theme or palette.current()
    colors = {
        "background": theme.color("editor"),
        "foreground": theme.color("text"),
        "cursor": theme.color("accent_bright"),
        "cursorAccent": theme.color("editor"),
        "selectionBackground": theme.color("accent_soft"),
    }
    colors.update(dict(zip(_ANSI_NAMES, theme.ansi, strict=True)))
    return colors


class _Bridge(QObject):
    """Object published to JavaScript as ``bridge``; slot names are the JS-facing API."""

    ready = Signal(int, int)
    data = Signal(bytes)
    size_changed = Signal(int, int)
    command = Signal(str)
    copy = Signal(str)
    paste = Signal()
    search_results = Signal(int, int)  # index of the current match (-1: none), count

    @Slot(int, int)
    def terminalReady(self, cols: int, rows: int) -> None:  # noqa: N802 - JS-facing name
        self.ready.emit(cols, rows)

    @Slot(str)
    def input(self, text: str) -> None:
        self.data.emit(text.encode("utf-8"))

    @Slot(str)
    def inputBinary(self, text: str) -> None:  # noqa: N802 - JS-facing name
        self.data.emit(text.encode("latin-1", errors="replace"))

    @Slot(int, int)
    def resized(self, cols: int, rows: int) -> None:
        self.size_changed.emit(cols, rows)

    @Slot(str)
    def commandRequested(self, name: str) -> None:  # noqa: N802 - JS-facing name
        self.command.emit(name)

    @Slot(str)
    def copyText(self, text: str) -> None:  # noqa: N802 - JS-facing name
        self.copy.emit(text)

    @Slot()
    def pasteRequested(self) -> None:  # noqa: N802 - JS-facing name
        self.paste.emit()

    @Slot(int, int)
    def searchResults(self, index: int, count: int) -> None:  # noqa: N802 - JS-facing name
        self.search_results.emit(index, count)


class _Page(QWebEnginePage):
    def javaScriptConsoleMessage(  # noqa: N802 - Qt override
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        log.debug("terminal %s: %s (%s:%s)", level.name, message, source, line)


class TerminalView(QWidget):
    ready = Signal(int, int)  # cols, rows once xterm.js is up
    input_received = Signal(bytes)  # keys typed / text pasted, to send to the shell
    resized = Signal(int, int)  # cols, rows
    command_requested = Signal(str)  # app shortcuts pressed inside the terminal
    copied = Signal()
    # For the tools of the bottom panel (see LogToolbar): what they can do here and how.
    log_tools = frozenset({"search", "copy", "clear"})
    search_results = Signal(int, int)  # index of the current match (-1: none), count

    def __init__(self, parent: QWidget | None = None, directory: Path | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("terminalView")
        self._directory = directory or terminal_directory()
        self._ready = False
        self._pending_calls: list[str] = []
        self._buffer = bytearray()
        self._size = (80, 24)
        self._flush_timer = QTimer(self, singleShot=True, interval=FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush)

        self._view = QWebEngineView(self)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._page = _Page(self._view)
        settings = self._page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        # The page itself carries no colour: this is what shows until xterm.js has drawn.
        self._page.setBackgroundColor(QColor(palette.EDITOR))
        self._view.setPage(self._page)

        self._bridge = _Bridge(self)
        self._bridge.ready.connect(self._on_ready)
        self._bridge.data.connect(self.input_received)
        self._bridge.size_changed.connect(self._on_resized)
        self._bridge.command.connect(self.command_requested)
        self._bridge.copy.connect(self._copy)
        self._bridge.paste.connect(self.paste_clipboard)
        self._bridge.search_results.connect(self.search_results)
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        if xterm_installed(self._directory):
            self._view.setUrl(QUrl.fromLocalFile(str(self._directory / "index.html")))
        else:
            log.error("xterm.js is missing in %s; run scripts/fetch_xterm.py", self._directory)
            self._view.setHtml(
                f"<body style='background:{palette.EDITOR};color:{palette.TEXT};"
                "font-family:sans-serif'><p>"
                + _("Термінал не встановлено: виконайте {command}.").format(
                    command="<code>scripts/fetch_xterm.py</code>"
                )
                + "</p>"
            )

    # --- state -----------------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def size(self) -> tuple[int, int]:
        """(cols, rows) last reported by xterm.js."""
        return self._size

    # --- output ----------------------------------------------------------------------------

    def write(self, data: bytes) -> None:
        self._buffer.extend(data)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def write_text(self, text: str) -> None:
        """A message from the application itself (not the shell), e.g. "process exited"."""
        self.write(text.replace("\n", "\r\n").encode("utf-8"))

    def reset(self) -> None:
        self._buffer.clear()
        self._call("reset")

    def focus_terminal(self) -> None:
        self._view.setFocus()
        self._call("focus")

    def set_font_size(self, size: int) -> None:
        self._call("setFontSize", size)

    def clear(self) -> None:
        """Empty the scrollback; the line with the prompt stays (as Cmd+K does)."""
        self._call("clear")

    def find(self, text: str, direction: int) -> None:
        """Highlight ``text``; 0 searches again from the current match, 1 / -1 go to the
        next / previous one. The result comes as ``search_results``."""
        if not text:
            self.stop_search()
            self.search_results.emit(-1, 0)
        elif self._ready:
            self._call("find", text, direction)
        else:
            self.search_results.emit(-1, 0)

    def stop_search(self) -> None:
        self._call("stopSearch")

    def copy_text(self, done: Callable[[str], None]) -> None:
        """The whole buffer as text, scrollback included, handed to ``done``."""
        if not self._ready:
            done("")
            return
        self._page.runJavaScript("window.stmr.text()", 0, lambda text: done(text or ""))

    def paste_clipboard(self) -> None:
        text = QGuiApplication.clipboard().text()
        if text:
            self._call("paste", text)  # xterm.js adds bracketed-paste markers when asked to

    # --- internals -------------------------------------------------------------------------

    def _flush(self) -> None:
        if not self._ready or not self._buffer:
            return
        chunk = bytes(self._buffer[:MAX_BATCH])
        del self._buffer[:MAX_BATCH]
        self._call("write", base64.b64encode(chunk).decode("ascii"))
        if self._buffer:
            self._flush_timer.start()

    def _call(self, method: str, *args: object) -> None:
        script = f"window.stmr.{method}({', '.join(json.dumps(a) for a in args)});"
        if self._ready:
            self._page.runJavaScript(script)
        else:
            self._pending_calls.append(script)

    def refresh_theme(self) -> None:
        self._page.setBackgroundColor(QColor(palette.EDITOR))
        if self._ready:  # otherwise _on_ready sends the theme of that moment
            self._call("setTheme", terminal_theme())
            self._send_search_colors()

    def _send_search_colors(self) -> None:
        self._call("setSearchColors", str(palette.SEARCH_MATCH), str(palette.SEARCH_MATCH_CURRENT))

    def _on_ready(self, cols: int, rows: int) -> None:
        self._ready = True
        self._call("setTheme", terminal_theme())
        self._send_search_colors()
        pending, self._pending_calls = self._pending_calls, []
        for script in pending:
            self._page.runJavaScript(script)
        self._size = (cols, rows)
        self._flush()
        self.ready.emit(cols, rows)

    def _on_resized(self, cols: int, rows: int) -> None:
        self._size = (cols, rows)
        self.resized.emit(cols, rows)

    def _copy(self, text: str) -> None:
        if text:
            QGuiApplication.clipboard().setText(text)
            self.copied.emit()
