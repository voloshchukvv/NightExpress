"""Shared helpers for observable state models."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, SignalInstance

from nightexpress.models.entities import OperationState


class ObservableModel(QObject):
    """Base class: assign an attribute and emit its signal only when the value really changes."""

    def _update(self, attr: str, value: Any, signal: SignalInstance) -> bool:
        if getattr(self, attr) == value:
            return False
        setattr(self, attr, value)
        signal.emit(value)
        return True

    def _transition(self, attr: str, new: OperationState, signal: SignalInstance) -> bool:
        current: OperationState = getattr(self, attr)
        if current is new:
            return False
        if not current.can_transition_to(new):
            raise ValueError(f"invalid state transition {current.value} -> {new.value}")
        return self._update(attr, new, signal)
