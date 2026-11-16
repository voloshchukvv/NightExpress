"""Project → Add peripheral: choose a module and its pins, see how to wire it and what will
change.

The dialog only shows; the controller makes the plan for the chosen module and pins
(``show_plan``) or says why it cannot be added (``show_problem``). The SCL/SDA rows of the wiring
are lists of the pins the chip allows: a change asks the controller for a new plan
(``pins_changed``).
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, _, live
from nightexpress.models.entities import I2cPinChoice
from nightexpress.views.theme import palette


class PeripheralDialog(QDialog):
    peripheral_selected = Signal(str)  # peripheral id
    pins_changed = Signal(object)  # I2cPinChoice

    def __init__(self, peripherals: Sequence[tuple[str, str]], parent: QWidget | None = None):
        """``peripherals``: (id, name) in the order to show."""
        super().__init__(parent)
        live(self.setWindowTitle, N_("Додати периферію"))
        self.resize(720, 460)

        self.list = QListWidget(self)
        self.list.setFixedWidth(200)
        for peripheral_id, name in peripherals:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, peripheral_id)
            self.list.addItem(item)

        self.title = QLabel(self)
        self.title.setObjectName("sectionTitle")
        self.description = QLabel(self)
        self.description.setWordWrap(True)
        self.wiring_title = QLabel(self)
        live(self.wiring_title.setText, N_("Підключення"))
        self.wiring_title.setObjectName("targetSection")
        self.wiring = QGridLayout()
        self.wiring.setHorizontalSpacing(10)
        self.wiring.setVerticalSpacing(6)
        self.wiring.setColumnStretch(3, 1)
        self._wiring_cells: list[QWidget] = []
        self._choices: list[I2cPinChoice] = []
        self._current: I2cPinChoice | None = None
        self.scl_combo: QComboBox | None = None
        self.sda_combo: QComboBox | None = None
        self.changes_title = QLabel(self)
        live(self.changes_title.setText, N_("Що зміниться в проєкті"))
        self.changes_title.setObjectName("targetSection")
        self.changes = QLabel(self)
        self.changes.setObjectName("mutedText")
        self.changes.setWordWrap(True)
        self.problem = QLabel(self)
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(f"color: {palette.ERROR}")

        details = QVBoxLayout()
        details.setSpacing(10)
        details.addWidget(self.title)
        details.addWidget(self.description)
        details.addSpacing(6)
        details.addWidget(self.wiring_title)
        details.addLayout(self.wiring)
        details.addSpacing(6)
        details.addWidget(self.changes_title)
        details.addWidget(self.changes)
        details.addWidget(self.problem)
        details.addStretch(1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.add_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        live(self.add_button.setText, N_("Додати"))
        palette.set_button_role(self.add_button, "primary")
        live(self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText, N_("Скасувати"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self.list)
        body.addLayout(details, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(self.buttons)

        self.list.currentItemChanged.connect(self._on_current_changed)
        if self.list.count():
            self.list.setCurrentRow(0)

    @property
    def peripheral_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def set_pin_choices(self, choices: Sequence[I2cPinChoice], current: I2cPinChoice) -> None:
        """The ways the chip can wire the bus, and the one shown; call before show_plan."""
        self._choices, self._current = list(choices), current

    def show_plan(
        self,
        title: str,
        description: str,
        wiring: Sequence[tuple[str, str, str | None]],
        changed_files: Sequence[str],
        created_files: Sequence[str],
    ) -> None:
        """``wiring``: (module pin, board pin, role); role "scl"/"sda" rows become pin lists."""
        self.title.setText(title)
        self.description.setText(description)
        self._show_wiring(wiring)
        lines = [_("Новий файл: {path}").format(path=path) for path in created_files]
        lines += [_("Зміниться: {path}").format(path=path) for path in changed_files]
        self.changes.setText("<br>".join(lines))
        self.problem.hide()
        self.add_button.setEnabled(True)

    def show_problem(
        self,
        title: str,
        description: str,
        problem: str,
        wiring: Sequence[tuple[str, str, str | None]] = (),
    ) -> None:
        """Why it cannot be added; ``wiring`` keeps the pin lists, so other pins can be chosen."""
        self.title.setText(title)
        self.description.setText(description)
        self._show_wiring(wiring)
        self.changes.setText("")
        self.problem.setText(problem)
        self.problem.show()
        self.add_button.setEnabled(False)

    def _show_wiring(self, wiring: Sequence[tuple[str, str, str | None]]) -> None:
        for cell in self._wiring_cells:
            self.wiring.removeWidget(cell)
            cell.hide()
            cell.deleteLater()  # a list may be the sender of the signal that got us here
        self._wiring_cells = []
        self.scl_combo = self.sda_combo = None
        for row, (module_pin, board_pin, role) in enumerate(wiring):
            target: QWidget
            if role in ("scl", "sda") and self._current is not None and len(self._choices) > 1:
                target = self._pin_combo(role)
            else:
                target = QLabel(board_pin, self)
            cells = (QLabel(module_pin, self), QLabel("→", self), target)
            cells[0].setObjectName("wiringPin")
            cells[1].setObjectName("mutedText")
            for column, cell in enumerate(cells):
                self.wiring.addWidget(cell, row, column)
            self._wiring_cells.extend(cells)
        self.wiring_title.setVisible(bool(wiring))

    def _pin_combo(self, role: str) -> QComboBox:
        current = self._current
        assert current is not None
        combo = QComboBox(self)
        combo.setMinimumWidth(150)
        if role == "scl":
            pins = list(dict.fromkeys((c.scl_pin, c.peripheral) for c in self._choices))
            selected = (current.scl_pin, current.peripheral)
            self.scl_combo = combo
        else:
            pins = [
                (c.sda_pin, c.peripheral)
                for c in self._choices
                if (c.peripheral, c.scl_pin) == (current.peripheral, current.scl_pin)
            ]
            selected = (current.sda_pin, current.peripheral)
            self.sda_combo = combo
        for pin, peripheral in pins:
            combo.addItem(f"{pin} · {peripheral}", (pin, peripheral))
        combo.setCurrentIndex(max(pins.index(selected), 0) if selected in pins else 0)
        combo.activated.connect(lambda _i, r=role, c=combo: self._on_pin_chosen(r, c))
        return combo

    def _on_pin_chosen(self, role: str, combo: QComboBox) -> None:
        pin, peripheral = combo.currentData()
        current = self._current
        assert current is not None
        if role == "scl":
            same = [c for c in self._choices if (c.peripheral, c.scl_pin) == (peripheral, pin)]
            # Keep the SDA pin when it still fits, else the first one that does.
            chosen = next((c for c in same if c.sda_pin == current.sda_pin), same[0])
        else:
            chosen = next(
                c
                for c in self._choices
                if (c.peripheral, c.scl_pin, c.sda_pin) == (peripheral, current.scl_pin, pin)
            )
        if chosen != current:
            self._current = chosen
            self.pins_changed.emit(chosen)

    @property
    def pins(self) -> I2cPinChoice | None:
        return self._current

    def _on_current_changed(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is not None:
            self.peripheral_selected.emit(current.data(Qt.ItemDataRole.UserRole))
