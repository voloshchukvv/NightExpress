"""Compiler diagnostics grouped by file (the "Problems" tab)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, live
from nightexpress.models.entities import Diagnostic, DiagnosticLevel
from nightexpress.models.qt_models import DiagnosticsTreeModel, UrlRole
from nightexpress.models.state import BuildModel
from nightexpress.views.icons import icon
from nightexpress.views.theme import palette
from nightexpress.views.widgets import icon_label

EMPTY_TITLE = N_("Проблем немає")
EMPTY_TEXT = N_("Помилки й попередження компілятора з'являться тут після збірки.")

# Level -> icon and the theme colour it is drawn in.
_LEVEL_ICONS = {
    DiagnosticLevel.ERROR: ("circle-x", "error"),
    DiagnosticLevel.WARNING: ("triangle-alert", "warning"),
    DiagnosticLevel.NOTE: ("info", "accent_bright"),
    DiagnosticLevel.HELP: ("info", "success"),
}


class ProblemsPanel(QWidget):
    diagnostic_activated = Signal(object)  # Diagnostic with a location
    code_help_requested = Signal(str)  # URL explaining the diagnostic code
    counts_changed = Signal(int, int)  # errors, warnings
    # For the tools of the bottom panel (see LogToolbar): only "copy all" makes sense here.
    log_tools = frozenset({"copy"})

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProblemsPanel")
        self._model = DiagnosticsTreeModel(colors=palette.color)
        self.refresh_theme()
        self.tree = QTreeView(self)
        self._model.retranslated.connect(self.tree.expandAll)
        self.tree.setObjectName("problemsTree")
        self.tree.setModel(self._model)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(18)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setMouseTracking(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(DiagnosticsTreeModel.MESSAGE, QHeaderView.ResizeMode.Stretch)
        for column in (DiagnosticsTreeModel.CODE, DiagnosticsTreeModel.POSITION):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._activated_now = False
        # A double click emits both signals: _on_activated acts once per event.
        self.tree.activated.connect(self._on_activated)
        self.tree.doubleClicked.connect(self._on_activated)
        self.tree.clicked.connect(self._on_clicked)
        self.tree.entered.connect(self._on_entered)

        self.empty = QWidget(self)
        self.empty_title = QLabel(self.empty)
        live(self.empty_title.setText, EMPTY_TITLE)
        self.empty_title.setObjectName("emptyTitle")
        self.empty_text = QLabel(self.empty)
        live(self.empty_text.setText, EMPTY_TEXT)
        self.empty_text.setObjectName("mutedText")
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setSpacing(8)
        empty_layout.addStretch(1)
        for widget in (
            icon_label("circle-check", 36, palette.SUCCESS, self.empty),
            self.empty_title,
            self.empty_text,
        ):
            empty_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.tree)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

    @property
    def tree_model(self) -> DiagnosticsTreeModel:
        return self._model

    @property
    def is_empty(self) -> bool:
        return self.stack.currentWidget() is self.empty

    def index_of(self, diagnostic: Diagnostic, column: int = 0) -> QModelIndex:
        return self._model.index_of(diagnostic).siblingAtColumn(column)

    def bind(self, model: BuildModel) -> None:
        model.diagnostics_changed.connect(self.set_diagnostics)

    def set_project_dir(self, project_dir: Path | None) -> None:
        self._model.set_project_dir(project_dir)
        self.tree.expandAll()

    def set_diagnostics(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        self._model.set_diagnostics(diagnostics)
        self.tree.expandAll()
        self.stack.setCurrentWidget(self.tree if diagnostics else self.empty)
        errors = sum(1 for d in diagnostics if d.level is DiagnosticLevel.ERROR)
        warnings = sum(1 for d in diagnostics if d.level is DiagnosticLevel.WARNING)
        self.counts_changed.emit(errors, warnings)

    def copy_text(self, done: Callable[[str], None]) -> None:
        done(self._model.as_text())

    def go_to(self, step: int) -> bool:
        """Select the next (1) or previous (-1) problem with a location and activate it (F8).

        Wraps around; returns False when there is nothing to go to.
        """
        rows: list[QModelIndex] = []
        for group in range(self._model.rowCount()):
            parent = self._model.index(group, 0)
            for row in range(self._model.rowCount(parent)):
                index = self._model.index(row, 0, parent)
                diagnostic = self._model.diagnostic(index)
                if diagnostic is not None and diagnostic.has_location:
                    rows.append(index)
        if not rows:
            return False
        current = self.tree.currentIndex().siblingAtColumn(0)
        position = rows.index(current) if current in rows else (-1 if step > 0 else 0)
        target = rows[(position + step) % len(rows)]
        self.tree.setCurrentIndex(target)
        self.tree.scrollTo(target)
        self._on_activated(target)
        return True

    def refresh_theme(self) -> None:
        """Icons and colours of the rows for the theme that is active now."""
        self._model.set_icons(
            {
                level: icon(name, palette.color(token))
                for level, (name, token) in _LEVEL_ICONS.items()
            },
            icon("file", palette.TEXT_MUTED),
        )
        if hasattr(self, "tree"):
            self.tree.expandAll()

    def _on_activated(self, index: QModelIndex) -> None:
        if self._activated_now:
            return
        self._activated_now = True
        QTimer.singleShot(0, lambda: setattr(self, "_activated_now", False))
        diagnostic = self._model.diagnostic(index) if index.isValid() else None
        if diagnostic is not None and diagnostic.has_location:
            self.diagnostic_activated.emit(diagnostic)

    def _on_clicked(self, index: QModelIndex) -> None:
        url = index.data(UrlRole)
        if url:
            self.code_help_requested.emit(url)

    def _on_entered(self, index: QModelIndex) -> None:
        link = bool(index.data(UrlRole))
        self.tree.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if link else Qt.CursorShape.ArrowCursor
        )
