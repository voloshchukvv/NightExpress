"""Theme colours inside text documents (logs): formats remember which colour they stand for,
so text that is already on screen can be repainted for another theme."""

from __future__ import annotations

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat

from nightexpress.views.theme import palette

# Custom property of a QTextCharFormat: a colour token, or "ansi:N" for the N-th ANSI colour.
COLOR_KEY = int(QTextFormat.Property.UserProperty) + 1
ANSI_PREFIX = "ansi:"


def ansi_key(index: int) -> str:
    return f"{ANSI_PREFIX}{index}"


def resolve(key: str) -> str:
    """The colour ``key`` stands for in the theme that is active now."""
    if key.startswith(ANSI_PREFIX):
        return palette.current().ansi[int(key.removeprefix(ANSI_PREFIX))]
    return palette.color(key)


def colored(fmt: QTextCharFormat, key: str) -> QTextCharFormat:
    """Give ``fmt`` the foreground colour of ``key`` and note the key in the format."""
    fmt.setForeground(QColor(resolve(key)))
    fmt.setProperty(COLOR_KEY, key)
    return fmt


def recolor(document: QTextDocument) -> None:
    """Repaint all text of ``document`` that was written with ``colored`` formats."""
    spans: list[tuple[int, int, str]] = []
    block = document.firstBlock()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            key = fragment.charFormat().property(COLOR_KEY)
            if isinstance(key, str) and key:
                spans.append((fragment.position(), fragment.length(), key))
            it += 1
        block = block.next()
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    for position, length, key in spans:
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        change = QTextCharFormat()
        change.setForeground(QColor(resolve(key)))
        cursor.mergeCharFormat(change)
    cursor.endEditBlock()
