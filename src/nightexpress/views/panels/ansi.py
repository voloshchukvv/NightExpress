"""Minimal ANSI SGR parser for cargo/rustc coloured output."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_OTHER_ESCAPES_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]")  # cursor movement etc.: dropped


@dataclass(frozen=True)
class AnsiStyle:
    foreground: int | None = None  # 0..15 (8..15 = bright)
    bold: bool = False


PLAIN = AnsiStyle()


def _apply(style: AnsiStyle, params: str) -> AnsiStyle:
    codes = [int(p) for p in params.split(";") if p.isdigit()] or [0]
    position = 0
    while position < len(codes):
        code = codes[position]
        position += 1
        if code in (38, 48):  # extended colour: "5;N" (palette) or "2;R;G;B"
            mode = codes[position] if position < len(codes) else 0
            if mode == 5:
                index = codes[position + 1] if position + 1 < len(codes) else None
                if code == 38 and index is not None and index < 16:
                    style = replace(style, foreground=index)
                position += 2
            elif mode == 2:
                position += 4
        elif code == 0:
            style = PLAIN
        elif code == 1:
            style = replace(style, bold=True)
        elif code == 22:
            style = replace(style, bold=False)
        elif 30 <= code <= 37:
            style = replace(style, foreground=code - 30)
        elif 90 <= code <= 97:
            style = replace(style, foreground=code - 90 + 8)
        elif code == 39:
            style = replace(style, foreground=None)
    return style


def parse_ansi(
    text: str, style: AnsiStyle = PLAIN
) -> tuple[list[tuple[str, AnsiStyle]], AnsiStyle]:
    """Split ``text`` into styled segments; returns the style active at the end of the text."""
    text = _OTHER_ESCAPES_RE.sub("", text)
    segments: list[tuple[str, AnsiStyle]] = []
    position = 0
    for match in _SGR_RE.finditer(text):
        if match.start() > position:
            segments.append((text[position : match.start()], style))
        style = _apply(style, match.group(1))
        position = match.end()
    if position < len(text):
        segments.append((text[position:], style))
    return segments, style
