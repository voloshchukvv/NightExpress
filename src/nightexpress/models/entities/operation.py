"""Lifecycle of a long-running operation (build, upload, ...)."""

from __future__ import annotations

from enum import Enum


class OperationState(Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_busy(self) -> bool:
        return self in (OperationState.PREPARING, OperationState.RUNNING)

    @property
    def is_finished(self) -> bool:
        return self in (OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED)

    def can_transition_to(self, new: OperationState) -> bool:
        return new in _TRANSITIONS[self]


_START = frozenset({OperationState.PREPARING, OperationState.RUNNING})
_END = frozenset({OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED})

# idle -> preparing -> running -> succeeded | failed | cancelled; a finished operation may restart.
_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.IDLE: _START,
    OperationState.PREPARING: frozenset(
        {OperationState.RUNNING, OperationState.FAILED, OperationState.CANCELLED}
    ),
    OperationState.RUNNING: _END,
    OperationState.SUCCEEDED: _START | {OperationState.IDLE},
    OperationState.FAILED: _START | {OperationState.IDLE},
    OperationState.CANCELLED: _START | {OperationState.IDLE},
}
