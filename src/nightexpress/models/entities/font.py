"""Text areas whose font size the user can set, together or one by one."""

from __future__ import annotations

from enum import StrEnum

FONT_SIZE_MIN = 9
FONT_SIZE_MAX = 28
DEFAULT_FONT_SIZE = 14


class FontArea(StrEnum):
    EDITOR = "editor"  # the code editor
    TERMINAL = "terminal"
    UART = "uart"  # the UART monitor
    LOGS = "logs"  # build and flash logs


def clamp_font_size(size: int) -> int:
    return min(max(size, FONT_SIZE_MIN), FONT_SIZE_MAX)
