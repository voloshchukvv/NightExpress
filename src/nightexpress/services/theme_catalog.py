"""Colour themes bundled with the application: one TOML file per theme in ``resources/themes``.

Adding a theme means adding a file; a file that is incomplete or malformed fails loudly at
start-up (and in the tests) instead of producing a half-coloured window.
"""

from __future__ import annotations

import tomllib
from importlib import resources
from importlib.resources.abc import Traversable

from nightexpress.models.entities.theme import Theme, ThemeError

DEFAULT_THEME_ID = "graphite"


class ThemeCatalog:
    def __init__(self, themes: list[Theme]) -> None:
        self._themes = {theme.id: theme for theme in themes}
        if len(self._themes) != len(themes):
            raise ThemeError("two themes share one id")
        if DEFAULT_THEME_ID not in self._themes:
            raise ThemeError(f"the default theme {DEFAULT_THEME_ID!r} is missing")

    @classmethod
    def load(cls, directory: Traversable | None = None) -> ThemeCatalog:
        root = directory or resources.files("nightexpress") / "resources" / "themes"
        files = sorted(
            (f for f in root.iterdir() if f.name.endswith(".toml")), key=lambda f: f.name
        )
        themes = []
        for file in files:
            try:
                data = tomllib.loads(file.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise ThemeError(f"{file.name}: {exc}") from exc
            themes.append(Theme.from_mapping(data, file.name))
        return cls(themes)

    @property
    def themes(self) -> tuple[Theme, ...]:
        """All themes: the default one first, the rest by name."""
        rest = sorted((t for t in self._themes.values() if t.id != DEFAULT_THEME_ID), key=_name)
        return (self.default, *rest)

    @property
    def default(self) -> Theme:
        return self._themes[DEFAULT_THEME_ID]

    def get(self, theme_id: str | None) -> Theme:
        """The theme with this id; the default one for an unknown or missing id."""
        return self._themes.get(theme_id or "", self.default)


def _name(theme: Theme) -> str:
    return theme.name.casefold()
