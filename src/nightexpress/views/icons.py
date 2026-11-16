"""Bundled icons (Lucide outline set, Rust logo from Simple Icons), recoloured on load."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from nightexpress.views.theme import palette

RENDER_SIZE = 96  # rendered once at high resolution; Qt scales it down crisply


def _svg_text(name: str) -> tuple[str, bool]:
    base = resources.files("nightexpress") / "resources" / "icons"
    for folder, filled in (("lucide", False), ("brand", True), ("", False)):
        file = (base / folder / f"{name}.svg") if folder else (base / f"{name}.svg")
        if file.is_file():
            return file.read_text(encoding="utf-8"), filled
    raise KeyError(f"unknown icon {name!r}")


def svg_bytes(name: str, color: str) -> bytes:
    text, filled = _svg_text(name)
    text = text.replace("currentColor", color)
    if filled and "fill=" not in text.split(">", 1)[0]:
        text = text.replace("<svg ", f'<svg fill="{color}" ', 1)
    return text.encode("utf-8")


# QIcon.cacheKey() -> how the icon was made, so that it can be made again for another theme.
_origins: dict[int, tuple[Callable[..., QIcon], tuple[object, ...]]] = {}


def _token(color: str) -> str | None:
    return color.token if isinstance(color, palette.ThemeColor) else None


def _remember(result: QIcon, factory: Callable[..., QIcon], *args: object) -> QIcon:
    _origins[result.cacheKey()] = (factory, args)
    return result


def retinted(old: QIcon) -> QIcon | None:
    """``old`` drawn in the colours of the theme that is active now; None if it was not made
    here or uses no theme colour (then it needs no change)."""
    origin = _origins.get(old.cacheKey())
    if origin is None or not any(isinstance(a, palette.ThemeColor) for a in origin[1]):
        return None
    factory, args = origin
    return factory(
        *(palette.color(a.token) if isinstance(a, palette.ThemeColor) else a for a in args)
    )


def image(name: str, color: str | None = None, size: int = RENDER_SIZE) -> QImage:
    """The icon drawn in ``color`` (the theme's text colour by default)."""
    return _image(name, str(color or palette.TEXT), size)


@lru_cache(maxsize=256)
def _image(name: str, color: str, size: int) -> QImage:
    renderer = QSvgRenderer(QByteArray(svg_bytes(name, color)))
    result = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return result


# The cached makers below take the token next to the colour: two theme colours that happen to
# be equal today must stay two icons, or one of them could not follow the next theme.


def icon(name: str, color: str | None = None) -> QIcon:
    color = color or palette.TEXT
    return _remember(_icon(name, str(color), _token(color)), icon, name, color)


@lru_cache(maxsize=256)
def _icon(name: str, color: str, _color_token: str | None) -> QIcon:
    return QIcon(QPixmap.fromImage(image(name, color)))


def badged_icon(name: str, color: str, badge_color: str) -> QIcon:
    """The icon with a dot in its top-right corner ("something new in this tab")."""
    made = _badged_icon(name, str(color), str(badge_color), (_token(color), _token(badge_color)))
    return _remember(made, badged_icon, name, color, badge_color)


@lru_cache(maxsize=64)
def _badged_icon(
    name: str, color: str, badge_color: str, _tokens: tuple[str | None, str | None]
) -> QIcon:
    result = image(name, color).copy()
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(badge_color))
    diameter = RENDER_SIZE * 0.42
    painter.drawEllipse(QRectF(RENDER_SIZE - diameter, 0, diameter, diameter))
    painter.end()
    return QIcon(QPixmap.fromImage(result))


LABEL_ICON_GAP = 6  # px of air between a button's icon and its text, on top of the style's own
LABEL_ICON_SIZE = QSize(16 + LABEL_ICON_GAP, 16)  # the icon size for buttons using label_icon()


def label_icon(name: str, color: str | None = None) -> QIcon:
    """An icon for a button with text: Qt leaves no gap setting, so the air is in the picture."""
    color = color or palette.TEXT
    return _remember(_label_icon(name, str(color), _token(color)), label_icon, name, color)


@lru_cache(maxsize=64)
def _label_icon(name: str, color: str, _color_token: str | None) -> QIcon:
    glyph = image(name, color)
    size = LABEL_ICON_SIZE * (RENDER_SIZE // LABEL_ICON_SIZE.height())
    result = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawImage(0, 0, glyph)
    painter.end()
    return QIcon(QPixmap.fromImage(result))


FILE_ICONS_BY_NAME = {"Cargo.lock": "lock", "memory.x": "memory-stick"}
FILE_ICONS_BY_SUFFIX = {
    ".rs": "rust",
    ".toml": "settings",
    ".md": "file-text",
    ".txt": "file-text",
    ".x": "memory-stick",
    ".ld": "memory-stick",
}


def file_icon_name(file_name: str) -> str:
    """Icon for a file by its type: Rust sources, TOML settings, text, linker scripts."""
    if file_name in FILE_ICONS_BY_NAME:
        return FILE_ICONS_BY_NAME[file_name]
    suffix = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return FILE_ICONS_BY_SUFFIX.get(suffix, "file")


def file_icon(file_name: str, color: str | None = None) -> QIcon:
    return icon(file_icon_name(file_name), color or palette.TEXT_MUTED)


def dot_icon(color: str) -> QIcon:
    """A filled dot: the "unsaved changes" mark."""
    return _remember(_dot_icon(str(color), _token(color)), dot_icon, color)


@lru_cache(maxsize=16)
def _dot_icon(color: str, _color_token: str | None) -> QIcon:
    result = QImage(RENDER_SIZE, RENDER_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    diameter = RENDER_SIZE * 0.5
    offset = (RENDER_SIZE - diameter) / 2
    painter.drawEllipse(QRectF(offset, offset, diameter, diameter))
    painter.end()
    return QIcon(QPixmap.fromImage(result))
