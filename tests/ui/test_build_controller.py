"""BuildController with a fake cargo (recorded output) and, at the end, the real toolchain."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from nightexpress.controllers.build_controller import BuildController
from nightexpress.controllers.busy_tracker import BusyTracker
from nightexpress.controllers.project_controller import ProjectController
from nightexpress.models.entities import (
    BuildSummary,
    Diagnostic,
    DiagnosticLevel,
    MemoryUsage,
    OperationState,
    ProjectConfig,
    UploadMethod,
)
from nightexpress.models.state import AppModel, BuildModel, ProjectModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.cargo_message_parser import strip_ansi
from nightexpress.services.cargo_service import CargoService
from nightexpress.services.elf_tools import ElfToolsError
from nightexpress.services.toolchain_probe import ToolchainProbe
from nightexpress.views.editor.monaco_editor import monaco_installed
from nightexpress.views.main_window import BUILD_TAB, PROBLEMS_TAB, UART_TAB, MainWindow

pytestmark = pytest.mark.ui

ROOT = Path(__file__).resolve().parents[2]
FAKE_CARGO = str(ROOT / "tests" / "fixtures" / "fake_cargo.py")
REFERENCE = ROOT / "reference" / "blinky_f103c6"
CATALOG = BoardCatalog.load()
USAGE = MemoryUsage(1064, 32768, 4, 10240)


class Env:
    def __init__(self, dispose, tmp_path: Path, scenario: str = "success") -> None:
        self.window = MainWindow("NightExpress", "0.1.0")
        dispose(self.window)
        self.project = ProjectModel()
        self.build = BuildModel()
        self.scenario = scenario
        self.cargo_found = True
        self.save_ok = True
        self.opened: list[Path] = []
        self.errors: list[tuple[str, str]] = []
        self.memory_calls: list[tuple[Path, int, int]] = []
        self.window.show_error = lambda title, text: self.errors.append((title, text))
        self.cargo = CargoService(parent=self.window)
        self.controller = BuildController(
            self.window,
            self.project,
            self.build,
            CATALOG,
            self.cargo,
            lambda: (sys.executable, FAKE_CARGO, self.scenario) if self.cargo_found else None,
            save_all=lambda: self.save_ok,
            open_file=lambda path: self.opened.append(path) or True,
            install_hint="Встановіть Rust: https://rustup.rs",
            read_memory=self._read_memory,
        )
        BusyTracker(self.window, self.build)
        self.project_dir = tmp_path / "blinky"
        self.project_dir.mkdir()
        self.window.set_project("blinky", self.project_dir)
        self.project.open_project(
            ProjectConfig(self.project_dir, "blinky", "stm32f103c6-minimal", UploadMethod.PROBE_RS)
        )

    def _read_memory(self, elf: Path, flash: int, ram: int) -> MemoryUsage:
        self.memory_calls.append((elf, flash, ram))
        if not elf.name:
            raise ElfToolsError("bad elf")
        return USAGE

    def log(self) -> str:
        return strip_ansi("\n".join(self.build.log))


@pytest.fixture
def env(dispose, tmp_path: Path) -> Env:
    return Env(dispose, tmp_path)


def run_verify(qtbot, env: Env, timeout: int = 30_000):
    with qtbot.waitSignal(env.controller.build_finished, timeout=timeout) as blocker:
        assert env.controller.verify()
    return blocker.args[0]


def test_successful_verify_reports_memory(qtbot, env: Env) -> None:
    states: list[OperationState] = []
    env.build.state_changed.connect(states.append)
    result = run_verify(qtbot, env)

    assert result.success
    assert states == [OperationState.PREPARING, OperationState.RUNNING, OperationState.SUCCEEDED]
    assert env.memory_calls == [(result.elf_path, 32 * 1024, 10 * 1024)]
    assert env.build.memory_usage == USAGE
    log = env.log()
    assert "Запуск збірки проєкту «blinky» (release)" in log
    assert "Перша збірка" in log  # no target/ directory yet
    assert "Програма займає" in log
    assert env.build.summary is not None and env.build.summary.success
    assert env.build.summary.message.startswith("Збірку успішно завершено (")
    assert env.window.toolbar.verify_button.isEnabled()  # unlocked again
    status = env.window.activity_status
    assert status.text().startswith("Зібрано за ") and status.text().endswith("· flash 3 %")
    assert status.property("state") == "ok"
    assert not env.window.activity_spinner.is_spinning
    assert env.window.toolbar.running is None


def test_warning_shows_in_problems_and_editor(qtbot, env: Env) -> None:
    env.scenario = "warning"
    run_verify(qtbot, env)
    [warning] = env.build.diagnostics
    assert warning.level is DiagnosticLevel.WARNING
    assert env.window.problems_panel.tree_model.rowCount() == 1
    assert env.window.editor_tabs.editor._markers  # markers kept for files opened later
    assert env.opened == []  # warnings do not jump around


def test_compile_error_reveals_first_error(qtbot, env: Env) -> None:
    env.scenario = "compile_error"
    states: list[OperationState] = []
    env.build.state_changed.connect(states.append)
    result = run_verify(qtbot, env)
    assert not result.success
    assert states[-1] is OperationState.FAILED
    assert env.opened == [env.project_dir / "src" / "main.rs"]
    assert env.build.summary == BuildSummary(False, "Збірка не вдалася: 1 помилка")
    assert env.memory_calls == []


def test_problems_double_click_reveals(qtbot, env: Env) -> None:
    env.scenario = "compile_error"
    run_verify(qtbot, env)
    env.opened.clear()
    panel = env.window.problems_panel
    [error] = [d for d in env.build.diagnostics if d.level is DiagnosticLevel.ERROR]
    panel.tree.doubleClicked.emit(panel.index_of(error))
    assert env.opened == [env.project_dir / "src" / "main.rs"]


def test_log_location_opens_the_file(qtbot, env: Env) -> None:
    main = env.project_dir / "src" / "main.rs"
    main.parent.mkdir()
    main.write_text("fn main() {}\n")
    env.window.output_panel.location_activated.emit("src/main.rs", 3, 1)
    assert env.opened == [main]
    env.window.output_panel.location_activated.emit("src/missing.rs", 1, 1)
    assert "не знайдено" in env.window.activity_status.text()
    outside = env.project_dir.parent / "dependency.rs"
    outside.write_text("")
    env.window.output_panel.location_activated.emit(str(outside), 1, 1)
    assert "поза проєктом" in env.window.activity_status.text()
    assert env.opened == [main]


def test_busy_state_and_cancel(qtbot, env: Env) -> None:
    env.scenario = "slow"
    states: list[OperationState] = []
    env.build.state_changed.connect(states.append)
    with qtbot.waitSignal(env.cargo._runner.started, timeout=5000):
        env.controller.verify()
    assert env.window.toolbar.verify_button.text() == "Зупинити"
    assert env.window.toolbar.verify_button.isEnabled()
    assert not env.window.toolbar.upload_button.isEnabled()
    assert env.window.actions_by_name["stop"].isEnabled()
    assert not env.controller.verify()  # second Verify while busy is ignored

    with qtbot.waitSignal(env.controller.build_finished, timeout=10_000):
        env.window.toolbar.verify_button.click()
    assert states[-1] is OperationState.CANCELLED
    assert env.build.summary == BuildSummary(False, "Збірку зупинено")
    assert env.window.toolbar.verify_button.isEnabled()
    assert not env.window.actions_by_name["stop"].isEnabled()


def test_preconditions(env: Env) -> None:
    env.cargo_found = False
    assert not env.controller.verify()
    assert env.errors[-1][0] == "Rust не знайдено" and "rustup.rs" in env.errors[-1][1]

    env.cargo_found = True
    env.save_ok = False  # user cancelled the save dialog
    assert not env.controller.verify()
    assert env.build.state is OperationState.IDLE

    env.project.close_project()
    env.save_ok = True
    assert not env.controller.verify()
    assert env.errors[-1][0] == "Немає проєкту"


def test_failed_to_start(qtbot, env: Env) -> None:
    env.controller._cargo_command = lambda: ("/nonexistent/cargo",)
    result = run_verify(qtbot, env)
    assert not result.success
    assert "Не вдалося запустити cargo" in env.log()
    assert env.build.state is OperationState.FAILED


def test_clean(qtbot, env: Env) -> None:
    received: list[object] = []
    env.controller.build_finished.connect(received.append)
    with qtbot.waitSignal(
        env.build.state_changed, timeout=10_000, check_params_cb=lambda s: s.is_finished
    ):
        assert env.controller.clean()
    assert "Очищаю збірку" in env.log()
    assert received == []  # cleaning is not a build result


# --- end to end with the real toolchain ----------------------------------------------------------


@pytest.mark.skipif(not monaco_installed(), reason="run scripts/fetch_monaco.py first")
def test_broken_then_fixed_main_rs_with_real_cargo(
    qtbot, dispose, settings_store, tmp_path: Path
) -> None:
    """Acceptance criterion of stage 6."""
    probe = ToolchainProbe()
    cargo = probe.find("cargo")
    if cargo is None or shutil.which("rustup", path=probe.search_path) is None:
        pytest.skip("Rust toolchain is not installed")
    project = tmp_path / "blinky"
    shutil.copytree(REFERENCE, project, ignore=shutil.ignore_patterns("target", "*.bin"))

    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    window.show_error = lambda title, text: pytest.fail(f"{title}: {text}")
    app, project_model, build = AppModel(), ProjectModel(), BuildModel()
    projects = ProjectController(window, app, project_model, CATALOG, settings_store)
    target_dir = Path(tempfile.gettempdir()) / "nightexpress-test-target"
    controller = BuildController(
        window,
        project_model,
        build,
        CATALOG,
        CargoService(
            search_path=probe.search_path,
            parent=window,
            extra_env={"CARGO_TARGET_DIR": str(target_dir)},
        ),
        lambda: (str(cargo),),
        save_all=projects.save_all,
        open_file=projects.open_file,
    )
    assert projects.open_project(project)
    main = project / "src" / "main.rs"
    original = main.read_text()
    broken = original.replace("led.toggle();", "led.toggle();\n        undefined_value += 1;")
    window.editor_tabs.editor._on_content_changed(str(main), broken, 1)  # as if typed in Monaco

    with qtbot.waitSignal(controller.build_finished, timeout=600_000) as blocker:
        controller.verify()
    assert not blocker.args[0].success
    assert main.read_text() == broken  # Verify saved the file first
    [error] = [d for d in build.diagnostics if d.level is DiagnosticLevel.ERROR]
    assert (
        error.file == main
        and error.line == broken.splitlines().index("        undefined_value += 1;") + 1
    )
    assert window.editor_tabs.current_path == main

    window.editor_tabs.editor._on_content_changed(str(main), original, 2)
    with qtbot.waitSignal(controller.build_finished, timeout=600_000) as blocker:
        controller.verify()
    assert blocker.args[0].success
    assert build.diagnostics == ()
    usage = build.memory_usage
    assert usage is not None and 0 < usage.flash_used < usage.flash_total == 32 * 1024


def test_build_shows_stop_button_and_stage_while_running(qtbot, env: Env) -> None:
    seen: list[tuple[str | None, str, bool]] = []
    env.build.state_changed.connect(
        lambda state: (
            seen.append(
                (
                    env.window.toolbar.running,
                    env.window.activity_status.text(),
                    env.window.activity_spinner.is_spinning,
                )
            )
            if state is OperationState.RUNNING
            else None
        )
    )
    run_verify(qtbot, env)
    assert seen == [("verify", "Збираю…", True)]


def test_failed_build_opens_problems_and_links_status(qtbot, env: Env) -> None:
    env.scenario = "compile_error"
    env.window.show()
    run_verify(qtbot, env)
    assert env.window.bottom_tabs.currentIndex() == PROBLEMS_TAB
    status = env.window.activity_status
    assert status.text().startswith("Збірка не вдалася: ")
    assert status.property("state") == "error"


def test_success_in_background_tab_gets_a_badge(qtbot, env: Env) -> None:
    env.window.show()
    env.build.state_changed.connect(
        lambda state: env.window.show_uart() if state is OperationState.RUNNING else None
    )
    run_verify(qtbot, env)
    assert env.window.bottom_tabs.currentIndex() == UART_TAB  # the user is not interrupted
    assert env.window.tab_badge(BUILD_TAB) is False


def _error(file: Path) -> Diagnostic:
    return Diagnostic(DiagnosticLevel.ERROR, "mismatched types", file, 3, 1, "E0308", "error")


def test_files_outside_the_project_are_not_opened(env: Env) -> None:
    dependency = env.project_dir.parent / "registry" / "lib.rs"
    env.controller.reveal(_error(dependency))
    assert env.opened == []
    assert "поза проєктом" in env.window.activity_status.text()
    env.controller.reveal(_error(env.project_dir / "src" / "main.rs"))
    assert env.opened == [env.project_dir / "src" / "main.rs"]


def test_diagnostics_reach_the_model_in_batches(qtbot, env: Env) -> None:
    changes: list[int] = []
    env.build.diagnostics_changed.connect(lambda items: changes.append(len(items)))
    for _ in range(50):
        env.cargo.diagnostic.emit(_error(env.project_dir / "src" / "main.rs"))
    assert changes == []
    qtbot.waitUntil(lambda: changes == [50], timeout=5000)


def test_project_closed_during_a_build_leaves_no_results(qtbot, env: Env) -> None:
    env.scenario = "slow"
    with qtbot.waitSignal(env.cargo._runner.started, timeout=5000):
        env.controller.verify()
    env.cargo.diagnostic.emit(_error(env.project_dir / "src" / "main.rs"))
    with qtbot.waitSignal(env.controller.build_finished, timeout=10_000):
        env.project.close_project()
    assert env.build.diagnostics == ()
    assert env.build.log == ()
    qtbot.wait(150)  # nothing arrives later from the batch timer either
    assert env.build.diagnostics == ()
