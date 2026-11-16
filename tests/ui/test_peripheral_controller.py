"""Build → Add peripheral, end to end: the dialog, the files written, the message."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog

from nightexpress.controllers.peripheral_controller import PeripheralController
from nightexpress.models.entities import I2cPinChoice, ProjectConfig, UploadMethod
from nightexpress.models.state import ProjectModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.project_generator import ProjectGenerator, make_context
from nightexpress.views.dialogs.peripheral_dialog import PeripheralDialog
from nightexpress.views.main_window import MainWindow

pytestmark = pytest.mark.ui

CATALOG = BoardCatalog.load()


class Env:
    def __init__(self, qtbot, dispose, tmp_path: Path, template: str = "blink") -> None:
        self.window = MainWindow("NightExpress", "0.1.0")
        dispose(self.window)
        board = CATALOG.get_board("stm32f103c6-minimal")
        self.folder = tmp_path / "demo"
        context = make_context("demo", board, CATALOG.chip_for(board))
        ProjectGenerator().generate(template, self.folder, context)
        self.project = ProjectModel()
        self.project.open_project(
            ProjectConfig(self.folder, "demo", board.id, UploadMethod.PROBE_RS)
        )
        self.saved = 0
        self.opened: list[Path] = []
        self.busy = False
        self.controller = PeripheralController(
            self.window,
            self.project,
            CATALOG,
            save_all=self._save_all,
            refresh_files=lambda: None,
            open_file=self.opened.append,
            is_busy=lambda: self.busy,
        )

    def _save_all(self) -> bool:
        self.saved += 1
        return True


def test_adding_the_mpu6050(qtbot, dispose, tmp_path: Path, monkeypatch) -> None:
    env = Env(qtbot, dispose, tmp_path)
    shown: list[PeripheralDialog] = []

    def answer(dialog: PeripheralDialog) -> int:
        shown.append(dialog)
        select(dialog, "mpu6050")
        assert dialog.add_button.isEnabled()
        assert dialog.scl_combo is not None and dialog.sda_combo is not None
        assert dialog.scl_combo.currentText() == "PB6 · I2C1"
        assert "src/mpu6050.rs" in dialog.changes.text()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PeripheralDialog, "exec", answer)
    env.window.add_peripheral_requested.emit()
    assert shown and env.saved == 1  # open files are saved before main.rs is edited
    assert (env.folder / "src/mpu6050.rs").is_file()
    assert "mpu.read()" in (env.folder / "src/main.rs").read_text()
    assert env.opened == [env.folder / "src/main.rs"]
    (toast,) = env.window.toasts.toasts
    assert toast.title_text == "MPU-6050 додано" and "SCL → PB6" in toast.body_text

    # A second time the dialog says why it cannot, and "Add" is off.
    def refused(dialog: PeripheralDialog) -> int:
        select(dialog, "mpu6050")
        assert not dialog.add_button.isEnabled() and dialog.problem.isVisibleTo(dialog)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(PeripheralDialog, "exec", refused)
    assert not env.controller.open()


def select(dialog: PeripheralDialog, peripheral_id: str) -> None:
    from PySide6.QtCore import Qt

    for row in range(dialog.list.count()):
        if dialog.list.item(row).data(Qt.ItemDataRole.UserRole) == peripheral_id:
            dialog.list.setCurrentRow(row)


def test_choosing_other_pins(qtbot, dispose, tmp_path: Path, monkeypatch) -> None:
    env = Env(qtbot, dispose, tmp_path)

    def answer(dialog: PeripheralDialog) -> int:
        select(dialog, "bmp180")
        scl = dialog.scl_combo
        assert scl is not None
        assert [scl.itemText(i) for i in range(scl.count())] == ["PB6 · I2C1", "PB8 · I2C1"]
        scl.setCurrentIndex(1)
        scl.activated.emit(1)
        # On the F103 SDA follows SCL (remap): only PB9 goes with PB8.
        sda = dialog.sda_combo
        assert sda is not None and [sda.itemText(i) for i in range(sda.count())] == ["PB9 · I2C1"]
        assert dialog.add_button.isEnabled()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PeripheralDialog, "exec", answer)
    assert env.controller.open()
    main = (env.folder / "src/main.rs").read_text()
    assert "p.I2C1, p.PB8, p.PB9" in main and "Bmp180::new" in main


def test_a_second_module_offers_the_bus_already_there(
    qtbot, dispose, tmp_path: Path, monkeypatch
) -> None:
    env = Env(qtbot, dispose, tmp_path)
    assert env.controller.add("mpu6050", I2cPinChoice("I2C1", "PB8", "PB9"))

    def answer(dialog: PeripheralDialog) -> int:
        select(dialog, "bmp180")
        assert dialog.pins == I2cPinChoice("I2C1", "PB8", "PB9")
        assert dialog.add_button.isEnabled()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PeripheralDialog, "exec", answer)
    assert env.controller.open()
    main = (env.folder / "src/main.rs").read_text()
    assert main.count("I2c::new_blocking(") == 1 and "Bmp180::new" in main


def test_code_without_a_place_is_offered_to_copy(qtbot, dispose, tmp_path: Path) -> None:
    from PySide6.QtGui import QGuiApplication

    env = Env(qtbot, dispose, tmp_path, template="blank")
    assert env.controller.add("mpu6050")
    (toast,) = env.window.toasts.toasts
    assert toast.action_button is not None
    toast.action_button.click()
    assert "Mpu6050::new" in QGuiApplication.clipboard().text()


def test_nothing_happens_while_building(qtbot, dispose, tmp_path: Path) -> None:
    env = Env(qtbot, dispose, tmp_path)
    env.busy = True
    assert not env.controller.open()
    assert not (env.folder / "src/mpu6050.rs").exists()
