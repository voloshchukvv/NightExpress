"""Progress reporting for firmware uploads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UploadStage(Enum):
    WAITING_FOR_BOOT_MODE = "waiting_for_boot_mode"  # serial bootloader: BOOT0 = 1 + reset
    CONNECTING = "connecting"
    CHECKING_CHIP = "checking_chip"
    ERASING = "erasing"
    PROGRAMMING = "programming"
    VERIFYING = "verifying"
    RESETTING = "resetting"


@dataclass(frozen=True)
class UploadProgress:
    stage: UploadStage
    percent: int | None = None  # None = indeterminate

    def __post_init__(self) -> None:
        if self.percent is not None and not 0 <= self.percent <= 100:
            raise ValueError(f"percent must be in 0..100, got {self.percent}")
