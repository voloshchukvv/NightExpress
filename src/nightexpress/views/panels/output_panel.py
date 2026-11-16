"""Build/upload log with timestamps, ANSI colours and a final status line (the "Build" tab)."""

from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QMouseEvent,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextImageFormat,
)
from PySide6.QtWidgets import QTextEdit, QWidget

from nightexpress.i18n import _
from nightexpress.models.entities import BuildSummary
from nightexpress.models.state import BuildModel, UploadModel
from nightexpress.views.icons import image
from nightexpress.views.panels.ansi import PLAIN, AnsiStyle, parse_ansi
from nightexpress.views.theme import palette
from nightexpress.views.theme.text_colors import ansi_key, colored, recolor

MAX_BLOCKS = 10_000
STATUS_ICON_PX = 18
# rustc points at the code as "  --> src/main.rs:17:9" (":::" for secondary files).
# The file may be an absolute Windows path: its drive colon is not the line separator.
LOCATION_RE = re.compile(
    r"(?:-->|:::)\s+(?P<file>(?:[A-Za-z]:[\\/])?\S[^:\n]*?):(?P<line>\d+):(?P<column>\d+)"
)


class OutputPanel(QTextEdit):
    # QTextEdit (not QPlainTextEdit): only it renders the inline status icons.

    location_activated = Signal(str, int, int)  # file as printed by the compiler, line, column

    def __init__(self, parent: QWidget | None = None, timestamps: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("OutputPanel")
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.document().setMaximumBlockCount(MAX_BLOCKS)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(font.pointSize() + 1)
        self.setFont(font)
        self.timestamps = timestamps
        self._style: AnsiStyle = PLAIN
        self._formats: dict[AnsiStyle, QTextCharFormat] = {}
        self._make_formats()
        self.setMouseTracking(True)
        self._pressed_anchor = ""
        self._register_images()

    def _make_formats(self) -> None:
        self._formats.clear()
        self._time_format = colored(QTextCharFormat(), "text_muted")
        self._link_format = colored(QTextCharFormat(), "info")
        self._link_format.setFontUnderline(True)

    def refresh_theme(self) -> None:
        """Another theme: what is already printed takes its colours, and so does new text."""
        self._make_formats()
        self._register_images()
        recolor(self.document())
        self.document().markContentsDirty(0, self.document().characterCount())

    def _register_images(self) -> None:
        # QTextDocument.clear() drops resources too, so this runs again after every clear.
        for name, icon_name, color in (
            ("ok", "circle-check", palette.SUCCESS),
            ("fail", "circle-x", palette.ERROR),
        ):
            self.document().addResource(
                QTextDocument.ResourceType.ImageResource,
                QUrl(f"stmr:{name}"),
                image(icon_name, color, STATUS_ICON_PX * 2),
            )

    def bind(self, model: BuildModel | UploadModel) -> None:
        model.log_appended.connect(self.append_line)
        model.log_cleared.connect(self.clear)
        model.summary_changed.connect(self.append_summary)

    def _new_block(self) -> QTextCursor:
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock()
        return cursor

    def _scroll_if_following(self, at_bottom: bool) -> None:
        if at_bottom:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 2

    def append_line(self, line: str) -> None:
        at_bottom = self._at_bottom()
        cursor = self._new_block()
        if self.timestamps:
            cursor.insertText(datetime.now().strftime("[%H:%M:%S] "), self._time_format)
        segments, self._style = parse_ansi(line, self._style)
        start = cursor.position()
        for text, style in segments:
            cursor.insertText(text, self._format(style))
        self._link_locations(start, "".join(text for text, _style in segments))
        self._scroll_if_following(at_bottom)

    def _link_locations(self, start: int, text: str) -> None:
        """Turn "--> file:line:column" into a link that opens the file there."""
        for match in LOCATION_RE.finditer(text):
            cursor = QTextCursor(self.document())
            cursor.setPosition(start + match.start("file"))
            cursor.setPosition(start + match.end("column"), QTextCursor.MoveMode.KeepAnchor)
            link = QTextCharFormat(self._link_format)
            link.setAnchor(True)
            link.setAnchorHref(f"{match['file']}:{match['line']}:{match['column']}")
            link.setToolTip(_("Відкрити в редакторі"))
            cursor.mergeCharFormat(link)

    def activate_anchor(self, href: str) -> None:
        file, line, column = href.rsplit(":", 2)
        self.location_activated.emit(file, int(line), int(column))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._pressed_anchor = (
            self.anchorAt(event.position().toPoint())
            if event.button() is Qt.MouseButton.LeftButton
            else ""
        )
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        anchor = self.anchorAt(event.position().toPoint())
        super().mouseReleaseEvent(event)
        # A click, not the end of a text selection that happened to finish on a link.
        if anchor and anchor == self._pressed_anchor and not self.textCursor().hasSelection():
            self.activate_anchor(anchor)
        self._pressed_anchor = ""

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        over_link = bool(self.anchorAt(event.position().toPoint()))
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if over_link else Qt.CursorShape.IBeamCursor
        )
        super().mouseMoveEvent(event)

    def append_summary(self, summary: BuildSummary | None) -> None:
        if summary is None:
            return
        at_bottom = self._at_bottom()
        cursor = self._new_block()
        picture = QTextImageFormat()
        picture.setName("stmr:ok" if summary.success else "stmr:fail")
        picture.setWidth(STATUS_ICON_PX)
        picture.setHeight(STATUS_ICON_PX)
        picture.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
        cursor.insertImage(picture)
        fmt = colored(QTextCharFormat(), "success" if summary.success else "error")
        fmt.setFontWeight(QFont.Weight.Bold)
        cursor.insertText("  " + summary.message, fmt)
        self._scroll_if_following(at_bottom)

    def clear(self) -> None:
        super().clear()
        self._style = PLAIN
        self._register_images()

    def plain_text(self) -> str:
        return self.toPlainText()

    def sizeHint(self) -> QSize:
        return QSize(600, 200)

    def _format(self, style: AnsiStyle) -> QTextCharFormat:
        fmt = self._formats.get(style)
        if fmt is None:
            # One of the theme's 16 ANSI colours, or its ordinary text colour.
            key = ansi_key(style.foreground) if style.foreground is not None else "text"
            fmt = colored(QTextCharFormat(), key)
            if style.bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            self._formats[style] = fmt
        return fmt
