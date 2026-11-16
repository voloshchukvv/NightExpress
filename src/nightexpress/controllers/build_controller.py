"""Verify (✓): build the open project and show diagnostics and memory usage."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from nightexpress.i18n import N_, _, ngettext
from nightexpress.models.entities import (
    BoardDefinition,
    BuildSummary,
    ChipDefinition,
    Diagnostic,
    DiagnosticLevel,
    MemoryUsage,
    OperationState,
)
from nightexpress.models.state import BuildModel, ProjectModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.cargo_service import BuildResult, CargoService
from nightexpress.services.elf_tools import ElfToolsError, memory_usage
from nightexpress.services.process_runner import ProcessOutcome
from nightexpress.views.formatting import decimal, memory_report, percent
from nightexpress.views.main_window import BUILD_TAB, PROBLEMS_TAB, MainWindow
from nightexpress.views.toolbar_view import PROFILE_NAMES, board_label

MEMORY_WARNING_PERCENT = 90
RESULT_TIMEOUT_MS = 8000  # minor outcomes leave the status bar by themselves
DIAGNOSTICS_BATCH_MS = 100  # diagnostics are shown at most this often while cargo runs

CargoCommandProvider = Callable[[], Sequence[str] | None]
MemoryReader = Callable[[Path, int, int], MemoryUsage]


class _Operation(Enum):
    BUILD = "build"
    CLEAN = "clean"


class BuildController(QObject):
    build_finished = Signal(object)  # BuildResult; the upload controller chains on it

    def __init__(
        self,
        window: MainWindow,
        project_model: ProjectModel,
        build_model: BuildModel,
        catalog: BoardCatalog,
        cargo: CargoService,
        cargo_command: CargoCommandProvider,
        save_all: Callable[[], bool],
        open_file: Callable[[Path], bool],
        install_hint: str = "",
        read_memory: MemoryReader = memory_usage,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._project = project_model
        self._build = build_model
        self._catalog = catalog
        self._cargo = cargo
        self._cargo_command = cargo_command
        self._save_all = save_all
        self._open_file = open_file
        self._install_hint = install_hint
        self._read_memory = read_memory
        self._operation = _Operation.BUILD
        self._chip: ChipDefinition | None = None
        self._started = time.monotonic()

        cargo.log_line.connect(build_model.append_log)
        self._pending_diagnostics: list[Diagnostic] = []
        self._diagnostics_timer = QTimer(self, singleShot=True, interval=DIAGNOSTICS_BATCH_MS)
        self._diagnostics_timer.timeout.connect(self._flush_diagnostics)
        self._project_changed = False  # during a build: its results belong to another project
        cargo.diagnostic.connect(self._queue_diagnostic)
        cargo.finished.connect(self._on_finished)

        window.output_panel.bind(build_model)
        build_model.memory_usage_changed.connect(window.target_view.set_memory_usage)
        window.problems_panel.bind(build_model)
        build_model.diagnostics_changed.connect(window.editor_tabs.editor.set_diagnostics)
        build_model.diagnostics_changed.connect(
            lambda diagnostics: window.file_tree.set_error_files(
                d.file for d in diagnostics if d.level is DiagnosticLevel.ERROR and d.file
            )
        )
        build_model.state_changed.connect(self._on_state_changed)
        window.problems_panel.diagnostic_activated.connect(self.reveal)
        window.output_panel.location_activated.connect(self.reveal_location)
        window.verify_requested.connect(self.verify)
        window.clean_requested.connect(self.clean)
        window.stop_requested.connect(self.cancel)
        project_model.config_changed.connect(self._on_project_changed)

    @property
    def is_busy(self) -> bool:
        return self._build.state.is_busy

    @property
    def memory_usage(self) -> MemoryUsage | None:
        """Size of the last successful build (None while building or after a failure)."""
        return self._build.memory_usage

    # --- commands --------------------------------------------------------------------------

    def verify(self) -> bool:
        """Start a build; returns False if it could not be started."""
        target = self._prepare(_Operation.BUILD)
        if target is None:
            return False
        project_dir, board, chip = target
        first_build = not (project_dir / "target" / chip.target_triple).exists()
        profile = self._project.build_profile
        self._build.append_log(
            _("Запуск збірки проєкту «{name}» ({profile}) для {board}…").format(
                name=project_dir.name,
                profile=PROFILE_NAMES[profile],
                board=board_label(board),
            )
        )
        if first_build:
            self._build.append_log(
                _(
                    "Перша збірка: rustup може завантажити toolchain і target, а cargo — "
                    "залежності. Це займе кілька хвилин; наступні збірки — секунди."
                )
            )
        self._build.set_state(OperationState.RUNNING)
        self._started = time.monotonic()
        self._cargo.build(project_dir, profile)
        return True

    def clean(self) -> bool:
        target = self._prepare(_Operation.CLEAN, save=False)
        if target is None:
            return False
        self._build.append_log(_("Очищаю збірку (cargo clean)…"))
        self._build.set_state(OperationState.RUNNING)
        self._cargo.clean(target[0])
        return True

    def cancel(self) -> None:
        if self.is_busy:
            self._build.append_log(_("Зупиняю…"))
            self._cargo.cancel()

    def reveal(self, diagnostic: Diagnostic) -> None:
        """Open the file of a diagnostic and put the cursor on it."""
        if diagnostic.file is None or diagnostic.line is None:
            return
        if not self._in_project(diagnostic.file):
            # Sources of dependencies are not ours to edit by accident.
            self._window.show_message(
                _("{name} — файл поза проєктом, його не відкрито").format(name=diagnostic.file.name)
            )
            return
        if not self._open_file(diagnostic.file):
            return
        if diagnostic.file in self._window.editor_tabs.paths:
            self._window.editor_tabs.reveal(
                diagnostic.file, diagnostic.line, diagnostic.column or 1
            )

    def reveal_location(self, file: str, line: int, column: int = 1) -> None:
        """Open a "file:line:column" printed in the build log (relative to the project)."""
        config = self._project.config
        if config is None:
            return
        path = Path(file)
        if not path.is_absolute():
            path = config.path / path
        if not path.is_file():
            self._window.show_message(_("Файл {file} не знайдено").format(file=file))
            return
        if not self._in_project(path):
            # Sources of dependencies are not ours to edit by accident.
            self._window.show_message(
                _("{name} — файл поза проєктом, його не відкрито").format(name=path.name)
            )
            return
        if self._open_file(path) and path in self._window.editor_tabs.paths:
            self._window.editor_tabs.reveal(path, max(line, 1), max(column, 1))

    # --- internals -------------------------------------------------------------------------

    def _in_project(self, path: Path) -> bool:
        config = self._project.config
        if config is None:
            return False
        try:
            return path.resolve().is_relative_to(config.path.resolve())
        except OSError:
            return False

    def _queue_diagnostic(self, diagnostic: Diagnostic) -> None:
        """Diagnostics reach the model in batches: every change of the list redraws the
        problems tree and the editor markers, which is too much per single warning."""
        self._pending_diagnostics.append(diagnostic)
        if not self._diagnostics_timer.isActive():
            self._diagnostics_timer.start()

    def _flush_diagnostics(self) -> None:
        self._diagnostics_timer.stop()
        if self._pending_diagnostics:
            pending, self._pending_diagnostics = self._pending_diagnostics, []
            self._build.set_diagnostics((*self._build.diagnostics, *pending))

    def _reset_results(self) -> None:
        self._diagnostics_timer.stop()
        self._pending_diagnostics = []
        self._build.reset_results()

    def _prepare(
        self, operation: _Operation, save: bool = True
    ) -> tuple[Path, BoardDefinition, ChipDefinition] | None:
        if self.is_busy:
            return None
        config = self._project.config
        if config is None:
            self._window.show_error(
                _("Немає проєкту"), _("Спочатку створіть або відкрийте проєкт.")
            )
            return None
        try:
            board = self._catalog.get_board(config.board_id)
        except KeyError:
            self._window.show_error(
                _("Невідома плата"),
                _("Плата «{board_id}» невідома цій версії NightExpress.").format(
                    board_id=config.board_id
                ),
            )
            return None
        command = self._cargo_command()
        if not command:
            self._window.show_error(
                _("Rust не знайдено"),
                _("Не знайдено cargo. {hint}").format(hint=self._install_hint).strip(),
            )
            return None
        if save and not self._save_all():
            return None  # the user cancelled or a file could not be saved

        self._cargo.set_command(command)
        self._operation = operation
        self._chip = self._catalog.chip_for(board)
        self._project_changed = False
        self._reset_results()
        self._build.set_state(OperationState.PREPARING)
        self._window.show_output()
        return config.path, board, self._chip

    def _on_finished(self, result: BuildResult) -> None:
        elapsed = time.monotonic() - self._started
        window = self._window
        self._flush_diagnostics()
        # What stays in the status bar is a function: it follows a change of the language.
        if result.outcome is ProcessOutcome.CANCELLED:
            clean = self._operation is _Operation.CLEAN

            def stopped() -> str:
                return _("Очищення зупинено") if clean else _("Збірку зупинено")

            self._build.set_summary(BuildSummary(False, stopped()))
            window.show_result(stopped, None, timeout_ms=RESULT_TIMEOUT_MS)
            self._build.set_state(OperationState.CANCELLED)
        elif result.outcome is ProcessOutcome.FAILED_TO_START:
            self._build.append_log(
                _("Не вдалося запустити cargo: {error}").format(error=result.error)
            )
            window.show_result(N_("Не вдалося запустити cargo"), False, BUILD_TAB)
            window.show_output()
            self._build.set_state(OperationState.FAILED)
        elif self._operation is _Operation.CLEAN:
            self._build.append_log(
                _("Збірку очищено.") if result.success else _("cargo clean не вдався.")
            )
            if result.success:
                window.show_result(N_("Збірку очищено"), True, timeout_ms=RESULT_TIMEOUT_MS)
            else:
                window.show_result(N_("cargo clean не вдався"), False, BUILD_TAB)
            self._build.set_state(
                OperationState.SUCCEEDED if result.success else OperationState.FAILED
            )
        elif result.success:
            usage = self._report_memory(result.elf_path)
            seconds = decimal(elapsed, 2)
            self._build.set_summary(
                BuildSummary(
                    True, _("Збірку успішно завершено ({seconds} с)").format(seconds=seconds)
                )
            )

            def built() -> str:
                text = _("Зібрано за {seconds} с").format(seconds=decimal(elapsed, 1))
                if usage is not None:
                    text += " · flash " + percent(f"{usage.flash_percent:.0f}")
                return text

            window.show_result(built, True, BUILD_TAB)
            window.notify_tab(BUILD_TAB)
            self._build.set_state(OperationState.SUCCEEDED)
        else:
            errors = [d for d in result.diagnostics if d.level is DiagnosticLevel.ERROR]
            count = len(errors)

            def failed() -> str:
                if not count:
                    return _("Збірка не вдалася")
                return ngettext(
                    "Збірка не вдалася: {count} помилка",
                    "Збірка не вдалася: {count} помилок",
                    count,
                ).format(count=count)

            self._build.set_summary(BuildSummary(False, failed()))
            # A failure must not go unnoticed: bring the tab that explains it to the front.
            if errors:
                window.show_result(failed, False, PROBLEMS_TAB)
                window.show_problems()
            else:
                window.show_result(failed, False, BUILD_TAB)
                window.show_output()
            self._build.set_state(OperationState.FAILED)
            first = next(
                (d for d in errors if d.has_location and d.file and self._in_project(d.file)),
                None,
            )
            if first is not None:
                self.reveal(first)
        if self._operation is _Operation.BUILD:
            self.build_finished.emit(result)
        if self._project_changed:
            # The project was closed or replaced meanwhile: its problems and log must not
            # stay on screen as if they were the new project's.
            self._project_changed = False
            self._reset_results()

    def _report_memory(self, elf: Path | None) -> MemoryUsage | None:
        chip = self._chip
        if elf is None or chip is None:
            return None
        try:
            usage = self._read_memory(elf, chip.flash_bytes, chip.ram_bytes)
        except ElfToolsError as exc:
            self._build.append_log(
                _("Не вдалося визначити розмір програми: {error}").format(error=exc)
            )
            return None
        self._build.set_memory_usage(usage)
        for line in memory_report(usage):
            self._build.append_log(line)
        if max(usage.flash_percent, usage.ram_percent) > MEMORY_WARNING_PERCENT:
            self._build.append_log(
                _(
                    "⚠ Пам'ять майже заповнена — прошивка може перестати влазити або стеку "
                    "забракне місця."
                )
            )
        return usage

    def _on_state_changed(self, state: OperationState) -> None:
        # How it ended is reported by _on_finished, which knows the details.
        if state is OperationState.RUNNING:
            self._window.show_activity(
                N_("Очищаю збірку…") if self._operation is _Operation.CLEAN else N_("Збираю…")
            )

    def _on_project_changed(self, _config: object) -> None:
        if self.is_busy:
            self._project_changed = True
            self._cargo.cancel()
        else:
            self._reset_results()
