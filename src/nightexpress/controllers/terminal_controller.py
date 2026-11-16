"""TerminalController: one shell per open project in the "Terminal" tab.

The shell is started lazily — the first time the tab is shown — in the project folder, so
opening a project does not spawn a process nobody uses. Switching to another project restarts
it there; closing the project (or the app) stops it with everything started from it. When the
shell exits, Enter starts a new one, as in VS Code.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject

from nightexpress.i18n import _
from nightexpress.models.entities import ProjectConfig
from nightexpress.models.state import ProjectModel
from nightexpress.services.pty_session import PtyError, PtySession
from nightexpress.views.main_window import TERMINAL_TAB, MainWindow

DIM, RESET = "\x1b[2m", "\x1b[0m"


class TerminalController(QObject):
    def __init__(
        self,
        window: MainWindow,
        project_model: ProjectModel,
        session: PtySession | None = None,
        search_path: str | None = None,
        shell: Sequence[str] | None = None,
    ) -> None:
        """``shell``: the command to run instead of the user's login shell (tests)."""
        super().__init__(window)
        self._window = window
        self._view = window.terminal_panel
        self._session = session or PtySession(self)
        self._search_path = search_path
        self._shell = list(shell) if shell else None
        self._cwd: Path | None = None  # the project folder the shell belongs to
        self._exited = False  # the shell ended by itself: Enter starts a new one

        self._view.input_received.connect(self._on_input)
        self._view.resized.connect(self._session.resize)
        self._session.output.connect(self._view.write)
        self._session.exited.connect(self._on_exited)
        project_model.config_changed.connect(self._on_project_changed)
        window.bottom_tabs.currentChanged.connect(self._on_tab_changed)
        self._on_project_changed(project_model.config)

    @property
    def session(self) -> PtySession:
        return self._session

    def shutdown(self) -> None:
        """Application exit: stop the shell and its children."""
        self._session.close()

    # --- internals -------------------------------------------------------------------------

    def _terminal_shown(self) -> bool:
        return self._window.bottom_tabs.currentIndex() == TERMINAL_TAB

    def _on_project_changed(self, config: ProjectConfig | None) -> None:
        path = config.path if config is not None else None
        if path == self._cwd:
            return  # e.g. only the board changed
        self._session.close()
        self._exited = False
        self._cwd = path
        self._view.reset()
        if path is not None and self._terminal_shown():
            self._start()

    def _on_tab_changed(self, index: int) -> None:
        if index != TERMINAL_TAB:
            return
        if self._cwd is not None and not self._session.running and not self._exited:
            self._start()
        self._view.focus_terminal()

    def _start(self) -> None:
        if self._cwd is None:
            return
        cols, rows = self._view.size
        try:
            self._session.start(
                self._cwd, cols, rows, argv=self._shell, search_path=self._search_path
            )
        except PtyError as exc:
            self._view.write_text(f"{exc}\n")
            return
        self._exited = False

    def _on_input(self, data: bytes) -> None:
        if self._session.running:
            self._session.write(data)
        elif self._exited and b"\r" in data:
            self._view.reset()
            self._start()

    def _on_exited(self, code: int) -> None:
        self._exited = True
        text = _(
            "Процес завершено (код {code}). Натисніть Enter, щоб запустити термінал знову."
        ).format(code=code)
        self._view.write_text(f"\n{DIM}{text}{RESET}\n")
