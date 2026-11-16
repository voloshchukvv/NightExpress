"""Availability of external tools (cargo, rustup, probe-rs, ...)."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Signal

from nightexpress.models.entities import ToolStatus
from nightexpress.models.state.base import ObservableModel


class ToolchainModel(ObservableModel):
    status_changed = Signal(object)  # ToolStatus
    checking_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._statuses: dict[str, ToolStatus] = {}
        self._checking = False

    @property
    def statuses(self) -> tuple[ToolStatus, ...]:
        return tuple(self._statuses.values())

    def status(self, name: str) -> ToolStatus | None:
        return self._statuses.get(name)

    def set_status(self, status: ToolStatus) -> None:
        if self._statuses.get(status.name) == status:
            return
        self._statuses[status.name] = status
        self.status_changed.emit(status)

    def set_statuses(self, statuses: Iterable[ToolStatus]) -> None:
        for status in statuses:
            self.set_status(status)

    @property
    def is_ready(self) -> bool:
        """True when every required tool has been found."""
        return bool(self._statuses) and not any(s.is_blocking for s in self._statuses.values())

    @property
    def checking(self) -> bool:
        return self._checking

    def set_checking(self, checking: bool) -> None:
        self._update("_checking", checking, self.checking_changed)
