"""State of the current/last firmware upload, with its own log ("Flashing" tab)."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import Signal

from nightexpress.models.entities import BuildSummary, OperationState, UploadProgress
from nightexpress.models.state.base import ObservableModel

MAX_LOG_LINES = 2000


class UploadModel(ObservableModel):
    state_changed = Signal(object)  # OperationState
    progress_changed = Signal(object)  # UploadProgress | None
    message_changed = Signal(str)  # result/hint for the user, e.g. "set BOOT0 back to 0"
    log_appended = Signal(str)
    log_cleared = Signal()
    summary_changed = Signal(object)  # BuildSummary | None

    def __init__(self) -> None:
        super().__init__()
        self._state = OperationState.IDLE
        self._progress: UploadProgress | None = None
        self._message = ""
        self._log: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self._summary: BuildSummary | None = None

    @property
    def state(self) -> OperationState:
        return self._state

    def set_state(self, state: OperationState) -> None:
        self._transition("_state", state, self.state_changed)

    @property
    def progress(self) -> UploadProgress | None:
        return self._progress

    def set_progress(self, progress: UploadProgress | None) -> None:
        self._update("_progress", progress, self.progress_changed)

    @property
    def message(self) -> str:
        return self._message

    def set_message(self, message: str) -> None:
        self._update("_message", message, self.message_changed)

    @property
    def log(self) -> tuple[str, ...]:
        return tuple(self._log)

    def append_log(self, line: str) -> None:
        self._log.append(line)
        self.log_appended.emit(line)

    def clear_log(self) -> None:
        if self._log:
            self._log.clear()
            self.log_cleared.emit()

    @property
    def summary(self) -> BuildSummary | None:
        return self._summary

    def set_summary(self, summary: BuildSummary | None) -> None:
        self._update("_summary", summary, self.summary_changed)

    def reset_results(self) -> None:
        self.clear_log()
        self.set_progress(None)
        self.set_message("")
        self.set_summary(None)
