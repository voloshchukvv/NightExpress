"""Application-level state: which screen is shown and the recent projects list."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Signal

from nightexpress.models.state.base import ObservableModel

MAX_RECENT_PROJECTS = 10


class Screen(Enum):
    WELCOME = "welcome"
    EDITOR = "editor"


class AppModel(ObservableModel):
    screen_changed = Signal(object)  # Screen
    recent_projects_changed = Signal(object)  # tuple[Path, ...]

    def __init__(self) -> None:
        super().__init__()
        self._screen = Screen.WELCOME
        self._recent_projects: tuple[Path, ...] = ()

    @property
    def screen(self) -> Screen:
        return self._screen

    def set_screen(self, screen: Screen) -> None:
        self._update("_screen", screen, self.screen_changed)

    @property
    def recent_projects(self) -> tuple[Path, ...]:
        return self._recent_projects

    def set_recent_projects(self, paths: Iterable[Path]) -> None:
        unique = tuple(dict.fromkeys(paths))[:MAX_RECENT_PROJECTS]
        self._update("_recent_projects", unique, self.recent_projects_changed)

    def add_recent_project(self, path: Path) -> None:
        """Move ``path`` to the top of the list."""
        self.set_recent_projects((path, *(p for p in self._recent_projects if p != path)))

    def remove_recent_project(self, path: Path) -> None:
        self.set_recent_projects(p for p in self._recent_projects if p != path)
