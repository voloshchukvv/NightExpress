"""Example (project template) definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nightexpress.i18n import localized


@dataclass(frozen=True)
class ExampleDefinition:
    id: str  # e.g. "blink"
    title: str
    descriptions: Mapping[str, str]  # {language: description}
    template: str  # directory name under resources/templates
    board_ids: tuple[str, ...]  # boards this example has been checked on

    @property
    def description(self) -> str:
        """The description in the current interface language."""
        return localized(self.descriptions)

    def __post_init__(self) -> None:
        if not self.id or not self.template:
            raise ValueError("example id and template must be non-empty")
        if not self.board_ids:
            raise ValueError(f"example {self.id!r}: board_ids is empty")
