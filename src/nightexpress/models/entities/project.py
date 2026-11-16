"""Configuration of an open NightExpress project."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from nightexpress.i18n import _
from nightexpress.models.entities.board import UploadMethod


class BuildProfile(Enum):
    DEV = "dev"
    RELEASE = "release"

    @property
    def target_dir_name(self) -> str:
        """Directory name under target/<triple>/ that Cargo uses for this profile."""
        return "debug" if self is BuildProfile.DEV else "release"


@dataclass(frozen=True)
class ProjectConfig:
    path: Path  # project root (directory with Cargo.toml)
    name: str  # Cargo package name
    board_id: str
    upload_method: UploadMethod
    build_profile: BuildProfile = BuildProfile.RELEASE

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("project name is empty")

    @property
    def cargo_toml(self) -> Path:
        return self.path / "Cargo.toml"

    def elf_path(self, target_triple: str) -> Path:
        return self.path / "target" / target_triple / self.build_profile.target_dir_name / self.name


# Names Cargo refuses or that clash with Rust's own crates (user-facing errors in Ukrainian).
_RESERVED_NAMES = frozenset(
    {"alloc", "core", "proc_macro", "std", "test", "self", "super", "crate", "build", "deps"}
)
_RUST_KEYWORDS = frozenset(
    (
        "as",
        "async",
        "await",
        "break",
        "const",
        "continue",
        "dyn",
        "else",
        "enum",
        "extern",
        "false",
        "fn",
        "for",
        "if",
        "impl",
        "in",
        "let",
        "loop",
        "match",
        "mod",
        "move",
        "mut",
        "pub",
        "ref",
        "return",
        "static",
        "struct",
        "trait",
        "true",
        "type",
        "unsafe",
        "use",
        "where",
        "while",
        "abstract",
        "become",
        "box",
        "do",
        "final",
        "macro",
        "override",
        "priv",
        "try",
        "typeof",
        "unsized",
        "virtual",
        "yield",
    )
)
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
MAX_PROJECT_NAME = 64


def project_name_error(name: str) -> str | None:
    """Return why ``name`` cannot be a Cargo package name, or None if it is fine."""
    if not name:
        return _("Введіть назву проєкту.")
    if len(name) > MAX_PROJECT_NAME:
        return _("Назва задовга (максимум {limit} символи).").format(limit=MAX_PROJECT_NAME)
    if not _NAME_RE.match(name):
        return _(
            "Назва має починатися з латинської літери й містити лише латинські літери, "
            "цифри, «_» або «-»."
        )
    normalized = name.replace("-", "_").lower()
    if normalized in _RESERVED_NAMES or normalized in _RUST_KEYWORDS:
        return _("«{name}» — зарезервоване слово Rust/Cargo, виберіть іншу назву.").format(
            name=name
        )
    return None


@dataclass(frozen=True)
class NewProjectRequest:
    name: str
    location: Path  # parent directory; the project is created in location / name
    board_id: str
    example_id: str | None  # None creates the blank STM32 project
    create_gitignore: bool = True

    def __post_init__(self) -> None:
        error = project_name_error(self.name)
        if error is not None:
            raise ValueError(error)

    @property
    def project_dir(self) -> Path:
        return self.location / self.name


@dataclass(frozen=True)
class RecentProject:
    """A recent project as shown on the start screen."""

    path: Path
    name: str
    chip_label: str | None = None  # e.g. "STM32F103C6T6A"; None if Cargo.toml cannot be read
    opened_at: datetime | None = None
    missing: bool = False  # the folder is gone (moved or deleted)
