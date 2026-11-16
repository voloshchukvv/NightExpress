"""Tree model for the "Problems" panel: diagnostics grouped by file."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QStandardItem, QStandardItemModel

from nightexpress.i18n import N_, _, on_change
from nightexpress.models.entities import Diagnostic, DiagnosticLevel

DiagnosticRole = Qt.ItemDataRole.UserRole + 1
UrlRole = Qt.ItemDataRole.UserRole + 2  # explanation page of the diagnostic code, if any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_HELP_RE = re.compile(r"^\s*(?:=\s*)?help:\s*(.+)$", re.MULTILINE)
_ERROR_CODE_RE = re.compile(r"E\d{4}")

# User-facing text (Ukrainian UI).
LEVEL_NAMES = {
    DiagnosticLevel.ERROR: N_("помилка"),
    DiagnosticLevel.WARNING: N_("попередження"),
    DiagnosticLevel.NOTE: N_("примітка"),
    DiagnosticLevel.HELP: N_("порада"),
}
# Level -> the theme colour of its message (see ``colors`` of DiagnosticsTreeModel).
LEVEL_COLOR_TOKENS = {
    DiagnosticLevel.ERROR: "error",
    DiagnosticLevel.WARNING: "warning",
    DiagnosticLevel.NOTE: "info",
    DiagnosticLevel.HELP: "ok",
}
LINK_COLOR_TOKEN = "info"
HEADERS = (N_("Повідомлення"), N_("Код"), N_("Рядок"))
NO_FILE_GROUP = N_("Проєкт загалом")

Index = QModelIndex | QPersistentModelIndex


def help_text(diagnostic: Diagnostic) -> str | None:
    """The compiler's first "help:" suggestion for the diagnostic, if it gave one."""
    match = _HELP_RE.search(_ANSI_RE.sub("", diagnostic.rendered))
    return match.group(1).strip() if match else None


def code_url(code: str | None) -> str | None:
    """Where a diagnostic code is explained: rustc error codes and clippy lints have pages."""
    if not code:
        return None
    if _ERROR_CODE_RE.fullmatch(code):
        return f"https://doc.rust-lang.org/error_codes/{code}.html"
    if code.startswith("clippy::"):
        lint = code.removeprefix("clippy::")
        return f"https://rust-lang.github.io/rust-clippy/master/index.html#{lint}"
    return None


class DiagnosticsTreeModel(QStandardItemModel):
    MESSAGE, CODE, POSITION = range(3)
    retranslated = Signal()  # the rows were rebuilt in another language

    def __init__(
        self, project_dir: Path | None = None, colors: Callable[[str], str] | None = None
    ) -> None:
        """``colors`` turns a theme colour token into a colour; it comes from the view layer,
        like the icons. Without it the rows keep the default text colour."""
        super().__init__()
        self._diagnostics: tuple[Diagnostic, ...] = ()
        self._project_dir = project_dir
        self._colors = colors
        self._icons: Mapping[DiagnosticLevel, QIcon] = {}
        self._group_icon: QIcon | None = None
        self.setHorizontalHeaderLabels([_(text) for text in HEADERS])
        # After a change of the language: no texts on the first call, they were just made.
        self._retranslating = False
        on_change(self, self._retranslate, "texts")
        self._retranslating = True

    def _retranslate(self) -> None:
        if not self._retranslating:
            return
        self.setHorizontalHeaderLabels([_(text) for text in HEADERS])
        self._rebuild()
        self.retranslated.emit()

    def set_icons(self, levels: Mapping[DiagnosticLevel, QIcon], group: QIcon) -> None:
        """Icons come from the view layer; the model only places them."""
        self._icons = levels
        self._group_icon = group
        self._rebuild()

    def set_project_dir(self, project_dir: Path | None) -> None:
        self._project_dir = project_dir
        self._rebuild()

    def set_diagnostics(self, diagnostics: Iterable[Diagnostic]) -> None:
        self._diagnostics = tuple(diagnostics)
        self._rebuild()

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self._diagnostics

    def as_text(self) -> str:
        """Every problem on its own line, as the compiler writes it, for the clipboard:
        ``src/main.rs:12:5: error[E0425]: cannot find value `x` in this scope``."""
        lines = []
        for d in self._diagnostics:
            where = ""
            if d.file is not None:
                where = self.group_title(d.file)
                if d.line is not None:
                    where += f":{d.line}" + (f":{d.column}" if d.column else "")
                where += ": "
            code = f"[{d.code}]" if d.code else ""
            lines.append(f"{where}{d.level.value}{code}: {d.message}")
        return "\n".join(lines)

    def diagnostic(self, index: Index) -> Diagnostic | None:
        """The diagnostic of a row (any column); None for a file row."""
        return index.siblingAtColumn(self.MESSAGE).data(DiagnosticRole)

    def index_of(self, diagnostic: Diagnostic) -> QModelIndex:
        for group_row in range(self.rowCount()):
            group = self.index(group_row, 0)
            for row in range(self.rowCount(group)):
                index = self.index(row, 0, group)
                if index.data(DiagnosticRole) == diagnostic:
                    return index
        return QModelIndex()

    def group_title(self, file: Path | None) -> str:
        if file is None:
            return _(NO_FILE_GROUP)
        if self._project_dir is not None and file.is_relative_to(self._project_dir):
            return file.relative_to(self._project_dir).as_posix()
        return str(file)

    def _rebuild(self) -> None:
        self.removeRows(0, self.rowCount())
        order = list(DiagnosticLevel)
        groups: dict[Path | None, list[Diagnostic]] = {}
        for d in self._diagnostics:
            groups.setdefault(d.file, []).append(d)

        def worst(items: list[Diagnostic]) -> int:
            return min(order.index(d.level) for d in items)

        # Files with errors first; inside a file errors first, then by line.
        for file, items in sorted(
            groups.items(), key=lambda g: (worst(g[1]), self.group_title(g[0]))
        ):
            group = QStandardItem(f"{self.group_title(file)}  ({len(items)})")
            if self._group_icon is not None:
                group.setIcon(self._group_icon)
            font = group.font()
            font.setBold(True)
            group.setFont(font)
            row = [group, QStandardItem(), QStandardItem()]
            for item in row:
                item.setEditable(False)
            for d in sorted(items, key=lambda d: (order.index(d.level), d.line or 0)):
                group.appendRow(self._row(d))
            self.appendRow(row)

    def _paint(self, item: QStandardItem, token: str) -> None:
        if self._colors is not None:
            item.setForeground(QColor(self._colors(token)))

    def _row(self, d: Diagnostic) -> list[QStandardItem]:
        hint = help_text(d)
        message = QStandardItem(
            _("{message}\nпорада: {hint}").format(message=d.message, hint=hint)
            if hint
            else d.message
        )
        message.setData(d, DiagnosticRole)
        message.setToolTip(_ANSI_RE.sub("", d.rendered) or d.message)
        self._paint(message, LEVEL_COLOR_TOKENS[d.level])
        if d.level in self._icons:
            message.setIcon(self._icons[d.level])
        message.setAccessibleText(f"{_(LEVEL_NAMES[d.level])}: {d.message}")

        code = QStandardItem(d.code or "")
        url = code_url(d.code)
        if url:
            code.setData(url, UrlRole)
            self._paint(code, LINK_COLOR_TOKEN)
            code.setToolTip(_("Відкрити пояснення в браузері"))

        if d.line is None:
            position = QStandardItem("")
        else:
            position = QStandardItem(f"{d.line}:{d.column}" if d.column else str(d.line))
        row = [message, code, position]
        for item in row:
            item.setEditable(False)
        return row
