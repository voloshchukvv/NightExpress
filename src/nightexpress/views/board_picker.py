"""Board search + list + details; used by the new project wizard and the board chooser."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from nightexpress.i18n import N_, _, live
from nightexpress.models.entities import (
    BoardDefinition,
    ChipDefinition,
    SupportStatus,
)

SearchFunction = Callable[[str], list[BoardDefinition]]


def describe_board(board: BoardDefinition, chip: ChipDefinition | None) -> str:
    lines = [f"<b>{board.display_name}</b>"]
    if chip is not None:
        facts = [chip.core, f"{chip.flash_kb} KB flash", f"{chip.ram_kb} KB RAM"]
        if not board.display_name.startswith(chip.id):  # the name says it already
            facts.insert(0, chip.id)
        lines.append(" · ".join(facts))
    if board.status is SupportStatus.EXPERIMENTAL:
        lines.append(_("<i>Плата ще не перевірена на залізі.</i>"))
    return "<br>".join(lines)


class BoardPicker(QWidget):
    board_changed = Signal(object)  # board id or None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._boards: list[BoardDefinition] = []
        self._chips: Mapping[str, ChipDefinition] = {}
        self._search: SearchFunction | None = None

        self.search_edit = QLineEdit(self)
        live(
            self.search_edit.setPlaceholderText,
            N_("Пошук: назва плати або маркування чипа (STM32F103C6T6A)"),
        )
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)
        self.list = QListWidget(self)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.details = QLabel(self)
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setMinimumHeight(90)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.empty_label = QLabel(self)
        live(self.empty_label.setText, N_("Плату не знайдено. Перевірте маркування на чипі."))
        self.empty_label.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.details)

    def set_boards(
        self,
        boards: Iterable[BoardDefinition],
        chips: Mapping[str, ChipDefinition],
        search: SearchFunction | None = None,
    ) -> None:
        self._boards = list(boards)
        self._chips = chips
        self._search = search
        self._show(self._boards)

    @property
    def current_board_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select(self, board_id: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == board_id:
                self.list.setCurrentRow(row)
                return

    def _on_search(self, text: str) -> None:
        if not text.strip():
            self._show(self._boards)
        elif self._search is not None:
            self._show(self._search(text))
        else:
            needle = text.lower()
            self._show(
                [
                    b
                    for b in self._boards
                    if any(needle in name.lower() for name in (b.display_name, *b.nicknames))
                ]
            )

    def _show(self, boards: list[BoardDefinition]) -> None:
        previous = self.current_board_id
        self.list.clear()
        for board in boards:
            item = QListWidgetItem(board.display_name)
            item.setData(Qt.ItemDataRole.UserRole, board.id)
            self.list.addItem(item)
        self.empty_label.setVisible(not boards)
        if previous is not None:
            self.select(previous)
        if self.list.currentRow() < 0 and boards:
            self.list.setCurrentRow(0)
        if not boards:
            self.details.clear()
            self.board_changed.emit(None)

    def _on_current_changed(self, current: QListWidgetItem | None, _previous: object) -> None:
        board_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        board = next((b for b in self._boards if b.id == board_id), None)
        self.details.setText(describe_board(board, self._chips.get(board.chip_id)) if board else "")
        self.board_changed.emit(board_id)
