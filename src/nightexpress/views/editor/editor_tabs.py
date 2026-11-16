"""Editor area: open file tabs, one shared Monaco editor, and a Markdown preview."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QEnterEvent, QGuiApplication, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, _, live
from nightexpress.views.editor.markdown_preview import MarkdownPreview
from nightexpress.views.editor.monaco_editor import EditorAction, MonacoEditor, language_for
from nightexpress.views.icons import dot_icon, file_icon, icon
from nightexpress.views.theme import palette
from nightexpress.views.widgets import icon_label, tool_button

# What to do when nothing is open: (text, shortcut in Qt notation).
EMPTY_HINTS = (
    (N_("Відкрити файл"), "Ctrl+P"),
    (N_("Зібрати"), "Ctrl+B"),
    (N_("Зібрати й прошити"), "Ctrl+U"),
    (N_("Монітор UART"), "Ctrl+Shift+M"),
)


def shortcut_text(shortcut: str) -> str:
    """The shortcut as the platform writes it: "⌘P" on macOS, "Ctrl+P" elsewhere."""
    return QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)


class TabCloseButton(QToolButton):
    """Close button of a tab; shows a dot instead of the cross while the file is unsaved."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tabClose")
        self.setFixedSize(20, 20)
        self.setIconSize(QSize(14, 14))
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        live(self.setAccessibleName, N_("Закрити файл"))
        self._dirty = False
        self._hovered = False
        self._refresh()

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def shows_dot(self) -> bool:
        return self._dirty and not self._hovered

    def set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._refresh()

    def _refresh(self) -> None:
        if self.shows_dot:
            self.setIcon(dot_icon(palette.TEXT))
            live(self.setToolTip, N_("Є незбережені зміни"))
        else:
            self.setIcon(icon("x", palette.TEXT if self._hovered else palette.TEXT_MUTED))
            live(
                self.setToolTip,
                lambda: _("Закрити ({shortcut})").format(shortcut=shortcut_text("Ctrl+W")),
            )

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        self._refresh()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered = False
        self._refresh()
        super().leaveEvent(event)


class _TabBar(QTabBar):
    middle_clicked = Signal(int)
    menu_requested = Signal(int, QPoint)  # tab index, global position

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.middle_clicked.emit(index)
                return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        index = self.tabAt(event.pos())
        if index >= 0:
            self.menu_requested.emit(index, event.globalPos())


class EmptyEditor(QWidget):
    """Shown instead of the editor while no file is open: what to press next."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyEditor")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hints: list[tuple[str, str]] = []
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        for row, (source_text, shortcut) in enumerate(EMPTY_HINTS):
            text = _(source_text)
            keys = shortcut_text(shortcut)
            self.hints.append((text, keys))
            caption = QLabel(self)
            live(caption.setText, source_text)
            caption.setObjectName("emptyHint")
            key_label = QLabel(keys, self)
            key_label.setObjectName("emptyKey")
            grid.addWidget(caption, row, 0, Qt.AlignmentFlag.AlignRight)
            grid.addWidget(key_label, row, 1, Qt.AlignmentFlag.AlignLeft)
        layout = QVBoxLayout(self)
        layout.addStretch(2)
        layout.addWidget(
            icon_label("cpu", 72, palette.BORDER, self), 0, Qt.AlignmentFlag.AlignHCenter
        )
        layout.addSpacing(24)
        layout.addLayout(grid)
        layout.setAlignment(grid, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(3)


class EditorTabsView(QWidget):
    tab_selected = Signal(object)  # Path
    tab_close_requested = Signal(object)  # Path
    close_others_requested = Signal(object)  # Path of the tab to keep
    close_all_requested = Signal()
    reveal_in_tree_requested = Signal(object)  # Path
    markdown_file_requested = Signal(object)  # Path
    path_copied = Signal(str)
    content_changed = Signal(object, str)  # Path, text
    cursor_moved = Signal(int, int)
    editing_changed = Signal(bool)  # whether the source editor is visible
    command_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None, editor: MonacoEditor | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorTabsView")
        self._preview_paths: set[Path] = set()
        self._tab_row = QWidget(self)
        self._tab_row.setObjectName("editorTabRow")
        self._tab_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.tab_bar = _TabBar(self._tab_row)
        self.tab_bar.setObjectName("editorTabs")
        self.tab_bar.setMovable(True)
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.tab_bar.setIconSize(QSize(18, 18))
        self.tab_bar.currentChanged.connect(self._on_current_changed)
        self.tab_bar.middle_clicked.connect(self._on_close_requested)
        self.tab_bar.menu_requested.connect(self._show_tab_menu)

        self.markdown_toggle = tool_button("eye", N_("Форматований Markdown"), self._tab_row)
        self.markdown_toggle.setCheckable(True)
        self.markdown_toggle.toggled.connect(self._toggle_markdown)
        tab_layout = QHBoxLayout(self._tab_row)
        tab_layout.setContentsMargins(0, 0, 4, 0)
        tab_layout.addWidget(self.tab_bar, 1)
        tab_layout.addWidget(self.markdown_toggle, 0, Qt.AlignmentFlag.AlignVCenter)

        self.editor = editor or MonacoEditor(self)
        self.editor.content_changed.connect(self._on_content_changed)
        self.editor.cursor_moved.connect(self.cursor_moved)
        self.editor.command_requested.connect(self.command_requested)
        self.markdown_preview = MarkdownPreview(self)
        self.markdown_preview.file_requested.connect(self._open_markdown_link)
        self.empty = EmptyEditor(self)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.markdown_preview)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tab_row)
        layout.addWidget(self.stack, 1)
        self._switching = False
        self._refresh_view()

    # --- tabs ------------------------------------------------------------------------------

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(self.tab_bar.tabData(i) for i in range(self.tab_bar.count()))

    @property
    def current_path(self) -> Path | None:
        index = self.tab_bar.currentIndex()
        return self.tab_bar.tabData(index) if index >= 0 else None

    @property
    def is_empty(self) -> bool:
        return self.stack.currentWidget() is self.empty

    @property
    def is_editing(self) -> bool:
        return self.stack.currentWidget() is self.editor

    def open_file(self, path: Path, text: str) -> None:
        index = self._index_of(path)
        if index < 0:
            self.editor.open_document(path, text)
            self._switching = True
            try:
                index = self.tab_bar.addTab(file_icon(path.name), path.name)
                self.tab_bar.setTabData(index, path)
                self.tab_bar.setTabToolTip(index, str(path))
                button = TabCloseButton(self.tab_bar)
                button.clicked.connect(lambda _=False, p=path: self.tab_close_requested.emit(p))
                self.tab_bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)
            finally:
                self._switching = False
        if self.tab_bar.currentIndex() == index:
            self.editor.show_document(path)
            self._refresh_view()
        else:
            self.tab_bar.setCurrentIndex(index)

    def close_file(self, path: Path) -> None:
        index = self._index_of(path)
        if index < 0:
            return
        self._preview_paths.discard(path)
        self.editor.close_document(path)
        self._switching = True
        try:
            self.tab_bar.removeTab(index)
        finally:
            self._switching = False
        current = self.current_path
        if current is not None:
            self.editor.show_document(current)
        self._refresh_view()

    def set_current(self, path: Path) -> None:
        index = self._index_of(path)
        if index >= 0:
            self.tab_bar.setCurrentIndex(index)

    def select_next(self, step: int = 1) -> None:
        """Switch to the next (or, with -1, the previous) tab, wrapping around."""
        count = self.tab_bar.count()
        if count > 1:
            self.tab_bar.setCurrentIndex((self.tab_bar.currentIndex() + step) % count)

    def set_dirty(self, path: Path, dirty: bool) -> None:
        button = self.close_button(path)
        if button is not None:
            button.set_dirty(dirty)

    def is_marked_dirty(self, path: Path) -> bool:
        button = self.close_button(path)
        return button is not None and button.dirty

    def close_button(self, path: Path) -> TabCloseButton | None:
        index = self._index_of(path)
        if index < 0:
            return None
        button = self.tab_bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
        return button if isinstance(button, TabCloseButton) else None

    def tab_menu(self, path: Path) -> QMenu:
        menu = QMenu(self)
        close = menu.addAction(_("Закрити"), lambda: self.tab_close_requested.emit(path))
        close.setShortcut(QKeySequence("Ctrl+W"))
        close.setShortcutVisibleInContextMenu(True)
        close.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)  # shown only, not grabbed
        others = menu.addAction(_("Закрити інші"), lambda: self.close_others_requested.emit(path))
        others.setEnabled(self.tab_bar.count() > 1)
        menu.addAction(_("Закрити всі"), self.close_all_requested.emit)
        menu.addSeparator()
        menu.addAction(_("Показати в дереві"), lambda: self.reveal_in_tree_requested.emit(path))
        menu.addAction(_("Скопіювати шлях"), lambda: self._copy_path(path))
        return menu

    # --- editor pass-through ---------------------------------------------------------------

    def text(self, path: Path) -> str | None:
        return self.editor.text(path)

    def replace_text(self, path: Path, text: str) -> None:
        self.editor.replace_text(path, text)
        self._update_markdown_preview(path, text)

    def reveal(self, path: Path, line: int, column: int = 1) -> None:
        self.set_current(path)
        self.markdown_toggle.setChecked(False)
        self.editor.reveal(path, line, column)

    def trigger_action(self, action: EditorAction) -> None:
        if self.stack.currentWidget() is self.markdown_preview:
            if action is EditorAction.COPY:
                self.markdown_preview.copy()
                return
            if action is EditorAction.SELECT_ALL:
                self.markdown_preview.selectAll()
                return
            self.markdown_toggle.setChecked(False)
        self.editor.trigger_action(action)

    # --- internals -------------------------------------------------------------------------

    def _refresh_view(self) -> None:
        path = self.current_path
        is_markdown = path is not None and language_for(path) == "markdown"
        show_preview = is_markdown and path in self._preview_paths
        self._tab_row.setVisible(path is not None)
        self.markdown_toggle.setVisible(is_markdown)
        with QSignalBlocker(self.markdown_toggle):
            self.markdown_toggle.setChecked(show_preview)
        was_editing = self.is_editing
        if path is None:
            self.stack.setCurrentWidget(self.empty)
        elif show_preview:
            self.markdown_preview.show_markdown(path, self.editor.text(path) or "")
            self.stack.setCurrentWidget(self.markdown_preview)
        else:
            self.stack.setCurrentWidget(self.editor)
        if was_editing != self.is_editing:
            self.editing_changed.emit(self.is_editing)

    def _toggle_markdown(self, preview: bool) -> None:
        path = self.current_path
        if path is None:
            return
        if preview:
            self._preview_paths.add(path)
        else:
            self._preview_paths.discard(path)
        self._refresh_view()
        if preview:
            self.markdown_preview.setFocus()
        else:
            self.editor.focus_editor()

    def _on_content_changed(self, path: Path, text: str) -> None:
        self._update_markdown_preview(path, text)
        self.content_changed.emit(path, text)

    def _update_markdown_preview(self, path: Path, text: str) -> None:
        if path == self.current_path and self.stack.currentWidget() is self.markdown_preview:
            self.markdown_preview.show_markdown(path, text)

    def _open_markdown_link(self, path: Path, anchor: str) -> None:
        self.markdown_file_requested.emit(path)
        if self.current_path == path and language_for(path) == "markdown":
            self.markdown_toggle.setChecked(True)
            self.markdown_preview.scroll_to_anchor(anchor)

    def _index_of(self, path: Path) -> int:
        return next((i for i in range(self.tab_bar.count()) if self.tab_bar.tabData(i) == path), -1)

    def _on_current_changed(self, index: int) -> None:
        if self._switching or index < 0:
            return
        path: Path = self.tab_bar.tabData(index)
        self.editor.show_document(path)
        self._refresh_view()
        self.tab_selected.emit(path)

    def _on_close_requested(self, index: int) -> None:
        self.tab_close_requested.emit(self.tab_bar.tabData(index))

    def _show_tab_menu(self, index: int, position: QPoint) -> None:
        menu = self.tab_menu(self.tab_bar.tabData(index))
        menu.exec(position)
        menu.deleteLater()

    def _copy_path(self, path: Path) -> None:
        QGuiApplication.clipboard().setText(str(path))
        self.path_copied.emit(str(path))
