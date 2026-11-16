"""BoardController with the real window and ProjectController; dialogs are stubs."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nightexpress.controllers.board_controller import BoardController
from nightexpress.controllers.project_controller import ProjectController
from nightexpress.models.entities import DiagnosticLevel, LedInfo
from nightexpress.models.state import AppModel, BuildModel, ProjectModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.board_migration import BoardMigration
from nightexpress.services.project_config_repository import ProjectConfigRepository
from nightexpress.services.project_generator import ProjectGenerator, make_context
from nightexpress.views.editor.monaco_editor import monaco_installed
from nightexpress.views.main_window import PROBLEMS_TAB, MainWindow

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not monaco_installed(), reason="run scripts/fetch_monaco.py first"),
]

REAL = BoardCatalog.load()
OLD_BOARD = REAL.get_board("stm32f103c6-minimal")
OLD_CHIP = REAL.chip_for(OLD_BOARD)
NEW_CHIP = dataclasses.replace(
    OLD_CHIP,
    id="STM32F411CE",
    family="STM32F4",
    core="cortex-m4",
    target_triple="thumbv7em-none-eabihf",
    flash_kb=512,
    ram_kb=128,
    embassy_feature="stm32f411ce",
    probe_rs_name="STM32F411CE",
)
NEW_BOARD = dataclasses.replace(
    OLD_BOARD,
    id="stm32f411ce-test",
    display_names={"uk": "STM32F411 — тестова плата"},
    chip_id=NEW_CHIP.id,
    aliases=("STM32F411CEU6",),
    led=LedInfo("PA5", active_low=False),
)
TWO_BOARDS = BoardCatalog([OLD_CHIP, NEW_CHIP], [OLD_BOARD, NEW_BOARD], [])
ONE_BOARD = BoardCatalog([OLD_CHIP], [OLD_BOARD], [])


class Env:
    def __init__(self, dispose, settings_store, tmp_path: Path, catalog: BoardCatalog) -> None:
        self.window = MainWindow("NightExpress", "0.1.0")
        dispose(self.window)
        self.window.toolbar.set_boards(catalog.boards)
        self.project = ProjectModel()
        self.build = BuildModel()
        self.busy = False
        self.confirm_answer = True
        self.confirmations: list[tuple[str, str]] = []
        self.board_answer: str | None = NEW_BOARD.id
        self.toasts: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []
        self.window.ask_confirm = self._ask_confirm
        self.window.ask_board = lambda *args, **kwargs: self.board_answer
        self.window.notify = lambda title, text="", *a, **k: self.toasts.append((title, text))
        self.window.show_error = lambda title, text: self.errors.append((title, text))
        repository = ProjectConfigRepository()
        self.projects = ProjectController(
            self.window, AppModel(), self.project, catalog, settings_store
        )
        self.controller = BoardController(
            self.window,
            self.project,
            self.build,
            catalog,
            BoardMigration(),
            repository,
            save_all=self.projects.save_all,
            refresh_files=self.projects.check_external_changes,
            is_busy=lambda: self.busy,
        )
        self.project_dir = tmp_path / "demo"
        ProjectGenerator().generate(
            "blink", self.project_dir, make_context("demo", OLD_BOARD, OLD_CHIP)
        )
        assert self.projects.open_project(self.project_dir)

    def _ask_confirm(self, title: str, text: str, confirm_text: str, details: str = "") -> bool:
        self.confirmations.append((text, details))
        return self.confirm_answer

    @property
    def cargo_toml(self) -> str:
        return (self.project_dir / "Cargo.toml").read_text()


@pytest.fixture
def env(qtbot, dispose, settings_store, tmp_path: Path) -> Env:
    return Env(dispose, settings_store, tmp_path, TWO_BOARDS)


def test_change_board_from_the_menu(env: Env) -> None:
    cargo = env.project_dir / "Cargo.toml"
    env.projects.open_file(cargo)
    env.build.append_log("an earlier build")
    env.window.actions_by_name["change_board"].trigger()

    [(question, details)] = env.confirmations
    assert "«demo»" in question and "STM32F411" in question
    assert ".cargo/config.toml, rust-toolchain.toml, Cargo.toml" in details
    assert "поправити вручну" in details and "повною" in details
    assert env.project.config is not None and env.project.config.board_id == NEW_BOARD.id
    assert env.window.toolbar.current_board_id == NEW_BOARD.id
    assert env.window.toolbar.target_combo.currentText() == "thumbv7em-none-eabihf"
    assert '"stm32f411ce"' in env.cargo_toml
    # The open tab shows the rewritten file, and it is not "unsaved".
    assert '"stm32f411ce"' in env.projects._saved[cargo] and not env.project.dirty_files

    main = env.project_dir / "src" / "main.rs"
    hints = env.build.diagnostics
    assert hints and all(d.level is DiagnosticLevel.WARNING and d.file == main for d in hints)
    assert env.window.bottom_tabs.currentIndex() == PROBLEMS_TAB
    log = "\n".join(env.build.log)
    assert "an earlier build" not in log and "Плату змінено" in log
    assert f"--> src/main.rs:{hints[0].line}:{hints[0].column}" in log
    assert env.toasts[-1][0] == "Плату змінено"


def test_cancelled_change_writes_nothing(env: Env) -> None:
    before = env.cargo_toml
    env.confirm_answer = False
    env.window.toolbar.board_combo.setCurrentIndex(1)  # the user picks it in the toolbar
    env.window.toolbar.board_combo.activated.emit(1)

    assert len(env.confirmations) == 1
    assert env.cargo_toml == before
    assert env.project.config is not None and env.project.config.board_id == OLD_BOARD.id
    assert env.window.toolbar.current_board_id == OLD_BOARD.id  # the list goes back
    assert env.build.diagnostics == ()

    env.board_answer = None  # the board dialog is cancelled
    assert not env.controller.change_board()
    assert len(env.confirmations) == 1


def test_unsaved_files_are_saved_first(env: Env) -> None:
    main = env.project_dir / "src" / "main.rs"
    env.window.editor_tabs.editor._on_content_changed(str(main), "// PC13 PC13\n", 1)
    assert env.controller.change_board(NEW_BOARD.id)
    assert main.read_text() == "// PC13 PC13\n"
    assert [(d.line, d.column) for d in env.build.diagnostics] == [(1, 4), (1, 9)]


def test_refused_while_busy_or_for_the_same_board(env: Env) -> None:
    env.busy = True
    assert not env.controller.change_board(NEW_BOARD.id)
    assert "зупиніть" in env.window.activity_status.text()
    env.busy = False
    assert not env.controller.change_board(OLD_BOARD.id)
    assert "вже плата проєкту" in env.window.activity_status.text()
    assert env.confirmations == []


def test_broken_configuration_is_reported(env: Env) -> None:
    (env.project_dir / "rust-toolchain.toml").write_text("[toolchain\n")
    before = env.cargo_toml
    assert not env.controller.change_board(NEW_BOARD.id)
    assert env.errors and "rust-toolchain.toml" in env.errors[0][1]
    assert env.cargo_toml == before and env.confirmations == []


def test_single_board_catalog_has_nothing_to_offer(dispose, settings_store, tmp_path: Path) -> None:
    env = Env(dispose, settings_store, tmp_path, ONE_BOARD)
    env.board_answer = OLD_BOARD.id
    assert not env.controller.change_board()
    assert env.toasts == [
        ("Іншої плати немає", "У каталозі поки що лише плата цього проєкту — змінювати нема на що.")
    ]


def test_action_needs_a_project(env: Env) -> None:
    action = env.window.actions_by_name["change_board"]
    assert action.isEnabled()
    env.window.set_busy(True, "verify")
    assert not action.isEnabled()
    env.window.set_busy(False, None)
    env.projects.close_project()
    assert not action.isEnabled()
    assert not env.controller.change_board(NEW_BOARD.id)
