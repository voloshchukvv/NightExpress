"""Projects and files: create, open, edit, save, close."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from nightexpress.i18n import _
from nightexpress.models.entities import (
    BuildProfile,
    NewProjectRequest,
    ProjectConfig,
    RecentProject,
)
from nightexpress.models.state import AppModel, ProjectModel, Screen
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.file_service import FileService, FileServiceError, FileSignature
from nightexpress.services.project_config_repository import (
    MissingMetadataError,
    ProjectConfigError,
    ProjectConfigRepository,
)
from nightexpress.services.project_generator import (
    ProjectGenerationError,
    ProjectGenerator,
    make_context,
)
from nightexpress.services.rustfmt import RustfmtError, format_rust
from nightexpress.services.settings_store import SettingsStore
from nightexpress.views.dialogs.new_project_wizard import DEFAULT_NAME, NewProjectWizard
from nightexpress.views.file_tree_view import BUILD_DIR_NAME, HIDDEN_NAMES
from nightexpress.views.main_window import MainWindow, SaveChoice
from nightexpress.views.toast import ToastKind
from nightexpress.views.toolbar_view import board_label

DEFAULT_FILES = ("src/main.rs", "Cargo.toml")
EXTERNAL_CHECK_INTERVAL_MS = 1500
FIRST_RUN_TOUR_DELAY_MS = 400  # let the editor page lay out first
# Not offered by "go to file": build output, VCS data and files the editor cannot show.
# Build output is never offered, even while the tree shows the target folder.
QUICK_OPEN_IGNORED = HIDDEN_NAMES | {"Cargo.lock"}
QUICK_OPEN_ROOT_IGNORED = {BUILD_DIR_NAME}  # src/target/ is the user's module
QUICK_OPEN_IGNORED_SUFFIXES = (".bin", ".elf", ".hex", ".png", ".jpg", ".pdf")


def free_project_name(base: str, location: Path) -> str:
    """``base``, or ``base_2``, ``base_3``… — the first name whose folder is free or empty."""

    def taken(target: Path) -> bool:
        try:
            return target.exists() and (not target.is_dir() or any(target.iterdir()))
        except OSError:  # cannot look inside: do not offer it
            return True

    name, number = base, 1
    while taken(location / name):
        number += 1
        name = f"{base}_{number}"
    return name


class ProjectController(QObject):
    def __init__(
        self,
        window: MainWindow,
        app_model: AppModel,
        project_model: ProjectModel,
        catalog: BoardCatalog,
        settings: SettingsStore,
        generator: ProjectGenerator | None = None,
        config_repository: ProjectConfigRepository | None = None,
        files: FileService | None = None,
        rustfmt_command: Callable[[], Sequence[str] | None] | None = None,
        tool_env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._app = app_model
        self._project = project_model
        self._catalog = catalog
        self._settings = settings
        self._generator = generator or ProjectGenerator()
        self._files = files or FileService()
        self._config = config_repository or ProjectConfigRepository(self._files)
        self._saved: dict[Path, str] = {}  # last text written to / read from disk
        self._signatures: dict[Path, FileSignature | None] = {}  # of the files when last seen
        self.wizard: NewProjectWizard | None = None
        self._cursors: dict[Path, tuple[int, int]] = {}  # last cursor position per open file
        self._rustfmt_command = rustfmt_command or (lambda: None)
        self._tool_env = tool_env  # PATH with ~/.cargo/bin for rustfmt (a GUI app lacks it)

        window.new_project_requested.connect(lambda: self.new_project())
        window.open_project_requested.connect(self.open_project_dialog)
        window.open_recent_requested.connect(self.open_project)
        window.welcome.reveal_recent_requested.connect(window.reveal_in_file_manager)
        window.welcome.remove_recent_requested.connect(self.remove_recent)
        window.close_project_requested.connect(self.close_project)
        window.save_requested.connect(self.save)
        window.save_all_requested.connect(self.save_all)
        window.format_requested.connect(lambda: self.format_file())
        window.reveal_project_requested.connect(self._reveal_project)
        window.external_terminal_requested.connect(self._open_terminal)
        window.new_file_requested.connect(lambda: self.new_file())
        window.quick_open_requested.connect(self.quick_open)
        window.file_chosen.connect(self.open_file)
        tree = window.file_tree
        tree.new_file_requested.connect(self.new_file)
        tree.new_folder_requested.connect(self.new_folder)
        tree.rename_requested.connect(self.rename)
        tree.delete_requested.connect(self.delete)
        tree.set_show_service_files(settings.show_service_files())
        tree.show_service_changed.connect(settings.set_show_service_files)
        tree.set_show_build_dir(settings.show_build_dir())
        tree.show_build_dir_changed.connect(settings.set_show_build_dir)
        window.toolbar.profile_selected.connect(self.set_build_profile)
        window.settings_dialog.profile_selected.connect(self.set_build_profile)
        window.file_tree.file_activated.connect(self.open_file)
        tabs = window.editor_tabs
        tabs.content_changed.connect(self._on_content_changed)
        tabs.cursor_moved.connect(self._on_cursor_moved)
        tabs.tab_selected.connect(self._on_tab_selected)
        tabs.tab_close_requested.connect(self.close_file)
        tabs.close_others_requested.connect(self.close_other_files)
        tabs.close_all_requested.connect(lambda: self.close_files(self._project.open_files))
        project_model.dirty_changed.connect(tabs.set_dirty)
        project_model.dirty_changed.connect(tree.set_dirty)
        project_model.active_file_changed.connect(self._on_active_file_changed)
        window.confirm_close = self.confirm_quit
        # Files changed by other programs (git, cargo, another editor) are noticed by polling
        # the few open files: cheap, and it survives the atomic renames editors save with.
        self._external_timer = QTimer(self)
        self._external_timer.setInterval(EXTERNAL_CHECK_INTERVAL_MS)
        self._external_timer.timeout.connect(self.check_external_changes)
        self._external_timer.start()

    # --- new project -----------------------------------------------------------------------

    def new_project(self, example_id: str | None = None) -> NewProjectWizard:
        """Open the wizard; ``example_id`` comes from "File → Examples" and is preselected."""
        current = self._project.config
        location = self._settings.projects_directory()
        wizard = NewProjectWizard(
            self._window,
            self._catalog.boards,
            {c.id: c for c in self._catalog.chips},
            location,
            search=self._catalog.search,
            examples_for=lambda board_id: list(self._catalog.examples_for(board_id)),
            preview=self._preview,
            default_board_id=current.board_id if current else None,
            default_name=free_project_name(example_id or DEFAULT_NAME, location),
            default_example_id=example_id,
        )
        wizard.create_requested.connect(self._create)
        self.wizard = wizard
        wizard.open()
        return wizard

    def _context(self, request: NewProjectRequest) -> tuple[str, dict]:
        board = self._catalog.get_board(request.board_id)
        template = (
            "blank"
            if request.example_id is None
            else self._catalog.get_example(request.example_id).template
        )
        self._generator.check_board(template, board)
        context = make_context(request.name, board, self._catalog.chip_for(board))
        return template, context

    @staticmethod
    def _excluded(request: NewProjectRequest) -> tuple[str, ...]:
        return () if request.create_gitignore else (".gitignore",)

    def _preview(self, request: NewProjectRequest) -> list[str]:
        try:
            template, context = self._context(request)
            files = self._generator.render(template, context, self._excluded(request))
            return [str(f.path) for f in files]
        except (ProjectGenerationError, KeyError) as exc:
            return [f"⚠ {exc}"]

    def _create(self, request: NewProjectRequest) -> None:
        wizard = self.wizard
        try:
            template, context = self._context(request)
            self._generator.generate(
                template, request.project_dir, context, self._excluded(request)
            )
        except (ProjectGenerationError, KeyError) as exc:
            if wizard is not None:
                wizard.show_error(str(exc))
            return
        self._settings.set_projects_directory(request.location)
        self._window.set_projects_directory(request.location)
        if wizard is not None:
            wizard.accept_created()
        opened = self.open_project(
            request.project_dir, self._generator.template_info(template).open_on_create
        )
        if opened and not self._settings.first_run_tips_shown():
            # Once per installation, after the very first project: where to click next.
            self._settings.set_first_run_tips_shown(True)
            QTimer.singleShot(FIRST_RUN_TOUR_DELAY_MS, self._window.start_first_run_tour)

    # --- open / close ----------------------------------------------------------------------

    def open_project_dialog(self) -> None:
        start = self._project.config.path.parent if self._project.config else None
        path = self._window.ask_project_directory(start or self._settings.projects_directory())
        if path is not None:
            self.open_project(path)

    def open_project(self, path: Path, open_file: str | None = None) -> bool:
        current = self._project.config
        if current is not None and current.path == path:
            self._app.set_screen(Screen.EDITOR)
            return True
        if not path.is_dir():
            self._window.show_error(
                _("Проєкт не знайдено"), _("Теки {path} більше немає.").format(path=path)
            )
            self._app.remove_recent_project(path)
            self._settings.set_recent_projects(list(self._app.recent_projects))
            return False
        config: ProjectConfig | None = None
        board_id: str | None = None
        try:
            config = self._config.read(path)
        except MissingMetadataError as exc:
            board_id = self._ask_board(str(exc))
            if board_id is None:
                return False
        except (ProjectConfigError, FileServiceError) as exc:
            self._window.show_error(_("Не вдалося відкрити проєкт"), str(exc))
            return False
        if current is not None and not self.close_project():
            return False
        if board_id is not None:
            # Only now: a cancelled close must not leave the other project's Cargo.toml changed.
            try:
                board = self._catalog.get_board(board_id)
                self._config.write_metadata(path, board_id, board.default_upload_method)
                config = self._config.read(path)
            except (ProjectConfigError, FileServiceError) as exc:
                self._window.show_error(_("Не вдалося відкрити проєкт"), str(exc))
                return False
        assert config is not None

        self._project.open_project(config)
        profile = self._settings.project_build_profile(path)
        self._project.set_build_profile(profile)
        self._window.toolbar.set_profile(profile)
        self._settings.set_project_opened_at(path, datetime.now())
        self._app.add_recent_project(path)
        self._settings.set_recent_projects(list(self._app.recent_projects))
        self._window.set_project(config.name, path)
        self._show_board(config)
        self._app.set_screen(Screen.EDITOR)
        if open_file is not None or not self._restore_session(path):
            for relative in (open_file, *DEFAULT_FILES):
                if relative and (path / relative).is_file():
                    self.open_file(path / relative)
                    break
        return True

    # --- editor session (U8.2): open tabs, active file, cursors -------------------------------

    def save_session(self) -> None:
        config = self._project.config
        if config is None:
            return
        root = config.path

        def relative(path: Path) -> str | None:
            return path.relative_to(root).as_posix() if path.is_relative_to(root) else None

        opened = [r for p in self._project.open_files if (r := relative(p)) is not None]
        active = self._project.active_file
        self._settings.set_project_session(
            root,
            {
                "open": opened,
                "active": relative(active) if active is not None else None,
                "cursors": {
                    r: list(self._cursors[p])
                    for p in self._project.open_files
                    if p in self._cursors and (r := relative(p)) is not None
                },
            },
        )

    def _restore_session(self, root: Path) -> bool:
        """Reopen the tabs of the last visit; False when there is nothing to restore."""
        session = self._settings.project_session(root)
        opened = [
            root / r
            for r in session.get("open", [])
            if isinstance(r, str)
            and (root / r).is_file()
            and (root / r).resolve().is_relative_to(root.resolve())
        ]
        opened = [path for path in opened if self.open_file(path)]
        if not opened:
            return False
        cursors = session.get("cursors", {})
        active = session.get("active")
        target = (
            root / active if isinstance(active, str) and (root / active) in opened else opened[-1]
        )
        self.open_file(target)
        position = (
            cursors.get(target.relative_to(root).as_posix()) if isinstance(cursors, dict) else None
        )
        if (
            isinstance(position, list)
            and len(position) == 2
            and all(isinstance(v, int) and v > 0 for v in position)
        ):
            self._window.editor_tabs.reveal(target, position[0], position[1])
        return True

    def _on_cursor_moved(self, line: int, column: int) -> None:
        active = self._project.active_file
        if active is not None:
            self._cursors[active] = (line, column)

    def _ask_board(self, reason: str) -> str | None:
        return self._window.ask_board(
            _(
                "{reason}\n\nЦе запишеться в Cargo.toml у розділ [package.metadata.nightexpress]."
            ).format(reason=reason),
            self._catalog.boards,
            {c.id: c for c in self._catalog.chips},
            self._catalog.search,
        )

    def _show_board(self, config: ProjectConfig) -> None:
        try:
            board = self._catalog.get_board(config.board_id)
        except KeyError:
            self._window.set_board(None, None)
            self._window.show_error(
                _("Невідома плата"),
                _(
                    "Плата «{board_id}» з Cargo.toml невідома цій версії NightExpress. Редагувати "
                    "код можна, але зібрати й прошити — ні."
                ).format(board_id=config.board_id),
            )
            return
        self._window.set_board(board, self._catalog.chip_for(board))

    def close_project(self) -> bool:
        """Close the open project; returns False if the user cancelled."""
        if self._project.config is None:
            return True
        if not self._resolve_unsaved(list(self._project.dirty_files)):
            return False
        self.save_session()
        self._cursors.clear()
        for path in list(self._project.open_files):
            self._window.editor_tabs.close_file(path)
        self._project.close_project()
        self._saved.clear()
        self._signatures.clear()
        self._window.set_project(None, None)
        self._window.set_board(None, None)
        self._app.set_screen(Screen.WELCOME)
        return True

    def remove_recent(self, path: Path) -> None:
        """ "Remove from the list": the folder itself is not touched."""
        self._app.remove_recent_project(path)
        self._settings.set_recent_projects(list(self._app.recent_projects))

    def recent_projects(self) -> list[RecentProject]:
        """Recent projects with the chip and the last-opened date for the start screen."""
        result = []
        for path in self._app.recent_projects:
            chip_label = None
            try:
                board = self._catalog.get_board(self._config.read(path).board_id)
                chip_label = board_label(board)
            except (ProjectConfigError, FileServiceError, KeyError, ValueError):
                pass
            result.append(
                RecentProject(
                    path,
                    path.name,
                    chip_label,
                    self._settings.project_opened_at(path),
                    missing=not path.is_dir(),
                )
            )
        return result

    def set_build_profile(self, profile: BuildProfile) -> None:
        self._project.set_build_profile(profile)
        self._window.toolbar.set_profile(profile)
        if self._project.config is not None:
            self._settings.set_project_build_profile(self._project.config.path, profile)

    def show_settings(self) -> None:
        config = self._project.config
        if config is None:
            return
        try:
            board = self._catalog.get_board(config.board_id)
        except KeyError:
            label, target = config.board_id, "—"
        else:
            label, target = board_label(board), self._catalog.chip_for(board).target_triple
        self._window.settings_dialog.show_for_project(
            config, label, target, self._project.build_profile
        )

    # --- project tree: create, rename, delete ----------------------------------------------

    def _relative(self, path: Path) -> str:
        config = self._project.config
        if config is None or not path.is_relative_to(config.path):
            return str(path)
        return path.relative_to(config.path).as_posix()

    def _ask_new_path(self, title: str, directory: Path | None) -> Path | None:
        """Ask for a path relative to the project, pre-filled with ``directory``."""
        config = self._project.config
        if config is None:
            return None
        if directory is None:
            directory = config.path / "src" if (config.path / "src").is_dir() else config.path
        prefix = "" if directory == config.path else self._relative(directory) + "/"
        name = self._window.ask_text(title, _("Шлях відносно теки проєкту:"), prefix)
        if not name:
            return None
        target = (config.path / name).resolve()
        root = config.path.resolve()
        if target == root or not target.is_relative_to(root) or name.endswith("/"):
            self._window.show_error(
                title, _("Назва має вказувати на місце всередині теки проєкту.")
            )
            return None
        if target.exists():
            self._window.show_error(title, _("{name} уже існує.").format(name=name))
            return None
        return config.path / target.relative_to(root)

    def new_file(self, directory: Path | None = None) -> bool:
        target = self._ask_new_path(_("Новий файл"), directory)
        if target is None:
            return False
        try:
            self._files.check_name(target.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._files.write_text_atomic(target, "")
        except (OSError, FileServiceError) as exc:
            self._window.show_error(_("Новий файл"), str(exc))
            return False
        return self.open_file(target)

    def new_folder(self, directory: Path | None = None) -> bool:
        target = self._ask_new_path(_("Нова тека"), directory)
        if target is None:
            return False
        try:
            self._files.check_name(target.name)
            self._files.create_directory(target)
        except FileServiceError as exc:
            self._window.show_error(_("Нова тека"), str(exc))
            return False
        self._window.file_tree.reveal_path(target)
        return True

    def rename(self, path: Path, new_name: str) -> bool:
        """Rename a file or folder in place; open tabs follow it with their unsaved text."""
        config = self._project.config
        if config is None or path == config.path or new_name == path.name:
            return False
        try:
            self._files.check_name(new_name)
            target = path.with_name(new_name)
            self._files.rename(path, target)
        except FileServiceError as exc:
            self._window.show_error(_("Не вдалося перейменувати"), str(exc))
            return False
        self._move_open_files(path, target)
        self._window.file_tree.select_path(target)
        return True

    def delete(self, path: Path) -> bool:
        """Move a file or folder to the trash after a confirmation."""
        config = self._project.config
        if config is None or path == config.path or not path.exists():
            return False
        affected = self._open_files_under(path)
        unsaved = [p for p in affected if self._project.is_dirty(p)]
        details = _("Його можна буде відновити з кошика.")
        if path.is_dir():
            details = _("Її з усім вмістом можна буде відновити з кошика.")
        if unsaved:
            details += _(" Незбережені зміни буде втрачено.")
        if not self._window.ask_confirm(
            _("Видалення"),
            (
                _("Видалити теку «{path}»?") if path.is_dir() else _("Видалити файл «{path}»?")
            ).format(path=self._relative(path)),
            _("Видалити"),
            details,
        ):
            return False
        try:
            self._files.move_to_trash(path)
        except FileServiceError as exc:
            self._window.show_error(_("Не вдалося видалити"), str(exc))
            return False
        for file in affected:
            self._close_silently(file)
        return True

    def _open_files_under(self, path: Path) -> list[Path]:
        return [p for p in self._project.open_files if p == path or path in p.parents]

    def _close_silently(self, path: Path) -> None:
        self._project.set_dirty(path, False)
        self._window.editor_tabs.close_file(path)
        self._project.close_file(path)
        self._saved.pop(path, None)
        self._signatures.pop(path, None)
        # Tabs can be dragged, so the tab bar and the model may pick different neighbours of
        # a closed tab: the file on screen is the active one.
        shown = self._window.editor_tabs.current_path
        if shown is not None and shown in self._project.open_files:
            self._project.set_active_file(shown)

    def _move_open_files(self, old: Path, new: Path) -> None:
        active = self._project.active_file
        new_active: Path | None = None
        tabs = self._window.editor_tabs
        for source in self._open_files_under(old):
            target = new if source == old else new / source.relative_to(old)
            text = tabs.text(source)
            dirty = self._project.is_dirty(source)
            cursor = self._cursors.pop(source, None)
            self._close_silently(source)
            if not self.open_file(target):
                continue
            if dirty and text is not None:
                tabs.replace_text(target, text)
                self._project.set_dirty(target, True)
            if cursor is not None:  # the same place in the file under its new name
                tabs.reveal(target, *cursor)
                self._cursors[target] = cursor
            if source == active:
                new_active = target
        back_to = new_active or active
        if back_to is not None and back_to in self._project.open_files:
            self.open_file(back_to)

    # --- quick open ------------------------------------------------------------------------

    def quick_open_files(self) -> list[Path]:
        """Project files for "go to file": the open ones first, then the rest by path."""
        config = self._project.config
        if config is None:
            return []
        files = [
            f
            for f in self._files.list_files(
                config.path, QUICK_OPEN_IGNORED, root_ignored_names=QUICK_OPEN_ROOT_IGNORED
            )
            if f.suffix.lower() not in QUICK_OPEN_IGNORED_SUFFIXES
        ]
        opened = [f for f in self._project.open_files if f in files]
        # Sources before configuration: that is what people jump to.
        rest = sorted((f for f in files if f not in opened), key=lambda f: f.suffix != ".rs")
        return [*opened, *rest]

    def quick_open(self) -> None:
        config = self._project.config
        if config is not None:
            self._window.show_quick_open(config.path, self.quick_open_files())

    def confirm_quit(self) -> bool:
        return self._resolve_unsaved(list(self._project.dirty_files))

    # --- files -----------------------------------------------------------------------------

    def open_file(self, path: Path) -> bool:
        if path in self._project.open_files:
            self._project.set_active_file(path)
            self._window.editor_tabs.set_current(path)
            return True
        try:
            text = self._files.read_text(path)
        except FileServiceError as exc:
            self._window.show_error(_("Не вдалося відкрити файл"), str(exc))
            return False
        self._saved[path] = text
        self._signatures[path] = self._files.signature(path)
        self._project.open_file(path)
        self._window.editor_tabs.open_file(path, text)
        return True

    def close_file(self, path: Path) -> bool:
        if path in self._project.dirty_files and not self._resolve_unsaved([path]):
            return False
        self._close_silently(path)
        return True

    def close_files(self, paths: Iterable[Path]) -> bool:
        """Close several tabs with one question about all their unsaved changes."""
        targets = [p for p in paths if p in self._project.open_files]
        if not self._resolve_unsaved([p for p in targets if self._project.is_dirty(p)]):
            return False
        for path in targets:
            self._close_silently(path)
        return True

    def close_other_files(self, keep: Path) -> bool:
        closed = self.close_files(p for p in self._project.open_files if p != keep)
        if closed and keep in self._project.open_files:
            self.open_file(keep)
        return closed

    # --- formatting (U8.3) --------------------------------------------------------------------

    def format_file(self, path: Path | None = None, quiet: bool = False) -> bool:
        """rustfmt the editor text of ``path`` (default: the active file); True on success.

        ``quiet`` (format on save) reports problems in the status bar instead of a toast.
        """
        target = path or self._project.active_file
        config = self._project.config
        if target is None or config is None:
            return False
        if target.suffix != ".rs":
            if not quiet:
                self._window.show_message(_("Форматування — лише для файлів .rs"))
            return False
        text = self._window.editor_tabs.text(target)
        command = self._rustfmt_command()
        if text is None:
            return False
        if not command:
            message = _("Не знайдено rustfmt. Встановіть: rustup component add rustfmt")
            self._report_format_error(message, quiet)
            return False
        try:
            formatted = format_rust(text, command, config.path, self._tool_env)
        except RustfmtError as exc:
            self._report_format_error(str(exc), quiet)
            return False
        if formatted != text:
            self._window.editor_tabs.editor.replace_text(target, formatted)  # one undo step
            self._on_content_changed(target, formatted)
        return True

    def _report_format_error(self, message: str, quiet: bool) -> None:
        if quiet:
            self._window.show_message(message)
        else:
            self._window.notify(_("Форматування"), message, ToastKind.ERROR)

    def save(self, path: Path | None = None) -> bool:
        target = path or self._project.active_file
        if target is None:
            return True
        if self._settings.format_on_save() and target.suffix == ".rs":
            self.format_file(target, quiet=True)  # a syntax error must not block saving
        text = self._window.editor_tabs.text(target)
        if text is None:
            return True
        try:
            self._files.write_text_atomic(target, text)
        except FileServiceError as exc:
            self._window.show_error(_("Не вдалося зберегти"), str(exc))
            return False
        self._saved[target] = text
        self._signatures[target] = self._files.signature(target)
        self._project.set_dirty(target, False)
        return True

    def save_all(self) -> bool:
        return all([self.save(path) for path in list(self._project.dirty_files)])

    def prepare_build(self) -> bool:
        """Before a build (U8.5): save the files, unless the user turned that off."""
        if self._settings.save_before_build():
            return self.save_all()
        if self._project.dirty_files:
            self._window.show_message(_("Є незбережені зміни — збирається версія з диска"))
        return True

    def _resolve_unsaved(self, paths: list[Path]) -> bool:
        """Ask about unsaved files; True means it is fine to continue."""
        if not paths:
            return True
        choice = self._window.ask_save_changes(sorted(p.name for p in paths))
        if choice is SaveChoice.CANCEL:
            return False
        if choice is SaveChoice.SAVE:
            return all([self.save(p) for p in paths])
        for path in paths:  # discard
            self._project.set_dirty(path, False)
        return True

    # --- changes made by other programs ----------------------------------------------------

    def check_external_changes(self) -> None:
        """Pick up open files that another program changed on disk.

        A file without unsaved changes is re-read quietly; one with unsaved changes is left
        alone and the user is offered to re-read it (saving would overwrite the other version).
        """
        for path in list(self._project.open_files):
            signature = self._files.signature(path)
            if signature is None or signature == self._signatures.get(path):
                continue  # unchanged, or deleted: the text stays in the editor
            self._signatures[path] = signature
            try:
                text = self._files.read_text(path)
            except FileServiceError:
                continue
            if text == self._saved.get(path):
                continue
            self._saved[path] = text
            if not self._project.is_dirty(path):
                self._window.editor_tabs.replace_text(path, text)
                self._window.show_message(_("{name} оновлено з диска").format(name=path.name))
            elif self._window.editor_tabs.text(path) == text:
                self._project.set_dirty(path, False)
            else:
                self._window.notify(
                    _("Файл змінено на диску"),
                    _(
                        "{path} змінила інша програма, а тут є незбережені зміни. Збереження "
                        "замінить версію з диска."
                    ).format(path=self._relative(path)),
                    ToastKind.WARNING,
                    _("Перечитати з диска"),
                    lambda p=path: self.reload_from_disk(p),
                )

    def reload_from_disk(self, path: Path) -> None:
        """Drop the unsaved changes of ``path`` and show what is on disk."""
        if path not in self._project.open_files or path not in self._saved:
            return
        self._window.editor_tabs.replace_text(path, self._saved[path])
        self._project.set_dirty(path, False)

    def _on_content_changed(self, path: Path, text: str) -> None:
        if path in self._project.open_files:
            self._project.set_dirty(path, text != self._saved.get(path))

    def _on_tab_selected(self, path: Path) -> None:
        if path in self._project.open_files:
            self._project.set_active_file(path)

    def _on_active_file_changed(self, path: Path | None) -> None:
        if path is not None:
            self._window.file_tree.select_path(path)

    def _open_terminal(self) -> None:
        if self._project.config is not None:
            self._window.open_terminal(self._project.config.path)

    def _reveal_project(self) -> None:
        if self._project.config is not None:
            self._window.reveal_in_file_manager(self._project.config.path)
