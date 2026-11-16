"""GPIO pin names such as ``PC13``."""

from __future__ import annotations

import re

_PIN_RE = re.compile(r"^P[A-K](?:[0-9]|1[0-5])$")


def is_valid_pin(name: str) -> bool:
    """Return True for STM32 pin names ``PA0`` .. ``PK15``."""
    return bool(_PIN_RE.match(name))


def require_pin(name: str, what: str) -> None:
    if not is_valid_pin(name):
        raise ValueError(f"{what}: invalid pin name {name!r} (expected e.g. 'PC13')")
