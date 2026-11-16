"""Tools of the bottom panel: search, line wrap, copy all, clear.

One toolbar sits in the corner of the tab row and acts on the current tab. A text log
(Build, Flashing, UART) has all four. Another tab says in ``log_tools`` which of them it has
and does them itself (see ``ToolTarget``): the terminal searches, copies and clears, the
problems list only copies. Tools a tab does not have are not shown.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QToolButton,
    QWidget,
)

from nightexpress.i18n import N_, Text, _, live, pgettext
from nightexpress.views.theme import palette
from nightexpress.views.widgets import tool_button

LogView = QTextEdit | QPlainTextEdit
TOOLS = frozenset({"search", "wrap", "copy", "clear"})


class ToolTarget(Protocol):
    """A tab that is not a text log. With "search" in ``log_tools`` it also has a
    ``search_results(int, int)`` signal (current match or -1, count), ``find(text,
    direction)`` and ``stop_search()``; "clear" is done by the owner (``clear_requested``)."""

    log_tools: frozenset[str]

    def copy_text(self, done: Callable[[str], None]) -> None: ...

    def setFocus(self) -> None: ...  # noqa: N802 - Qt's name


Target = LogView | ToolTarget
MAX_MATCHES = 2000
REFRESH_DELAY_MS = 150  # a growing log is searched again at most this often


class _SearchField(QLineEdit):
    next_requested = Signal()
    previous_requested = Signal()
    close_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.previous_requested.emit()
            else:
                self.next_requested.emit()
        elif event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
        else:
            super().keyPressEvent(event)


class LogToolbar(QWidget):
    clear_requested = Signal()  # the owner knows how to clear the current log
    copied = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("logToolbar")
        self._target: Target | None = None
        self._tools: frozenset[str] = frozenset()
        self._matches: list[QTextCursor] = []
        self._current = -1
        self._found = (-1, 0)  # (current, count) reported by a ToolTarget
        self._refresh_timer = QTimer(self, singleShot=True, interval=REFRESH_DELAY_MS)
        self._refresh_timer.timeout.connect(self._refresh)

        self.search_field = _SearchField(self)
        self.search_field.setObjectName("logSearch")
        live(self.search_field.setPlaceholderText, N_("Пошук у журналі"))
        self.search_field.setFixedWidth(178)
        self.search_field.textChanged.connect(self._search)
        self.search_field.next_requested.connect(lambda: self.step(1))
        self.search_field.previous_requested.connect(lambda: self.step(-1))
        self.search_field.close_requested.connect(self.close_search)
        self.match_label = QLabel("", self)
        self.match_label.setObjectName("mutedText")
        self.previous_button = self._button("chevron-up", N_("Попередній збіг (Shift+Enter)"))
        self.next_button = self._button("chevron-down", N_("Наступний збіг (Enter)"))
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self._search_widgets = (
            self.search_field,
            self.match_label,
            self.previous_button,
            self.next_button,
        )

        self.search_button = self._button("search", N_("Пошук у журналі"), checkable=True)
        self.search_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.wrap_button = self._button("wrap-text", N_("Переносити довгі рядки"), checkable=True)
        self.wrap_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.copy_button = self._button("copy", N_("Скопіювати все"))
        self.clear_button = self._button("trash-2", N_("Очистити"))
        self.search_button.toggled.connect(self._on_search_toggled)
        self.wrap_button.toggled.connect(self._apply_wrap)
        self.copy_button.clicked.connect(self.copy_all)
        self.clear_button.clicked.connect(self.clear_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(4)
        for widget in (
            *self._search_widgets,
            self.search_button,
            self.wrap_button,
            self.copy_button,
            self.clear_button,
        ):
            layout.addWidget(widget)
        for widget in self._search_widgets:
            widget.hide()
        self.set_target(None)

    def _button(self, icon_name: str, tooltip: Text, checkable: bool = False) -> QToolButton:
        button = tool_button(icon_name, tooltip, self)
        button.setCheckable(checkable)
        return button

    # --- target ----------------------------------------------------------------------------

    @property
    def target(self) -> Target | None:
        return self._target

    @property
    def tools(self) -> frozenset[str]:
        """What the buttons can do on the current tab."""
        return self._tools

    def _text_log(self) -> LogView | None:
        target = self._target
        return target if isinstance(target, QTextEdit | QPlainTextEdit) else None

    def set_target(self, target: Target | None) -> None:
        """The tab the buttons act on; None (nothing to act on) hides them."""
        previous, log = self._target, self._text_log()
        if previous is not None and previous is not target:
            if log is not None:
                log.setExtraSelections([])
                log.document().contentsChanged.disconnect(self._on_log_changed)
            elif "search" in self._tools:
                previous.search_results.disconnect(self._on_found)  # type: ignore[union-attr]
                previous.stop_search()  # type: ignore[union-attr]
        is_text = isinstance(target, QTextEdit | QPlainTextEdit)
        tools = TOOLS if is_text else getattr(target, "log_tools", frozenset())
        if target is not None and target is not previous:
            if is_text:
                document = target.document()  # type: ignore[union-attr]
                document.contentsChanged.connect(self._on_log_changed)
            elif "search" in tools:
                target.search_results.connect(self._on_found)  # type: ignore[union-attr]
        self._target, self._tools = target, tools
        self._found = (-1, 0)
        buttons = (
            (self.search_button, "search"),
            (self.wrap_button, "wrap"),
            (self.copy_button, "copy"),
            (self.clear_button, "clear"),
        )
        for button, tool in buttons:
            button.setVisible(tool in tools)
            button.setEnabled(tool in tools)
        if "search" not in tools:
            self.search_button.setChecked(False)
        log = self._text_log()
        if log is not None:
            self.wrap_button.blockSignals(True)
            self.wrap_button.setChecked(self.wraps(log))
            self.wrap_button.blockSignals(False)
        if self.search_button.isChecked():
            self._search()

    @staticmethod
    def wraps(target: LogView) -> bool:
        return target.lineWrapMode() != type(target).LineWrapMode.NoWrap

    def _apply_wrap(self, wrap: bool) -> None:
        target = self._text_log()
        if target is not None:
            modes = type(target).LineWrapMode
            target.setLineWrapMode(modes.WidgetWidth if wrap else modes.NoWrap)

    def copy_all(self) -> None:
        log = self._text_log()
        if log is not None:
            self._copy(log.toPlainText())
        elif "copy" in self._tools:
            self._target.copy_text(self._copy)  # type: ignore[union-attr]

    def _copy(self, text: str) -> None:
        if text:
            QGuiApplication.clipboard().setText(text)
            self.copied.emit()

    # --- search ----------------------------------------------------------------------------

    def open_search(self) -> None:
        if "search" not in self._tools:
            return
        self.search_button.setChecked(True)
        log = self._text_log()
        selected = log.textCursor().selectedText() if log is not None else ""
        if selected and " " not in selected:
            self.search_field.setText(selected)
        self.search_field.setFocus()
        self.search_field.selectAll()

    def close_search(self) -> None:
        self.search_button.setChecked(False)

    @property
    def match_count(self) -> int:
        return len(self._matches) if self._text_log() is not None else self._found[1]

    @property
    def current_match(self) -> int:
        """0-based index of the highlighted match, -1 when there is none."""
        return self._current if self._text_log() is not None else self._found[0]

    def step(self, direction: int) -> None:
        if self._text_log() is None:
            if "search" in self._tools and self.search_field.text():
                self._target.find(self.search_field.text(), direction)  # type: ignore[union-attr]
            return
        if self._matches:
            self._current = (self._current + direction) % len(self._matches)
            self._show_matches()

    def _on_search_toggled(self, on: bool) -> None:
        for widget in self._search_widgets:
            widget.setVisible(on)
        if on:
            self._search()
            self.search_field.setFocus()
        else:
            self._matches, self._current = [], -1
            log = self._text_log()
            if log is not None:
                log.setExtraSelections([])
            elif "search" in self._tools:
                self._target.stop_search()  # type: ignore[union-attr]
            if self._target is not None:
                self._target.setFocus()

    def _on_log_changed(self) -> None:
        if self.search_button.isChecked() and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _refresh(self) -> None:
        """The log grew or was cleared: count again, but leave the view where it is."""
        if self.search_button.isChecked():
            self._search(reveal=False)

    def _search(self, reveal: bool = True) -> None:
        # Where the current match is now (its cursor follows the edits of the document).
        if self._text_log() is None:
            if "search" in self._tools:
                self._target.find(self.search_field.text(), 0)  # type: ignore[union-attr]
            return
        position = self._matches[self._current].selectionStart() if self._current >= 0 else None
        self._matches, self._current = [], -1
        target, text = self._text_log(), self.search_field.text()
        if target is not None and text:
            document: QTextDocument = target.document()
            cursor = document.find(text, 0)
            while not cursor.isNull() and len(self._matches) < MAX_MATCHES:
                self._matches.append(cursor)
                cursor = document.find(text, cursor)
            if self._matches:
                # Start from the first match at or after the place the user is looking at
                # (after a refresh: at or after the match that was current).
                if reveal or position is None:
                    position = target.textCursor().selectionStart()
                self._current = next(
                    (i for i, m in enumerate(self._matches) if m.selectionStart() >= position), 0
                )
        self._show_matches(reveal)

    def _on_found(self, current: int, count: int) -> None:
        self._found = (current, count)
        self._show_count(current, count)

    def _show_matches(self, reveal: bool = True) -> None:
        target = self._text_log()
        if target is None:
            return
        selections = []
        for i, match in enumerate(self._matches):
            selection = QTextEdit.ExtraSelection()
            selection.cursor = match  # type: ignore[attr-defined]
            current = i == self._current
            color = palette.SEARCH_MATCH_CURRENT if current else palette.SEARCH_MATCH
            selection.format.setBackground(QColor(color))  # type: ignore[attr-defined]
            selections.append(selection)
        target.setExtraSelections(selections)
        if self._current >= 0 and reveal:
            view = QTextCursor(self._matches[self._current])
            view.clearSelection()
            target.setTextCursor(view)
            target.ensureCursorVisible()
        self._show_count(self._current, len(self._matches))

    def _show_count(self, current_index: int, total: int) -> None:
        if current_index >= 0 and total:
            current = current_index + 1
            live(
                self.match_label.setText,
                lambda: _("{current} з {total}").format(current=current, total=total),
            )
        else:
            searching = bool(self.search_field.text())
            live(self.match_label.setText, lambda: pgettext("search", "немає") if searching else "")
