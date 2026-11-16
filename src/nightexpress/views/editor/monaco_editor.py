"""Monaco Editor (the editor of VS Code) embedded through QtWebEngine.

One Monaco instance hosts one text model per open file, so every file keeps its own undo
history. The widget mirrors the current text of every document on the Python side, which makes
``text()`` synchronous (needed for saving) even though JavaScript calls are asynchronous.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from nightexpress.i18n import _
from nightexpress.models.entities import Diagnostic
from nightexpress.models.entities.theme import Theme
from nightexpress.views.theme import palette

log = logging.getLogger(__name__)

LANGUAGES = {
    ".rs": "rust",
    ".toml": "toml",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}

COMMANDS = (
    "save",
    "save_all",
    "format",
    "verify",
    "upload",
    "serial_monitor",
    "quick_open",
    "close_tab",
    "next_tab",
    "previous_tab",
    "toggle_bottom",
    "focus_tree",
    "zoom_in",
    "zoom_out",
    "zoom_reset",
    "next_problem",
    "previous_problem",
)


class EditorAction(Enum):
    """Monaco action ids used by the Edit menu."""

    UNDO = "undo"
    REDO = "redo"
    CUT = "editor.action.clipboardCutAction"
    COPY = "editor.action.clipboardCopyAction"
    PASTE = "editor.action.clipboardPasteAction"
    SELECT_ALL = "editor.action.selectAll"
    FIND = "actions.find"
    REPLACE = "editor.action.startFindReplaceAction"
    TOGGLE_COMMENT = "editor.action.commentLine"
    GO_TO_LINE = "editor.action.gotoLine"
    COMMAND_PALETTE = "editor.action.quickCommand"


_NATIVE_ACTIONS = {
    EditorAction.CUT: QWebEnginePage.WebAction.Cut,
    EditorAction.COPY: QWebEnginePage.WebAction.Copy,
    EditorAction.PASTE: QWebEnginePage.WebAction.Paste,
}


def language_for(path: Path) -> str:
    return LANGUAGES.get(path.suffix.lower(), "plaintext")


def editor_directory() -> Path:
    return Path(str(resources.files("nightexpress") / "resources" / "editor"))


def monaco_installed(directory: Path | None = None) -> bool:
    base = directory or editor_directory()
    return (base / "monaco" / "vs" / "loader.js").is_file()


def monaco_theme(theme: Theme) -> dict[str, Any]:
    """``theme`` as ``monaco.editor.defineTheme`` takes it."""
    return {
        "base": "vs-dark" if theme.dark else "vs",
        "inherit": True,
        "rules": [
            {"token": token, "foreground": color.removeprefix("#")}
            for token, color in theme.editor_tokens.items()
        ],
        "colors": {
            "menu.background": theme.color("surface"),
            "menu.foreground": theme.color("text"),
            "menu.border": theme.color("outline"),
            "menu.selectionBackground": theme.color("accent_soft"),
            "menu.selectionForeground": theme.color("text"),
            "menu.separatorBackground": theme.color("border"),
            # The command palette looks like the application's own quick open (⌘P).
            "quickInput.background": theme.color("surface"),
            "quickInput.foreground": theme.color("text"),
            "quickInputTitle.background": theme.color("surface"),
            "widget.border": theme.color("border"),
            "input.background": theme.color("editor"),
            "input.foreground": theme.color("text"),
            "input.border": theme.color("control_border"),
            "input.placeholderForeground": theme.color("text_muted"),
            "focusBorder": theme.color("focus"),
            "quickInputList.focusBackground": theme.color("accent_soft"),
            "quickInputList.focusForeground": theme.color("text"),
            "quickInputList.focusIconForeground": theme.color("text"),
            "quickInputList.focusHighlightForeground": theme.color("accent_bright"),
            "list.hoverBackground": theme.color("surface_alt"),
            "list.highlightForeground": theme.color("accent_bright"),
            "keybindingLabel.background": theme.color("surface_alt"),
            "keybindingLabel.foreground": theme.color("text_muted"),
            "keybindingLabel.border": theme.color("border"),
            "keybindingLabel.bottomBorder": theme.color("border"),
            "pickerGroup.border": theme.color("border"),
            "pickerGroup.foreground": theme.color("text_muted"),
            **theme.editor_colors,
        },
    }


class _Bridge(QObject):
    """Object published to JavaScript as ``bridge``; slot names are the JS-facing API."""

    ready = Signal()
    content_changed = Signal(str, str, int)
    cursor_changed = Signal(int, int)
    command = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.theme = "{}"  # JSON for monaco.editor.defineTheme, set by the editor widget

    @Slot(result=str)
    def initialTheme(self) -> str:  # noqa: N802 - JS-facing name
        return self.theme

    @Slot()
    def editorReady(self) -> None:  # noqa: N802 - JS-facing name
        self.ready.emit()

    @Slot(str, str, int)
    def contentChanged(self, path: str, text: str, version: int) -> None:  # noqa: N802 - JS-facing name
        self.content_changed.emit(path, text, version)

    @Slot(int, int)
    def cursorChanged(self, line: int, column: int) -> None:  # noqa: N802 - JS-facing name
        self.cursor_changed.emit(line, column)

    @Slot(str)
    def commandRequested(self, name: str) -> None:  # noqa: N802 - JS-facing name
        self.command.emit(name)


class _Page(QWebEnginePage):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.console: list[tuple[str, str]] = []

    def javaScriptConsoleMessage(  # noqa: N802 - Qt override
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        name = level.name.removesuffix("MessageLevel").lower()
        self.console.append((name, message))
        log.debug("monaco %s: %s (%s:%s)", name, message, source, line)


class MonacoEditor(QWidget):
    ready = Signal()
    content_changed = Signal(object, str)  # (Path, text) after every user edit
    cursor_moved = Signal(int, int)  # 1-based line, column
    command_requested = Signal(str)  # one of COMMANDS, from shortcuts pressed inside the editor

    def __init__(self, parent: QWidget | None = None, directory: Path | None = None) -> None:
        super().__init__(parent)
        self._directory = directory or editor_directory()
        self._ready = False
        self._pending: list[str] = []
        self._texts: dict[Path, str] = {}
        self._active: Path | None = None
        self._markers: dict[Path, list[dict[str, Any]]] = {}

        self._view = QWebEngineView(self)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)  # Monaco has its own
        self._page = _Page(self._view)
        settings = self._page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        # The page itself carries no colour: this is what shows until Monaco has drawn.
        self._page.setBackgroundColor(QColor(palette.EDITOR))
        self._view.setPage(self._page)

        self._bridge = _Bridge(self)
        self._bridge.theme = json.dumps(monaco_theme(palette.current()))
        self._bridge.ready.connect(self._on_ready)
        self._bridge.content_changed.connect(self._on_content_changed)
        self._bridge.cursor_changed.connect(self.cursor_moved)
        self._bridge.command.connect(self._on_command)
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        if monaco_installed(self._directory):
            self._view.setUrl(QUrl.fromLocalFile(str(self._directory / "index.html")))
        else:
            log.error("Monaco is missing in %s; run scripts/fetch_monaco.py", self._directory)
            self._view.setHtml(
                f"<body style='background:{palette.EDITOR};color:{palette.TEXT};"
                "font-family:sans-serif'><p>"
                + _("Редактор не встановлено: виконайте {command}.").format(
                    command="<code>scripts/fetch_monaco.py</code>"
                )
                + "</p>"
            )

    # --- state -----------------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def documents(self) -> tuple[Path, ...]:
        return tuple(self._texts)

    @property
    def active_document(self) -> Path | None:
        return self._active

    @property
    def console_messages(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._page.console)

    def text(self, path: Path) -> str | None:
        return self._texts.get(path)

    # --- documents -------------------------------------------------------------------------

    def open_document(self, path: Path, text: str) -> None:
        new = path not in self._texts
        if new:
            self._texts[path] = text
        self._active = path
        self._call("openDocument", str(path), text, language_for(path))
        if new and self._markers.get(path):
            self._call("setMarkers", str(path), self._markers[path])

    def show_document(self, path: Path) -> None:
        if path not in self._texts:
            raise KeyError(f"{path} is not open")
        self._active = path
        self._call("showDocument", str(path))

    def replace_text(self, path: Path, text: str) -> None:
        if path not in self._texts:
            raise KeyError(f"{path} is not open")
        self._texts[path] = text
        self._call("replaceText", str(path), text)

    def close_document(self, path: Path) -> None:
        if self._texts.pop(path, None) is None:
            return
        if self._active == path:
            self._active = None
        self._call("closeDocument", str(path))

    # --- decorations / navigation ----------------------------------------------------------

    def set_diagnostics(self, diagnostics: Iterable[Diagnostic]) -> None:
        """Show compiler diagnostics as squiggles; clears markers of documents without any."""
        by_file: dict[Path, list[dict[str, Any]]] = defaultdict(list)
        for d in diagnostics:
            if d.file is not None and d.line is not None:
                by_file[d.file].append(
                    {
                        "level": d.level.value,
                        "message": d.message,
                        "code": d.code,
                        "line": d.line,
                        "column": d.column or 1,
                    }
                )
        self._markers = dict(by_file)
        for path in self._texts:
            self._call("setMarkers", str(path), by_file.get(path, []))

    def reveal(self, path: Path, line: int, column: int = 1) -> None:
        if path not in self._texts:
            raise KeyError(f"{path} is not open")
        self._active = path
        self._call("reveal", str(path), line, column)

    def set_theme(self, theme: Theme | None = None) -> None:
        """Recolour the editor for ``theme`` (the application's current one by default)."""
        theme = theme or palette.current()
        self._page.setBackgroundColor(QColor(theme.color("editor")))
        self._call("setTheme", monaco_theme(theme))

    def refresh_theme(self) -> None:
        self.set_theme()

    def set_font_size(self, size: int) -> None:
        self._call("setFontSize", size)

    def set_bracket_pair_guides(self, enabled: bool) -> None:
        self._call("setBracketPairGuides", enabled)

    def set_minimap(self, enabled: bool) -> None:
        """Show the code overview at the right edge (still hidden while the editor is narrow)."""
        self._call("setMinimap", enabled)

    def focus_editor(self) -> None:
        self._call("focus")

    def trigger_action(self, action: EditorAction) -> None:
        native = _NATIVE_ACTIONS.get(action)
        if native is not None:
            # Clipboard access is handled by Chromium itself, not by Monaco.
            self._view.setFocus()
            self._page.triggerAction(native)
        else:
            self._call("trigger", action.value)

    # --- internals -------------------------------------------------------------------------

    def _call(self, method: str, *args: object) -> None:
        script = f"window.stmr.{method}({', '.join(json.dumps(a) for a in args)});"
        if self._ready:
            self._page.runJavaScript(script)
        else:
            self._pending.append(script)

    def _on_ready(self) -> None:
        self._ready = True
        pending, self._pending = self._pending, []
        for script in pending:
            self._page.runJavaScript(script)
        self.ready.emit()

    def _on_content_changed(self, path: str, text: str, _version: int) -> None:
        key = Path(path)
        if key in self._texts and self._texts[key] != text:
            self._texts[key] = text
            self.content_changed.emit(key, text)

    def _on_command(self, name: str) -> None:
        if name in COMMANDS:
            self.command_requested.emit(name)
