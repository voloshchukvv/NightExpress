"""Colour themes: the bundled files, their validation and the "no stray colours" guard."""

from __future__ import annotations

import re
import tomllib
from importlib import resources
from pathlib import Path

import pytest

from nightexpress.models.entities.theme import ANSI_COUNT, COLOR_TOKENS, Theme, ThemeError
from nightexpress.services.theme_catalog import DEFAULT_THEME_ID, ThemeCatalog

PACKAGE = Path(str(resources.files("nightexpress")))
THEMES = PACKAGE / "resources" / "themes"
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\b|\brgba?\(")
# Third-party code and the theme files themselves.
VENDORED = ("resources/editor/monaco/", "resources/terminal/xterm/", "resources/themes/")
CHECKED_SUFFIXES = {".py", ".js", ".html", ".css", ".svg", ".qss"}


def graphite() -> dict:
    return tomllib.loads((THEMES / "graphite.toml").read_text(encoding="utf-8"))


def test_bundled_themes_load_and_graphite_is_the_default() -> None:
    catalog = ThemeCatalog.load()
    assert catalog.default.id == DEFAULT_THEME_ID == "graphite"
    assert catalog.themes[0] is catalog.default
    assert len({theme.id for theme in catalog.themes}) == len(catalog.themes)
    for theme in catalog.themes:
        assert set(theme.colors) == set(COLOR_TOKENS)
        assert len(theme.ansi) == ANSI_COUNT
        assert theme.name


def test_unknown_theme_id_falls_back_to_the_default() -> None:
    catalog = ThemeCatalog.load()
    assert catalog.get("neon") is catalog.default
    assert catalog.get(None) is catalog.default
    assert catalog.get("dark") is catalog.default  # the value older settings may hold
    assert catalog.get("graphite") is catalog.default


def test_graphite_keeps_its_original_colours() -> None:
    """The look approved in dev/*.png: these values must not drift."""
    theme = ThemeCatalog.load().default
    assert theme.dark and theme.name == "Графіт"
    expected = {
        "background": "#131619", "surface": "#1a1d20", "surface_alt": "#22262a",
        "selection": "#1f4a43", "header": "#1c1f22", "editor": "#15181b", "border": "#2e3337",
        "control_border": "#3a4249", "control_hover": "#2c343a", "control_pressed": "#1a2227",
        "focus": "#75ebd7", "text": "#e6e8ea", "text_muted": "#9aa1a8", "accent": "#2bb39a",
        "accent_bright": "#3fd9bd", "accent_soft": "#173d37", "accent_muted": "#2c6b60",
        "accent_text": "#0d1f1b", "success": "#3ddc84", "warning": "#e5c07b",
        "disabled_text": "#5f666d", "error": "#f07178", "ok": "#7fd17f",
    }  # fmt: skip
    assert {token: theme.color(token) for token in expected} == expected
    assert theme.ansi[1] == theme.color("error") and theme.ansi[15] == "#ffffff"
    assert theme.editor_colors["editor.background"] == theme.color("editor")
    assert theme.editor_colors["editor.selectionBackground"] == "#2bb39a40"  # with alpha
    assert theme.editor_tokens == {"key": "#7fd1c1", "type": "#e5c07b"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["colors"].pop("accent"), r"t\.toml: \[colors\] lacks accent"),
        (lambda d: d["colors"].update(acent="#000000"), r"\[colors\] has unknown acent"),
        (lambda d: d["colors"].update(text="teal"), r"colors\.text is not a colour: 'teal'"),
        (lambda d: d["colors"].update(text="#e6e8ea80"), r"colors\.text is not a colour"),
        (lambda d: d["ansi"].pop(), "ansi must list 16 colours"),
        (lambda d: d["ansi"].__setitem__(3, "#12"), r"ansi\[3\] is not a colour"),
        (lambda d: d["editor"]["colors"].update({"editor.background": "x"}), "editor.colors"),
        (lambda d: d["editor"]["tokens"].update(key="7fd1c1"), r"editor\.tokens\.key"),
        (lambda d: d.pop("name"), "name is missing"),
        (lambda d: d.update(dark="yes"), "dark is missing"),
        (lambda d: d.pop("colors"), r"\[colors\] is missing"),
    ],
)
def test_broken_theme_files_are_named_in_the_error(change, message: str) -> None:
    data = graphite()
    change(data)
    with pytest.raises(ThemeError, match=message):
        Theme.from_mapping(data, "t.toml")


def test_catalog_reads_every_file_and_rejects_duplicates(tmp_path: Path) -> None:
    source = (THEMES / "graphite.toml").read_text(encoding="utf-8")
    (tmp_path / "graphite.toml").write_text(source, encoding="utf-8")
    other = source.replace('id = "graphite"', 'id = "amber"').replace("Графіт", "Бурштин")
    (tmp_path / "amber.toml").write_text(other, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a theme", encoding="utf-8")
    catalog = ThemeCatalog.load(tmp_path)
    assert [theme.id for theme in catalog.themes] == ["graphite", "amber"]
    assert catalog.get("amber").name == "Бурштин"

    (tmp_path / "copy.toml").write_text(other, encoding="utf-8")
    with pytest.raises(ThemeError, match="share one id"):
        ThemeCatalog.load(tmp_path)
    (tmp_path / "copy.toml").write_text("id = ", encoding="utf-8")
    with pytest.raises(ThemeError, match=r"copy\.toml"):
        ThemeCatalog.load(tmp_path)


def test_catalog_needs_the_default_theme(tmp_path: Path) -> None:
    source = (THEMES / "graphite.toml").read_text(encoding="utf-8")
    (tmp_path / "amber.toml").write_text(source.replace('"graphite"', '"amber"'), "utf-8")
    with pytest.raises(ThemeError, match="default theme"):
        ThemeCatalog.load(tmp_path)


def test_no_colours_outside_the_theme_files() -> None:
    """Every colour lives in resources/themes: a literal one elsewhere would ignore the theme."""
    found = []
    for file in sorted(PACKAGE.rglob("*")):
        relative = file.relative_to(PACKAGE).as_posix()
        if (
            not file.is_file()
            or file.suffix not in CHECKED_SUFFIXES
            or relative.startswith(VENDORED)
            or "__pycache__" in relative
        ):
            continue
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
            if COLOR_RE.search(line):
                found.append(f"{relative}:{number}: {line.strip()[:80]}")
    assert not found, "colours outside resources/themes:\n" + "\n".join(found)


# --- the bundled themes ---------------------------------------------------------------------


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    """WCAG contrast ratio of two colours (1 = the same, 21 = black on white)."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# (text, what it is written on): every pair that carries something the user has to read.
TEXT_PAIRS = [
    ("text", "background"), ("text", "surface"), ("text", "surface_alt"), ("text", "editor"),
    ("text", "header"), ("text", "selection"), ("text", "accent_soft"),
    ("text_muted", "background"), ("text_muted", "surface"), ("text_muted", "editor"),
    ("accent_text", "accent"), ("accent_bright", "surface"), ("accent_bright", "background"),
    ("error", "surface"), ("error", "editor"), ("warning", "surface"), ("success", "surface"),
    ("success", "editor"), ("ok", "surface"), ("info", "surface"), ("info", "editor"),
    ("error_text", "error_soft"), ("toast_text", "surface_alt"),
]  # fmt: skip
ALL_THEMES = ThemeCatalog.load().themes


def test_the_set_of_bundled_themes() -> None:
    assert [(theme.id, theme.name, theme.dark) for theme in ALL_THEMES] == [
        ("graphite", "Графіт", True),
        ("high_contrast", "Висока контрастність", True),
        ("ocean", "Океан", True),
        ("light", "Світла", False),
    ]


@pytest.mark.parametrize("theme", ALL_THEMES, ids=lambda theme: theme.id)
def test_text_is_readable_in_every_theme(theme: Theme) -> None:
    needed = 7.0 if theme.id == "high_contrast" else 4.5  # WCAG AAA / AA for body text
    low = [
        f"{text} on {surface}: {contrast(theme.color(text), theme.color(surface)):.2f}"
        for text, surface in TEXT_PAIRS
        if contrast(theme.color(text), theme.color(surface)) < needed
    ]
    assert not low, f"{theme.id}: contrast below {needed}: {low}"
    # A found word stays readable under the search highlight.
    for match in ("search_match", "search_match_current"):
        assert contrast(theme.color("text"), theme.color(match)) >= 3.0, match
    # Log and terminal output: every ANSI colour can be told from the background.
    weak = [i for i, c in enumerate(theme.ansi) if contrast(c, theme.color("editor")) < 3.0]
    assert not weak, f"{theme.id}: ANSI colours {weak} vanish on the log background"
    # The editor is one surface with the logs next to it.
    assert theme.editor_colors["editor.background"] == theme.color("editor")
    assert theme.editor_colors["editorGutter.background"] == theme.color("editor")


@pytest.mark.parametrize("theme", ALL_THEMES, ids=lambda theme: theme.id)
def test_states_can_be_told_apart(theme: Theme) -> None:
    """Hover, pressed and selected looks differ from the resting one in every theme."""
    c = theme.color
    assert len({c("surface_alt"), c("control_hover"), c("control_pressed")}) == 3
    assert len({c("accent"), c("accent_hover"), c("accent_pressed")}) == 3
    assert len({c("accent_soft"), c("accent_soft_hover"), c("accent_soft_pressed")}) == 3
    assert len({c("error_soft"), c("error_soft_hover"), c("error_soft_pressed")}) == 3
    assert c("selection") not in {c("surface"), c("surface_alt")}
    assert c("search_match") != c("search_match_current")
    assert c("scrollbar") != c("scrollbar_hover")
    assert len({c("error"), c("warning"), c("success"), c("info")}) == 4
