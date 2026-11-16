"""Available upload targets (ST-Link probes and USB-serial adapters) and the selected one.

The model never auto-selects: with several adapters attached the choice belongs to the user.
Remembering the choice per project is the controller's job.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Signal

from nightexpress.models.entities import ProbeInfo, SerialPortInfo, UploadTarget
from nightexpress.models.state.base import ObservableModel


class PortsModel(ObservableModel):
    targets_changed = Signal(object)  # tuple[UploadTarget, ...]
    selection_changed = Signal(object)  # UploadTarget | None

    def __init__(self) -> None:
        super().__init__()
        self._targets: tuple[UploadTarget, ...] = ()
        self._selected_id: str | None = None

    @property
    def targets(self) -> tuple[UploadTarget, ...]:
        return self._targets

    @property
    def probes(self) -> tuple[ProbeInfo, ...]:
        return tuple(t for t in self._targets if isinstance(t, ProbeInfo))

    @property
    def serial_ports(self) -> tuple[SerialPortInfo, ...]:
        return tuple(t for t in self._targets if isinstance(t, SerialPortInfo))

    def find(self, stable_id: str) -> UploadTarget | None:
        return next((t for t in self._targets if t.stable_id == stable_id), None)

    def set_targets(self, targets: Iterable[UploadTarget]) -> None:
        new = tuple(targets)
        ids = [t.stable_id for t in new]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate upload target ids")
        previous = self.selected
        if not self._update("_targets", new, self.targets_changed):
            return
        if self._selected_id is not None and self.find(self._selected_id) is None:
            self._selected_id = None
        if self.selected != previous:
            self.selection_changed.emit(self.selected)

    @property
    def selected(self) -> UploadTarget | None:
        return None if self._selected_id is None else self.find(self._selected_id)

    def select(self, stable_id: str | None) -> None:
        if stable_id is not None and self.find(stable_id) is None:
            raise ValueError(f"unknown upload target {stable_id!r}")
        if stable_id == self._selected_id:
            return
        self._selected_id = stable_id
        self.selection_changed.emit(self.selected)
