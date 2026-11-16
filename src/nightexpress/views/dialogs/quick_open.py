"""Quick open (⌘P): type a few letters of a file name, press Enter."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, live
from nightexpress.views.fuzzy import fuzzy_filter
from nightexpress.views.icons import file_icon

WIDTH = 560
MAX_RESULTS = 50
VISIBLE_ROWS = 10
ROW_HEIGHT = 34

_LIST_KEYS = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)


class QuickOpenDialog(QFrame):
    file_chosen = Signal(object)  # Path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("quickOpen")
        self.setFixedWidth(WIDTH)
        self._root = Path()
        self._files: list[str] = []  # relative POSIX paths, the most relevant first

        self.query = QLineEdit(self)
        self.query.setObjectName("quickOpenQuery")
        live(self.query.setPlaceholderText, N_("Назва файлу…"))
        self.query.setClearButtonEnabled(True)
        self.query.textChanged.connect(self._refresh)
        self.query.returnPressed.connect(self.accept_current)
        self.query.installEventFilter(self)
        self.results = QListWidget(self)
        self.results.setObjectName("quickOpenResults")
        self.results.setIconSize(QSize(18, 18))
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemClicked.connect(lambda _item: self.accept_current())
        self.empty = QLabel(self)
        live(self.empty.setText, N_("Нічого не знайдено"))
        self.empty.setObjectName("mutedText")
        self.empty.setContentsMargins(10, 8, 10, 8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.query)
        layout.addWidget(self.results)
        layout.addWidget(self.empty)

    def open(self, root: Path, files: Sequence[Path], anchor: QWidget | None = None) -> None:
        """Show the list of project ``files`` under the top edge of ``anchor``."""
        self._root = root
        self._files = [f.relative_to(root).as_posix() for f in files if f.is_relative_to(root)]
        self.query.clear()
        self._refresh()
        if anchor is not None:
            top_left = anchor.mapToGlobal(QPoint((anchor.width() - WIDTH) // 2, 6))
            self.move(top_left)
        self.show()
        self.query.setFocus()

    @property
    def shown_files(self) -> list[str]:
        return [self.results.item(i).text() for i in range(self.results.count())]

    @property
    def current_file(self) -> Path | None:
        item = self.results.currentItem()
        return self._root / item.text() if item is not None else None

    def accept_current(self) -> None:
        path = self.current_file
        if path is not None:
            self.hide()
            self.file_chosen.emit(path)

    def _refresh(self) -> None:
        matches = fuzzy_filter(self.query.text(), self._files, MAX_RESULTS)
        self.results.clear()
        for relative in matches:
            item = QListWidgetItem(file_icon(relative.rsplit("/", 1)[-1]), relative)
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.results.addItem(item)
        if matches:
            self.results.setCurrentRow(0)
        self.results.setVisible(bool(matches))
        self.results.setFixedHeight(min(len(matches), VISIBLE_ROWS) * ROW_HEIGHT + 4)
        self.empty.setVisible(not matches)
        self.adjustSize()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Arrow keys typed in the query field move the selection in the list."""
        if (
            watched is self.query
            and isinstance(event, QKeyEvent)
            and event.type() is QEvent.Type.KeyPress
            and event.key() in _LIST_KEYS
        ):
            steps = {
                Qt.Key.Key_Up: -1,
                Qt.Key.Key_Down: 1,
                Qt.Key.Key_PageUp: -VISIBLE_ROWS,
                Qt.Key.Key_PageDown: VISIBLE_ROWS,
            }
            self.move_selection(steps[Qt.Key(event.key())])
            return True
        return False

    def move_selection(self, step: int) -> None:
        count = self.results.count()
        if count:
            row = max(0, min(count - 1, self.results.currentRow() + step))
            self.results.setCurrentRow(row)
