"""State of the current/last build: log, diagnostics and memory usage."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from PySide6.QtCore import Signal

from nightexpress.models.entities import (
    BuildSummary,
    Diagnostic,
    DiagnosticLevel,
    MemoryUsage,
    OperationState,
)
from nightexpress.models.state.base import ObservableModel

MAX_LOG_LINES = 5000


class BuildModel(ObservableModel):
    state_changed = Signal(object)  # OperationState
    log_appended = Signal(str)
    log_cleared = Signal()
    diagnostics_changed = Signal(object)  # tuple[Diagnostic, ...]
    memory_usage_changed = Signal(object)  # MemoryUsage | None
    summary_changed = Signal(object)  # BuildSummary | None

    def __init__(self) -> None:
        super().__init__()
        self._state = OperationState.IDLE
        self._log: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self._diagnostics: tuple[Diagnostic, ...] = ()
        self._memory_usage: MemoryUsage | None = None
        self._summary: BuildSummary | None = None

    @property
    def state(self) -> OperationState:
        return self._state

    def set_state(self, state: OperationState) -> None:
        self._transition("_state", state, self.state_changed)

    # --- log -------------------------------------------------------------------------------

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

    # --- results ---------------------------------------------------------------------------

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self._diagnostics

    def set_diagnostics(self, diagnostics: Iterable[Diagnostic]) -> None:
        self._update("_diagnostics", tuple(diagnostics), self.diagnostics_changed)

    def add_diagnostic(self, diagnostic: Diagnostic) -> None:
        self.set_diagnostics((*self._diagnostics, diagnostic))

    def count(self, level: DiagnosticLevel) -> int:
        return sum(1 for d in self._diagnostics if d.level is level)

    @property
    def memory_usage(self) -> MemoryUsage | None:
        return self._memory_usage

    def set_memory_usage(self, usage: MemoryUsage | None) -> None:
        self._update("_memory_usage", usage, self.memory_usage_changed)

    @property
    def summary(self) -> BuildSummary | None:
        return self._summary

    def set_summary(self, summary: BuildSummary | None) -> None:
        self._update("_summary", summary, self.summary_changed)

    def reset_results(self) -> None:
        """Forget the previous run before starting a new one."""
        self.clear_log()
        self.set_diagnostics(())
        self.set_memory_usage(None)
        self.set_summary(None)
