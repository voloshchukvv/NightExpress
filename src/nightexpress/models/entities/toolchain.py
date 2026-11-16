"""Status of an external tool (cargo, rustup, probe-rs, ...)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ToolStatus:
    name: str
    found: bool
    version: str | None = None
    path: Path | None = None
    install_hint: str = ""
    required: bool = True
    install_command: str = ""  # to paste into a terminal ("" when there is none)
    help_url: str = ""  # installation instructions

    @property
    def is_blocking(self) -> bool:
        return self.required and not self.found
