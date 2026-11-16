"""Ask which board a project targets (e.g. a Cargo project opened for the first time)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from nightexpress.i18n import N_, _, live
from nightexpress.models.entities import BoardDefinition, ChipDefinition
from nightexpress.views.board_picker import BoardPicker, SearchFunction
from nightexpress.views.theme import palette


class BoardChooserDialog(QDialog):
    def __init__(
        self,
        message: str,
        boards: Iterable[BoardDefinition],
        chips: Mapping[str, ChipDefinition],
        search: SearchFunction | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        live(self.setWindowTitle, N_("Виберіть плату"))
        self.resize(560, 480)
        self.picker = BoardPicker(self)
        self.picker.set_boards(boards, chips, search)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        choose = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        live(choose.setText, N_("Вибрати"))
        palette.set_button_role(choose, "primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Скасувати"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.picker.board_changed.connect(self._update_ok)
        layout = QVBoxLayout(self)
        label = QLabel(message, self)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(self.picker, 1)
        layout.addWidget(self.buttons)
        self._update_ok(self.picker.current_board_id)

    @property
    def board_id(self) -> str | None:
        return self.picker.current_board_id

    def _update_ok(self, board_id: object) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(board_id is not None)
