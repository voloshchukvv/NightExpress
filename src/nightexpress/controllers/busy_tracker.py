"""Keep commands disabled while any long operation (build, upload) is running."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject, SignalInstance

from nightexpress.models.entities import OperationState
from nightexpress.models.state import UploadModel
from nightexpress.views.main_window import MainWindow


class _Operation(Protocol):
    state_changed: SignalInstance

    @property
    def state(self) -> OperationState: ...


class BusyTracker(QObject):
    def __init__(self, window: MainWindow, *operations: _Operation) -> None:
        super().__init__(window)
        self._window = window
        self._operations = operations
        for operation in operations:
            operation.state_changed.connect(self._update)

    @property
    def busy(self) -> bool:
        return any(op.state.is_busy for op in self._operations)

    @property
    def operation(self) -> str | None:
        """Which toolbar command is running: an upload also covers the build inside it."""
        if not self.busy:
            return None
        uploading = any(isinstance(op, UploadModel) and op.state.is_busy for op in self._operations)
        return "upload" if uploading else "verify"

    def _update(self, *_args: object) -> None:
        self._window.set_busy(self.busy, self.operation)
