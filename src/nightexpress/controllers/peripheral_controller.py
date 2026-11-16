"""PeripheralController: Build → Add peripheral — a module (MPU-6050…) joins the open project.

The dialog shows how to wire the chosen module to this board, with the pins the chip allows for
its bus to choose from, and which files change; on "Add" the driver is copied into the project and
main.rs, Cargo.toml and .cargo/config.toml are edited (services/peripherals.py). Open files are
saved first, so nothing is overwritten.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog

from nightexpress.i18n import _
from nightexpress.models.entities import BoardDefinition, ChipPins, I2cPinChoice
from nightexpress.models.state import ProjectModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.file_service import FileService, FileServiceError
from nightexpress.services.peripherals import (
    MAIN_RS,
    InstallPlan,
    PeripheralDefinition,
    PeripheralError,
    apply,
    default_pins,
    existing_i2c_buses,
    load_peripherals,
    plan_install,
    read_main,
    wiring_for,
)
from nightexpress.services.pin_catalog import load_chip_pins
from nightexpress.views.dialogs.peripheral_dialog import PeripheralDialog
from nightexpress.views.main_window import MainWindow
from nightexpress.views.toast import ToastKind


class PeripheralController(QObject):
    def __init__(
        self,
        window: MainWindow,
        project_model: ProjectModel,
        catalog: BoardCatalog,
        save_all: Callable[[], bool],
        refresh_files: Callable[[], None],
        open_file: Callable[[Path], object],
        is_busy: Callable[[], bool],
        files: FileService | None = None,
        peripherals: list[PeripheralDefinition] | None = None,
        chip_pins: dict[str, ChipPins] | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._project = project_model
        self._catalog = catalog
        self._save_all = save_all
        self._refresh_files = refresh_files
        self._open_file = open_file
        self._is_busy = is_busy
        self._files = files or FileService()
        self._peripherals = {p.id: p for p in peripherals or load_peripherals()}
        self._chip_pins = chip_pins if chip_pins is not None else load_chip_pins()
        self.dialog: PeripheralDialog | None = None
        window.add_peripheral_requested.connect(self.open)

    def open(self) -> bool:
        """Show the dialog; True if a peripheral was added."""
        config = self._project.config
        if config is None:
            return False
        if self._is_busy():
            self._window.show_message(
                _("Спершу дочекайтеся кінця збірки чи прошивання або зупиніть їх")
            )
            return False
        try:
            board = self._catalog.get_board(config.board_id)
        except KeyError:
            return False
        dialog = PeripheralDialog(
            [(p.id, p.name) for p in self._peripherals.values()], self._window
        )
        self.dialog = dialog
        dialog.peripheral_selected.connect(
            lambda pid: self._select(dialog, config.path, board, pid)
        )
        dialog.pins_changed.connect(
            lambda pins: self._show(dialog, config.path, board, dialog.peripheral_id, pins)
        )
        if dialog.peripheral_id is not None:
            self._select(dialog, config.path, board, dialog.peripheral_id)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        peripheral_id, pins = dialog.peripheral_id, dialog.pins
        self.dialog = None
        dialog.deleteLater()
        if not accepted or peripheral_id is None:
            return False
        return self.add(peripheral_id, pins)

    def pin_choices(self, board: BoardDefinition) -> list[I2cPinChoice]:
        """Every way the board's chip can wire an I2C bus (embassy's chip data)."""
        pins = self._chip_pins.get(self._catalog.chip_for(board).id)
        return pins.i2c_choices() if pins is not None else []

    def initial_pins(
        self, project: Path, board: BoardDefinition, peripheral: PeripheralDefinition
    ) -> I2cPinChoice:
        """A bus another module already uses (they share it), else the board's usual one."""
        try:
            buses = existing_i2c_buses(read_main(project, self._files))
        except (PeripheralError, FileServiceError):
            buses = []
        return buses[0] if buses else default_pins(board, peripheral)

    def add(self, peripheral_id: str, pins: I2cPinChoice | None = None) -> bool:
        """Add the peripheral to the open project (the dialog's "Add") on ``pins`` (default:
        what the dialog would offer first)."""
        config = self._project.config
        if config is None:
            return False
        peripheral = self._peripherals[peripheral_id]
        if not self._save_all():
            return False  # main.rs on disk must be what the user sees
        try:
            board = self._catalog.get_board(config.board_id)
            pins = pins or self.initial_pins(config.path, board, peripheral)
            plan = plan_install(config.path, peripheral, board, self._files, pins)
            apply(config.path, plan, self._files)
        except (PeripheralError, FileServiceError, KeyError) as exc:
            self._window.show_error(
                _("Не вдалося додати {name}").format(name=peripheral.name), str(exc)
            )
            return False
        self._refresh_files()
        self._open_file(config.path / MAIN_RS)
        self._report(plan)
        return True

    def _select(
        self, dialog: PeripheralDialog, project: Path, board: BoardDefinition, pid: str
    ) -> None:
        peripheral = self._peripherals[pid]
        try:
            pins = self.initial_pins(project, board, peripheral)
        except PeripheralError as exc:
            dialog.show_problem(peripheral.name, peripheral.description, str(exc))
            return
        choices = self.pin_choices(board)
        dialog.set_pin_choices(choices if pins in choices else [pins, *choices], pins)
        self._show(dialog, project, board, pid, pins)

    def _show(
        self,
        dialog: PeripheralDialog,
        project: Path,
        board: BoardDefinition,
        pid: str | None,
        pins: I2cPinChoice,
    ) -> None:
        if pid is None:
            return
        peripheral = self._peripherals[pid]
        try:
            plan = plan_install(project, peripheral, board, self._files, pins)
        except (PeripheralError, FileServiceError) as exc:
            dialog.show_problem(
                peripheral.name, peripheral.description, str(exc), wiring_for(peripheral, pins)
            )
            return
        dialog.show_plan(
            peripheral.name,
            peripheral.description,
            wiring_for(peripheral, pins),
            [str(c.path) for c in plan.changes if not c.created],
            [str(c.path) for c in plan.changes if c.created],
        )

    def _report(self, plan: InstallPlan) -> None:
        name = plan.peripheral.name
        wiring = ", ".join(f"{a} → {b}" for a, b in plan.wiring)
        if not plan.manual:
            self._window.notify(
                _("{name} додано").format(name=name),
                _(
                    "Підключіть модуль ({wiring}) і натисніть «Зібрати й прошити» через ST-Link: "
                    "значення з'являться у вкладці RTT."
                ).format(wiring=wiring),
                ToastKind.SUCCESS,
            )
            return
        manual = plan.manual
        self._window.notify(
            _("{name} додано, але код треба вставити вручну").format(name=name),
            _(
                "У main.rs немає embassy_stm32::init або циклу loop, тож код налаштування й "
                "читання не вставлено. Скопіюйте його й вставте у main()."
            ),
            ToastKind.WARNING,
            _("Скопіювати код"),
            lambda: QGuiApplication.clipboard().setText(manual),
        )
