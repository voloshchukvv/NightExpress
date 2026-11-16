"""Check the Rust/embedded toolchain in the background and show the result."""

from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from nightexpress.i18n import N_, Text, _
from nightexpress.models.entities import ToolStatus
from nightexpress.models.state import ToolchainModel
from nightexpress.services.toolchain_probe import ToolchainProbe
from nightexpress.views.dialogs.toolchain_dialog import ToolchainDialog
from nightexpress.views.main_window import MainWindow

log = logging.getLogger(__name__)

CHECK_FAILED_TEXT = N_("Rust: перевірка не вдалася")


class _Emitter(QObject):
    done = Signal(object)  # tuple[ToolStatus, ...]
    failed = Signal()


class _CheckTask(QRunnable):
    def __init__(self, probe: ToolchainProbe, emitter: _Emitter) -> None:
        super().__init__()
        self._probe = probe
        self._emitter = emitter

    def run(self) -> None:
        try:
            statuses = self._probe.check_all()
        except Exception:  # a bug here must not leave "checking…" on screen forever
            log.exception("toolchain check crashed")
            with contextlib.suppress(RuntimeError):
                self._emitter.failed.emit()
            return
        # The window (and the emitter with it) may have been closed while we were probing.
        with contextlib.suppress(RuntimeError):
            self._emitter.done.emit(statuses)


def summary(statuses: tuple[ToolStatus, ...]) -> tuple[str, bool]:
    """One line for the status bar (user-facing, Ukrainian)."""
    missing = [s.name for s in statuses if s.is_blocking and s.path is None]
    broken = [s.name for s in statuses if s.is_blocking and s.path is not None]
    if missing:
        return _("Rust: не знайдено {missing}").format(missing=", ".join(missing)), False
    if broken:  # installed, but e.g. rustup has no default toolchain
        return _("Rust: не працює {broken}").format(broken=", ".join(broken)), False
    return _("Rust: готовий"), True


class ToolchainController(QObject):
    finished = Signal()

    def __init__(
        self,
        model: ToolchainModel,
        probe: ToolchainProbe,
        window: MainWindow,
        pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(window)
        self._model = model
        self._probe = probe
        self._window = window
        self._pool = pool or QThreadPool.globalInstance()
        self._emitter = _Emitter(self)
        self._emitter.done.connect(self._on_done)
        self._emitter.failed.connect(self._on_failed)
        self.dialog: ToolchainDialog | None = None
        window.check_tools_requested.connect(self.check)
        window.tools_dialog_requested.connect(self.show_dialog)

    @property
    def search_path(self) -> str:
        return self._probe.search_path

    def show_dialog(self) -> ToolchainDialog:
        """Show every tool with how to install the missing ones, and check them again."""
        if self.dialog is None:
            dialog = ToolchainDialog(self._window)
            dialog.recheck_requested.connect(self.check)
            dialog.open_url_requested.connect(self._window.open_url)
            dialog.command_copied.connect(
                lambda _command: self._window.show_message(_("Команду скопійовано"))
            )
            self.dialog = dialog
        if self._model.statuses:
            self.dialog.set_statuses(self._model.statuses)
        self.dialog.open()  # a sheet of the main window, like the other help pages
        self.check()  # tools may have been installed since the last check
        return self.dialog

    def check(self) -> None:
        if self._model.checking:
            return
        self._model.set_checking(True)
        self._window.welcome.set_checking(True)
        self._window.settings_dialog.application.set_checking(True)
        if self.dialog is not None:
            self.dialog.set_checking(True)
        self._window.set_tools_status(N_("Rust: перевірка…"), ok=True)
        self._pool.start(_CheckTask(self._probe, self._emitter))

    def _on_failed(self) -> None:
        """The check itself broke: keep what was known before and say so."""
        self._show(self._model.statuses, CHECK_FAILED_TEXT, ok=False)

    def _on_done(self, statuses: tuple[ToolStatus, ...]) -> None:
        self._model.set_statuses(statuses)
        _text, ok = summary(statuses)
        self._show(statuses, lambda: summary(statuses)[0], ok)

    def _show(self, statuses: tuple[ToolStatus, ...], text: Text, ok: bool) -> None:
        self._model.set_checking(False)
        self._window.welcome.set_tool_statuses(statuses)
        preferences = self._window.settings_dialog.application
        preferences.set_statuses(statuses)
        preferences.set_checking(False)
        if self.dialog is not None:
            self.dialog.set_statuses(statuses)
        self._window.set_tools_status(text, ok)
        self.finished.emit()
