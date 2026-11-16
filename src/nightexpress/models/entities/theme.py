"""A colour theme: every colour of the interface, the logs and the code editor."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nightexpress.i18n import localized
from nightexpress.models.entities.language import localized_texts

# Interface colours every theme must define (the names used as ``palette.NAME`` in the views).
COLOR_TOKENS = (
    "background",  # window background between panels
    "surface",  # panels
    "surface_alt",  # inputs, hovered items
    "selection",  # selected rows in trees and tables
    "header",  # title/menu row, panel headers
    "editor",  # code editor and logs
    "border",
    "outline",  # menus, drop-down lists, cards
    "control_border",
    "control_hover",
    "control_pressed",
    "control_disabled_text",
    "checkbox_border",
    "scrollbar",
    "scrollbar_hover",
    "focus",
    "arrow",  # drop-down and spin-box arrows
    "text",
    "text_muted",
    "disabled_text",
    "accent",  # primary buttons, selection
    "accent_hover",
    "accent_pressed",
    "accent_bright",  # logo, links
    "accent_soft",  # tinted backgrounds (new project card, selected nav item)
    "accent_soft_hover",
    "accent_soft_pressed",
    "accent_border",  # outline of tinted backgrounds
    "accent_muted",  # quiet accent outline: an open combo box and its list
    "accent_text",  # text and icons on the accent colour
    "accent_icon_background",
    "accent_gradient_end",
    "success",
    "ok",
    "warning",
    "error",
    "error_soft",  # "disconnect"-style buttons
    "error_soft_hover",
    "error_soft_pressed",
    "error_border",
    "error_text",
    "info",  # notes and links in logs and problems
    "toast_border",
    "toast_text",
    "search_match",
    "search_match_current",
)
ANSI_COUNT = 16
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?")
_OPAQUE_RE = re.compile(r"#[0-9a-fA-F]{6}")


class ThemeError(ValueError):
    """A theme definition is incomplete or malformed (the message names the key)."""


@dataclass(frozen=True)
class Theme:
    id: str
    names: Mapping[str, str]  # {language: name}, see localized_texts
    dark: bool  # which built-in editor theme the code colours are layered on
    colors: Mapping[str, str]  # COLOR_TOKENS -> "#rrggbb"
    ansi: tuple[str, ...]  # the 16 ANSI colours of the logs and the terminal
    editor_colors: Mapping[str, str]  # Monaco colour ids -> "#rrggbb" or "#rrggbbaa"
    editor_tokens: Mapping[str, str]  # Monaco token names -> "#rrggbb"

    @property
    def name(self) -> str:
        """The name in the current interface language."""
        return localized(self.names)

    def color(self, token: str) -> str:
        return self.colors[token]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], source: str = "theme") -> Theme:
        """Validate a parsed theme file; ``source`` is named in the errors."""

        def table(key: str) -> Mapping[str, Any]:
            value = data.get(key)
            if not isinstance(value, Mapping):
                raise ThemeError(f"{source}: [{key}] is missing")
            return value

        def check(where: str, value: object, pattern: re.Pattern[str] = _OPAQUE_RE) -> str:
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise ThemeError(f"{source}: {where} is not a colour: {value!r}")
            return value.lower()

        for key, kind in (("id", str), ("dark", bool)):
            if not isinstance(data.get(key), kind) or data[key] == "":
                raise ThemeError(f"{source}: {key} is missing")
        if "name" not in data:
            raise ThemeError(f"{source}: name is missing")
        try:
            names = localized_texts(data["name"])
        except ValueError as exc:
            raise ThemeError(f"{source}: name: {exc}") from exc
        colors = table("colors")
        missing = [t for t in COLOR_TOKENS if t not in colors]
        if missing:
            raise ThemeError(f"{source}: [colors] lacks {', '.join(missing)}")
        unknown = sorted(set(colors) - set(COLOR_TOKENS))
        if unknown:
            raise ThemeError(f"{source}: [colors] has unknown {', '.join(unknown)}")
        ansi = data.get("ansi")
        if not isinstance(ansi, list) or len(ansi) != ANSI_COUNT:
            raise ThemeError(f"{source}: ansi must list {ANSI_COUNT} colours")
        editor = table("editor")
        return cls(
            id=data["id"],
            names=names,
            dark=data["dark"],
            colors={t: check(f"colors.{t}", colors[t]) for t in COLOR_TOKENS},
            ansi=tuple(check(f"ansi[{i}]", value) for i, value in enumerate(ansi)),
            editor_colors={
                key: check(f"editor.colors.{key}", value, _COLOR_RE)
                for key, value in _sub(editor, "colors", source).items()
            },
            editor_tokens={
                key: check(f"editor.tokens.{key}", value)
                for key, value in _sub(editor, "tokens", source).items()
            },
        )


def _sub(table: Mapping[str, Any], key: str, source: str) -> Mapping[str, Any]:
    value = table.get(key, {})
    if not isinstance(value, Mapping):
        raise ThemeError(f"{source}: [editor.{key}] must be a table")
    return value
