"""Project file tree: the project folder itself is the visible root.

The tree never changes the disk itself: creating, renaming and deleting are intent signals
handled by the controller (which also keeps the open editor tabs in step).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QFileInfo,
    QModelIndex,
    QPersistentModelIndex,
    QRectF,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAbstractFileIconProvider,
    QColor,
    QContextMenuEvent,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QPainter,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QLineEdit,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QWidget,
)

from nightexpress.i18n import N_, _
from nightexpress.views.icons import file_icon, icon
from nightexpress.views.theme import palette

HIDDEN_NAMES = frozenset({".git", ".idea", ".vscode", "__pycache__", ".DS_Store"})
# Cargo's build output: thousands of generated files, shown only on request
# ("Show the build folder (target)").
BUILD_DIR_NAME = "target"
# Files a beginner rarely needs: shown muted, and hidden by "Show hidden files".
SERVICE_NAMES = frozenset(
    {"Cargo.lock", ".gitignore", ".gitattributes", ".cargo", "rust-toolchain.toml"}
)

if sys.platform == "darwin":
    REVEAL_TEXT = N_("Показати у Finder")
elif sys.platform == "win32":
    REVEAL_TEXT = N_("Показати у Провіднику")
else:
    REVEAL_TEXT = N_("Показати у файловому менеджері")

_Index = QModelIndex | QPersistentModelIndex


class OutlineIconProvider(QAbstractFileIconProvider):
    """Monochrome outline icons instead of the colourful OS ones."""

    def icon(self, info: object) -> QIcon:  # type: ignore[override]
        if isinstance(info, QFileInfo):
            if info.isDir():
                return icon("folder", palette.TEXT_MUTED)
            return file_icon(info.fileName())
        if info == QAbstractFileIconProvider.IconType.Folder:
            return icon("folder", palette.TEXT_MUTED)
        return icon("file", palette.TEXT_MUTED)


class _ProjectFilter(QSortFilterProxyModel):
    rename_requested = Signal(object, str)  # Path, new name

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root: Path | None = None
        self.show_service = True
        self.show_build_dir = False
        self.error_paths: frozenset[Path] = frozenset()  # files with errors and their folders
        self.dirty_paths: frozenset[Path] = frozenset()

    def path(self, index: _Index) -> Path:
        model: QFileSystemModel = self.sourceModel()  # type: ignore[assignment]
        return Path(model.filePath(self.mapToSource(index)))

    def is_service(self, path: Path) -> bool:
        if self.root is None or not path.is_relative_to(self.root):
            return False
        parts = path.relative_to(self.root).parts
        # Only the project's own build folder: src/target/ is the user's module.
        return parts[:1] == (BUILD_DIR_NAME,) or any(part in SERVICE_NAMES for part in parts)

    def filterAcceptsRow(self, source_row: int, source_parent: _Index) -> bool:  # noqa: N802
        model: QFileSystemModel = self.sourceModel()  # type: ignore[assignment]
        index = model.index(source_row, 0, source_parent)
        name = model.fileName(index)
        if name in HIDDEN_NAMES:
            return False
        if self.root is None:
            return True
        path = Path(model.filePath(index))
        if name == BUILD_DIR_NAME and path.parent == self.root and not self.show_build_dir:
            return False
        # Siblings of the project folder share its parent; only the project itself is shown.
        if path.parent == self.root.parent:
            return path == self.root
        return self.show_service or name not in SERVICE_NAMES

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if role == Qt.ItemDataRole.ForegroundRole:
            path = self.path(index)
            if path in self.error_paths:
                return QColor(palette.ERROR)
            if self.is_service(path):
                return QColor(palette.TEXT_MUTED)
        return super().data(index, role)

    def flags(self, index: _Index) -> Qt.ItemFlag:
        flags = super().flags(index)
        if index.isValid() and self.path(index) != self.root:
            flags |= Qt.ItemFlag.ItemIsEditable  # in-place rename; see setData
        return flags

    def setData(  # noqa: N802
        self, index: _Index, value: object, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        """The in-place editor ends here: ask the controller to rename, change nothing."""
        if role == Qt.ItemDataRole.EditRole:
            path = self.path(index)
            name = str(value).strip()
            if name and name != path.name:
                self.rename_requested.emit(path, name)
        return False


class _TreeDelegate(QStyledItemDelegate):
    """Adds the "unsaved" dot and selects only the stem when a rename starts."""

    def __init__(self, proxy: _ProjectFilter, parent: QWidget) -> None:
        super().__init__(parent)
        self._proxy = proxy

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _Index) -> None:
        # One rounded highlight over the name and its icon. (A stylesheet ::item:selected would
        # also fill the indentation in front of the row, as a second shape with a seam.)
        state = option.state  # type: ignore[attr-defined]
        selected = bool(state & QStyle.StateFlag.State_Selected)
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.SELECTION if selected else palette.SURFACE_ALT))
            rect = QRectF(option.rect).adjusted(0, 1, -2, -1)  # type: ignore[attr-defined]
            painter.drawRoundedRect(rect, 6, 6)
            painter.restore()
        # No dotted/dark focus frame around the name: the highlight already shows the current
        # row, and the frame looked like a stray border inside it.
        plain = QStyleOptionViewItem(option)
        plain.state &= ~QStyle.StateFlag.State_HasFocus  # type: ignore[attr-defined]
        super().paint(painter, plain, index)
        if self._proxy.path(index) in self._proxy.dirty_paths:
            rect = option.rect  # type: ignore[attr-defined]
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.TEXT))
            painter.drawEllipse(QRectF(rect.right() - 16, rect.center().y() - 3, 7, 7))
            painter.restore()

    def setEditorData(self, editor: QWidget, index: _Index) -> None:  # noqa: N802
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit):
            name = editor.text()
            stem = len(name) - len(Path(name).suffix) if not name.startswith(".") else len(name)
            QTimer.singleShot(0, editor, lambda: editor.setSelection(0, stem))


class FileTreeView(QTreeView):
    file_activated = Signal(object)  # Path of a file (not a directory)
    new_file_requested = Signal(object)  # Path of the folder to create it in
    new_folder_requested = Signal(object)  # Path of the parent folder
    rename_requested = Signal(object, str)  # Path, new name
    delete_requested = Signal(object)  # Path
    reveal_requested = Signal(object)  # Path: show it in the OS file manager
    path_copied = Signal(str)
    show_service_changed = Signal(bool)
    show_build_dir_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fs = QFileSystemModel(self)
        self._fs.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        self._icons = OutlineIconProvider()
        self._fs.setIconProvider(self._icons)
        self._proxy = _ProjectFilter(self)
        self._proxy.setSourceModel(self._fs)
        self._proxy.rename_requested.connect(self.rename_requested)
        self.setModel(self._proxy)
        self.setItemDelegate(_TreeDelegate(self._proxy, self))
        for column in (1, 2, 3):  # size, type, date
            self.hideColumn(column)
        self.setHeaderHidden(True)
        self.setAnimated(True)
        self.setIndentation(18)
        self.setObjectName("fileTree")  # its highlight is drawn by _TreeDelegate
        # Renaming starts from the menu or the keyboard only, never from a double click.
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._root: Path | None = None
        self._activated_now = False
        # A double click emits both signals (Return emits only `activated`, and not on every
        # platform a double click does): _on_activated acts once per event.
        self.activated.connect(self._on_activated)
        self.doubleClicked.connect(self._on_activated)
        self._fs.directoryLoaded.connect(self._expand_root)

    @property
    def root(self) -> Path | None:
        return self._root

    def refresh_theme(self) -> None:
        self._fs.setIconProvider(self._icons)  # the model asks for every icon again

    def set_root(self, root: Path | None) -> None:
        self._root = root
        self._proxy.error_paths = frozenset()
        self._proxy.dirty_paths = frozenset()
        self._proxy.beginFilterChange()
        self._proxy.root = root
        self._proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        if root is None:
            self.setRootIndex(QModelIndex())
            self.setEnabled(False)
            return
        self.setEnabled(True)
        self._fs.setRootPath(str(root.parent))
        self._fs.index(str(root))  # start loading the project folder itself
        self.setRootIndex(self._proxy.mapFromSource(self._fs.index(str(root.parent))))
        self._expand_root()

    def _expand_root(self, *_args: object) -> None:
        if self._root is not None:
            index = self.index_of(self._root)
            if index.isValid():
                self.expand(index)
            src = self.index_of(self._root / "src")
            if src.isValid():
                self.expand(src)

    def select_path(self, path: Path) -> None:
        """Highlight the file that is active in the editor."""
        index = self.index_of(path)
        if index.isValid():
            self.setCurrentIndex(index)

    def reveal_path(self, path: Path) -> None:
        """Expand the folders above ``path``, select it and scroll to it."""
        if self._root is not None and path.is_relative_to(self._root):
            for parent in reversed(path.relative_to(self._root).parents):
                index = self.index_of(self._root / parent)
                if index.isValid():
                    self.expand(index)
        self.select_path(path)
        self.scrollTo(self.index_of(path))
        self.setFocus()

    def path_at(self, index: QModelIndex) -> Path:
        return self._proxy.path(index)

    def index_of(self, path: Path) -> QModelIndex:
        return self._proxy.mapFromSource(self._fs.index(str(path)))

    # --- state marks -----------------------------------------------------------------------

    def set_error_files(self, files: Iterable[Path]) -> None:
        """Files with compile errors: they and their folders are shown in red."""
        marked: set[Path] = set()
        for file in files:
            marked.add(file)
            if self._root is not None and file.is_relative_to(self._root):
                marked.update(
                    p for p in file.parents if p != self._root and self._root in p.parents
                )
        self._proxy.error_paths = frozenset(marked)
        self.viewport().update()

    @property
    def error_paths(self) -> frozenset[Path]:
        return self._proxy.error_paths

    def set_dirty(self, path: Path, dirty: bool) -> None:
        paths = self._proxy.dirty_paths
        self._proxy.dirty_paths = paths | {path} if dirty else paths - {path}
        self.viewport().update()

    @property
    def dirty_paths(self) -> frozenset[Path]:
        return self._proxy.dirty_paths

    # --- service files ---------------------------------------------------------------------

    @property
    def shows_service_files(self) -> bool:
        return self._proxy.show_service

    def set_show_service_files(self, show: bool) -> None:
        if show == self._proxy.show_service:
            return
        self._proxy.beginFilterChange()
        self._proxy.show_service = show
        self._proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        self._expand_root()
        self.show_service_changed.emit(show)

    @property
    def shows_build_dir(self) -> bool:
        return self._proxy.show_build_dir

    def set_show_build_dir(self, show: bool) -> None:
        """Show Cargo's ``target`` folder (muted, like the service files)."""
        if show == self._proxy.show_build_dir:
            return
        self._proxy.beginFilterChange()
        self._proxy.show_build_dir = show
        self._proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        self._expand_root()
        self.show_build_dir_changed.emit(show)

    def is_service(self, path: Path) -> bool:
        return self._proxy.is_service(path)

    def collapse_folders(self) -> None:
        """Collapse everything below the project folder."""
        self.collapseAll()
        if self._root is not None:
            self.expand(self.index_of(self._root))

    # --- actions ---------------------------------------------------------------------------

    def start_rename(self, path: Path) -> None:
        index = self.index_of(path)
        if index.isValid() and path != self._root:
            self.setCurrentIndex(index)
            self.edit(index)

    def copy_path(self, path: Path, relative: bool = False) -> None:
        text = str(path)
        if relative and self._root is not None and path.is_relative_to(self._root):
            text = path.relative_to(self._root).as_posix()
        QGuiApplication.clipboard().setText(text)
        self.path_copied.emit(text)

    def context_menu(self, path: Path) -> QMenu:
        """The menu for ``path`` (the project folder when the click missed every row)."""
        folder = path if path.is_dir() else path.parent
        is_root = path == self._root
        menu = QMenu(self)
        menu.addAction(_("Новий файл…"), lambda: self.new_file_requested.emit(folder))
        menu.addAction(_("Нова тека…"), lambda: self.new_folder_requested.emit(folder))
        menu.addSeparator()
        rename = menu.addAction(_("Перейменувати"), lambda: self.start_rename(path))
        delete = menu.addAction(_("Видалити"), lambda: self.delete_requested.emit(path))
        rename.setEnabled(not is_root)
        delete.setEnabled(not is_root)
        menu.addSeparator()
        menu.addAction(_(REVEAL_TEXT), lambda: self.reveal_requested.emit(path))
        menu.addAction(_("Скопіювати шлях"), lambda: self.copy_path(path))
        if not is_root:
            menu.addAction(_("Скопіювати відносний шлях"), lambda: self.copy_path(path, True))
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self._root is None:
            return
        index = self.indexAt(event.pos())
        if index.isValid():
            self.setCurrentIndex(index)
        menu = self.context_menu(self.path_at(index) if index.isValid() else self._root)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        index = self.currentIndex()
        if index.isValid() and self.state() != QAbstractItemView.State.EditingState:
            path = self.path_at(index)
            key, modifiers = event.key(), event.modifiers()
            command = bool(modifiers & Qt.KeyboardModifier.ControlModifier)  # ⌘ on macOS
            if key == Qt.Key.Key_F2:
                self.start_rename(path)
                return
            if key == Qt.Key.Key_Delete or (key == Qt.Key.Key_Backspace and command):
                if path != self._root:
                    self.delete_requested.emit(path)
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and path.is_file():
                self.file_activated.emit(path)
                return
        super().keyPressEvent(event)

    def _on_activated(self, index: QModelIndex) -> None:
        if self._activated_now:
            return
        self._activated_now = True
        QTimer.singleShot(0, lambda: setattr(self, "_activated_now", False))
        path = self.path_at(index)
        if path.is_file():
            self.file_activated.emit(path)
