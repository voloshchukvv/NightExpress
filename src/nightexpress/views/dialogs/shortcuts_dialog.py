"""Help → Keyboard shortcuts: every shortcut, grouped by menu, each key drawn as a key cap.

The list is built from the menu actions themselves (MainWindow.shortcut_groups), so it cannot
drift from what the keys actually do.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, _, live
from nightexpress.views.icons import icon
from nightexpress.views.theme import palette
from nightexpress.views.widgets import tool_button

Groups = Sequence[tuple[str, Sequence[tuple[str, str]]]]

ROW_HEIGHT = 32
KEY_CAP_HEIGHT = 22
MODIFIERS = "⌃⌥⇧⌘"  # macOS writes them without a separator: ⇧⌘S
ALTERNATIVES = " / "  # between two shortcuts of one command


def key_caps(keys: str) -> list[list[str]]:
    """The keys of every alternative: "⇧⌘S" -> [["⇧", "⌘", "S"]], "Ctrl++" -> [["Ctrl", "+"]]."""
    result: list[list[str]] = []
    for alternative in keys.split(ALTERNATIVES):
        caps: list[str] = []
        for part in re.split(r"\+(?=.)", alternative):
            while len(part) > 1 and part[0] in MODIFIERS:
                caps.append(part[0])
                part = part[1:]
            caps.append(part)
        result.append(caps)
    return result


class ShortcutRow(QWidget):
    def __init__(self, command: str, keys: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("shortcutRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(ROW_HEIGHT)
        self.command = command
        self.keys = keys
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel(command, self))
        layout.addStretch(1)
        for number, caps in enumerate(key_caps(keys)):
            if number:
                separator = QLabel("/", self)
                separator.setObjectName("mutedText")
                layout.addWidget(separator)
            for cap in caps:
                label = QLabel(cap, self)
                label.setObjectName("keyCap")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setFixedHeight(KEY_CAP_HEIGHT)
                layout.addWidget(label)

    def matches(self, needle: str) -> bool:
        return needle in self.command.casefold() or needle in self.keys.casefold()


class ShortcutsDialog(QDialog):
    def __init__(self, groups: Groups, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        live(self.setWindowTitle, N_("Гарячі клавіші"))
        self.resize(520, 640)

        # On macOS the dialog is a sheet without a title bar: the title and the close button
        # are its own.
        title = QLabel(_("Гарячі клавіші"), self)
        title.setObjectName("panelTitle")
        self.close_button = tool_button("x", N_("Закрити"), self)
        self.close_button.clicked.connect(self.reject)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.close_button)

        self.search = QLineEdit(self)
        self.search.setObjectName("shortcutSearch")
        live(self.search.setPlaceholderText, N_("Пошук команди або клавіші"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(
            icon("search", palette.TEXT_MUTED), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.textChanged.connect(self._filter)

        content = QWidget(self)
        content.setObjectName("shortcutsList")
        rows_layout = QVBoxLayout(content)
        rows_layout.setContentsMargins(0, 0, 6, 0)
        rows_layout.setSpacing(0)
        self._groups: list[tuple[QLabel, list[ShortcutRow]]] = []
        for group_title, rows in groups:
            header = QLabel(group_title, content)
            header.setObjectName("shortcutGroup")
            header.setIndent(0)  # A styled frame adds an implicit text indent by default.
            rows_layout.addWidget(header)
            widgets = [ShortcutRow(command, keys, content) for command, keys in rows]
            for row in widgets:
                rows_layout.addWidget(row)
            self._groups.append((header, widgets))
        self.empty = QLabel(content)
        live(self.empty.setText, N_("Нічого не знайдено"))
        self.empty.setObjectName("mutedText")
        self.empty.setContentsMargins(10, 14, 10, 0)
        self.empty.hide()
        rows_layout.addWidget(self.empty)
        rows_layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 14, 16)
        layout.setSpacing(12)
        layout.addLayout(head)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 6, 0)
        search_row.addWidget(self.search)
        layout.addLayout(search_row)
        layout.addWidget(scroll, 1)
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        needle = text.strip().casefold()
        found = False
        for header, rows in self._groups:
            shown = [row for row in rows if row.matches(needle)]
            for row in rows:
                row.setVisible(row in shown)
            header.setVisible(bool(shown))
            found = found or bool(shown)
        self.empty.setVisible(not found)

    def rows(self) -> dict[str, dict[str, str]]:
        """{group: {command: keys}} of the rows that pass the search (for tests)."""
        return {
            header.text(): {row.command: row.keys for row in rows if not row.isHidden()}
            for header, rows in self._groups
            if not header.isHidden()
        }
