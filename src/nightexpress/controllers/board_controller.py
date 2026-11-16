"""BoardController: "Change board…" — move the open project to another board."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject

from nightexpress.i18n import _, ngettext
from nightexpress.models.entities import (
    BoardDefinition,
    ChipDefinition,
    Diagnostic,
    DiagnosticLevel,
    ProjectConfig,
)
from nightexpress.models.state import BuildModel, ProjectModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.board_migration import (
    BoardMigration,
    BoardMigrationError,
    MigrationPlan,
)
from nightexpress.services.file_service import FileServiceError
from nightexpress.services.project_config_repository import (
    ProjectConfigError,
    ProjectConfigRepository,
)
from nightexpress.views.main_window import MainWindow
from nightexpress.views.toast import ToastKind


class BoardController(QObject):
    """The configuration files are rewritten; the sources are only pointed at.

    The places in the code that still name pins of the previous board go to "Problems" as
    warnings (and to the build log), where they stay until the next build.
    """

    def __init__(
        self,
        window: MainWindow,
        project_model: ProjectModel,
        build_model: BuildModel,
        catalog: BoardCatalog,
        migration: BoardMigration,
        config_repository: ProjectConfigRepository,
        save_all: Callable[[], bool],
        refresh_files: Callable[[], None],
        is_busy: Callable[[], bool],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._project = project_model
        self._build = build_model
        self._catalog = catalog
        self._migration = migration
        self._config = config_repository
        self._save_all = save_all
        self._refresh_files = refresh_files
        self._is_busy = is_busy
        window.change_board_requested.connect(lambda: self.change_board())
        window.toolbar.board_selected.connect(self.change_board)

    def change_board(self, board_id: str | None = None) -> bool:
        """Ask for a board (unless given), confirm and move the project to it."""
        config = self._project.config
        if config is None:
            return False
        changed = self._change(config, board_id)
        if not changed:
            # The list in the toolbar shows the project's board, not the refused choice.
            self._window.toolbar.set_current_board(config.board_id)
        return changed

    # --- internals -------------------------------------------------------------------------

    def _change(self, config: ProjectConfig, board_id: str | None) -> bool:
        window = self._window
        if self._is_busy():
            window.show_message(_("Спершу дочекайтеся кінця збірки чи прошивання або зупиніть їх"))
            return False
        if board_id is None:
            if all(board.id == config.board_id for board in self._catalog.boards):
                window.notify(
                    _("Іншої плати немає"),
                    _("У каталозі поки що лише плата цього проєкту — змінювати нема на що."),
                )
                return False
            board_id = window.ask_board(
                _(
                    "Проєкт «{name}» буде перенесено на вибрану плату. Перед зміною покажу, що "
                    "саме буде переписано."
                ).format(name=config.name),
                self._catalog.boards,
                {c.id: c for c in self._catalog.chips},
                self._catalog.search,
            )
            if board_id is None:
                return False
        if board_id == config.board_id:
            window.show_message(_("Це вже плата проєкту"))
            return False
        try:
            board = self._catalog.get_board(board_id)
        except KeyError:
            return False
        chip = self._catalog.chip_for(board)
        if not self._save_all():
            return False  # the configuration on disk must be the one the user sees
        try:
            plan = self._migration.plan(
                config.path, config.name, self._known(config), (board, chip), config.upload_method
            )
        except BoardMigrationError as exc:
            window.show_error(_("Не вдалося змінити плату"), str(exc))
            return False
        if not window.ask_confirm(
            _("Змінити плату"),
            _("Перенести проєкт «{name}» на плату «{board}»?").format(
                name=config.name, board=board.display_name
            ),
            _("Змінити плату"),
            _details(plan),
        ):
            return False
        try:
            self._migration.apply(plan)
            new_config = self._config.read(config.path)
        except (BoardMigrationError, ProjectConfigError, FileServiceError) as exc:
            window.show_error(_("Не вдалося змінити плату"), str(exc))
            return False

        self._project.set_config(new_config)
        window.set_board(board, chip)
        self._refresh_files()  # open tabs of the rewritten files show the new text
        self._report(plan)
        return True

    def _known(self, config: ProjectConfig) -> tuple[BoardDefinition, ChipDefinition] | None:
        try:
            board = self._catalog.get_board(config.board_id)
        except KeyError:
            return None
        return board, self._catalog.chip_for(board)

    def _report(self, plan: MigrationPlan) -> None:
        build, window = self._build, self._window
        root = plan.project_dir
        build.reset_results()  # problems and log of earlier builds were about the other chip
        build.append_log(
            _("Плату змінено: {board} ({chip}).").format(
                board=plan.board.display_name, chip=plan.chip.id
            )
        )
        for change in plan.changes:
            text = _("Створено {path}") if change.created else _("Оновлено {path}")
            build.append_log("  " + text.format(path=change.relative))
        for note in plan.notes:
            build.append_log("  " + _("Увага: {note}").format(note=note))
        if plan.hints:
            build.append_log(_("Перевірте в коді (застосунок його не змінював):"))
            for hint in plan.hints:
                where = hint.file.relative_to(root).as_posix()
                build.append_log(f"  --> {where}:{hint.line}:{hint.column}  {hint.message}")
        build.append_log(
            _("Наступна збірка буде повною: залежності збираються заново під цей чип.")
        )
        build.set_diagnostics(
            Diagnostic(
                level=DiagnosticLevel.WARNING,
                message=hint.message,
                file=hint.file,
                line=hint.line,
                column=hint.column,
                code=None,
                rendered=hint.message,
            )
            for hint in plan.hints
        )
        count = len(plan.hints)
        if count:
            window.show_problems()
            window.notify(
                _("Плату змінено"),
                ngettext(
                    "{count} місце в коді з пінами попередньої плати — у «Проблемах». Вони "
                    "зникнуть зі списку після наступної збірки.",
                    "{count} місць в коді з пінами попередньої плати — у «Проблемах». Вони "
                    "зникнуть зі списку після наступної збірки.",
                    count,
                ).format(count=count),
                ToastKind.WARNING,
            )
        else:
            window.show_output()
            window.notify(
                _("Плату змінено"),
                _(
                    "Згадок пінів попередньої плати в коді не знайдено. Подробиці — у журналі "
                    "збірки."
                ),
                ToastKind.SUCCESS,
            )


def _details(plan: MigrationPlan) -> str:
    """The text under the question: what is written, what is left to the user."""
    lines = []
    if plan.changes:
        names = ", ".join(str(change.relative) for change in plan.changes)
        lines.append(_("Буде переписано: {names}.").format(names=names))
    count = len(plan.hints)
    if count:
        lines.append(
            ngettext(
                "Код у src/ не змінюється: {count} місце з пінами попередньої плати треба "
                "поправити вручну — вони з'являться у «Проблемах».",
                "Код у src/ не змінюється: {count} місць з пінами попередньої плати треба "
                "поправити вручну — вони з'являться у «Проблемах».",
                count,
            ).format(count=count)
        )
    else:
        lines.append(_("Код у src/ не змінюється."))
    lines.extend(plan.notes)
    lines.append(_("Наступна збірка буде повною (кілька хвилин)."))
    return "\n\n".join(lines)
