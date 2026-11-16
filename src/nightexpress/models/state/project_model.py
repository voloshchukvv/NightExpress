"""The open project: its configuration, open editor files and unsaved changes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal

from nightexpress.models.entities import BuildProfile, ProjectConfig
from nightexpress.models.state.base import ObservableModel


class ProjectModel(ObservableModel):
    config_changed = Signal(object)  # ProjectConfig | None
    open_files_changed = Signal(object)  # tuple[Path, ...]
    active_file_changed = Signal(object)  # Path | None
    dirty_changed = Signal(object, bool)  # (Path, is_dirty)
    build_profile_changed = Signal(object)  # BuildProfile

    def __init__(self) -> None:
        super().__init__()
        self._config: ProjectConfig | None = None
        self._open_files: tuple[Path, ...] = ()
        self._active_file: Path | None = None
        self._dirty: frozenset[Path] = frozenset()
        self._build_profile = BuildProfile.RELEASE

    # --- project ---------------------------------------------------------------------------

    @property
    def config(self) -> ProjectConfig | None:
        return self._config

    @property
    def is_open(self) -> bool:
        return self._config is not None

    def set_config(self, config: ProjectConfig | None) -> None:
        """Replace the configuration of the open project (e.g. after a board change)."""
        self._update("_config", config, self.config_changed)

    def open_project(self, config: ProjectConfig) -> None:
        self.close_project()
        self.set_config(config)

    def close_project(self) -> None:
        for path in list(self._open_files):
            self.close_file(path)
        self.set_config(None)

    @property
    def build_profile(self) -> BuildProfile:
        return self._build_profile

    def set_build_profile(self, profile: BuildProfile) -> None:
        self._update("_build_profile", profile, self.build_profile_changed)

    # --- files -----------------------------------------------------------------------------

    @property
    def open_files(self) -> tuple[Path, ...]:
        return self._open_files

    @property
    def active_file(self) -> Path | None:
        return self._active_file

    def open_file(self, path: Path) -> None:
        if path not in self._open_files:
            self._update("_open_files", (*self._open_files, path), self.open_files_changed)
        self.set_active_file(path)

    def close_file(self, path: Path) -> None:
        if path not in self._open_files:
            return
        index = self._open_files.index(path)
        remaining = tuple(p for p in self._open_files if p != path)
        self.set_dirty(path, False)
        self._update("_open_files", remaining, self.open_files_changed)
        if self._active_file == path:
            neighbour = remaining[min(index, len(remaining) - 1)] if remaining else None
            self._update("_active_file", neighbour, self.active_file_changed)

    def set_active_file(self, path: Path | None) -> None:
        if path is not None and path not in self._open_files:
            raise ValueError(f"{path} is not open")
        self._update("_active_file", path, self.active_file_changed)

    # --- unsaved changes -------------------------------------------------------------------

    @property
    def dirty_files(self) -> frozenset[Path]:
        return self._dirty

    @property
    def has_unsaved_changes(self) -> bool:
        return bool(self._dirty)

    def is_dirty(self, path: Path) -> bool:
        return path in self._dirty

    def set_dirty(self, path: Path, dirty: bool) -> None:
        if (path in self._dirty) == dirty:
            return
        self._dirty = self._dirty | {path} if dirty else self._dirty - {path}
        self.dirty_changed.emit(path, dirty)
