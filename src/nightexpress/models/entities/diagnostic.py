"""Compiler diagnostics and firmware memory usage."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DiagnosticLevel(Enum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"
    HELP = "help"


@dataclass(frozen=True)
class Diagnostic:
    level: DiagnosticLevel
    message: str
    file: Path | None = None
    line: int | None = None  # 1-based
    column: int | None = None  # 1-based
    code: str | None = None  # e.g. "E0425"
    rendered: str = ""  # full human-readable text from rustc

    def __post_init__(self) -> None:
        for name in ("line", "column"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} must be 1-based, got {value}")

    @property
    def has_location(self) -> bool:
        return self.file is not None and self.line is not None


@dataclass(frozen=True)
class MemoryUsage:
    flash_used: int
    flash_total: int
    ram_used: int
    ram_total: int

    def __post_init__(self) -> None:
        if min(self.flash_used, self.ram_used) < 0:
            raise ValueError("memory usage cannot be negative")
        if self.flash_total <= 0 or self.ram_total <= 0:
            raise ValueError("memory totals must be positive")

    @property
    def flash_percent(self) -> float:
        return 100.0 * self.flash_used / self.flash_total

    @property
    def ram_percent(self) -> float:
        return 100.0 * self.ram_used / self.ram_total

    @property
    def fits(self) -> bool:
        return self.flash_used <= self.flash_total and self.ram_used <= self.ram_total


@dataclass(frozen=True)
class BuildSummary:
    """Final line of a build or upload log, e.g. "Build finished successfully (6.21 s)"."""

    success: bool
    message: str
