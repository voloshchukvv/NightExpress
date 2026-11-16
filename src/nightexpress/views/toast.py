"""Non-modal notifications stacked in the bottom-right corner of the window.

They replace modal message boxes for errors and hints: the user keeps working, and a toast may
carry one action button ("Show log", "Choose device").
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_
from nightexpress.views.theme import palette
from nightexpress.views.widgets import TOOL_BUTTON_SIZE, icon_label, tool_button

TOAST_WIDTH = 400
MARGIN = 14
SPACING = 8
MAX_TOASTS = 3
# Inside a toast: the text keeps the same distance from every edge. The title sits in a row as
# tall as the close button, so the row itself starts higher than the text does.
STRIP = 4  # the colour strip on the left, part of the background
PADDING = 14
CLOSE_INSET = 9
AUTO_HIDE_MS = 8000


class ToastKind(Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


# Kind -> icon and the theme colour it is drawn in.
_ICONS = {
    ToastKind.INFO: ("info", "accent_bright"),
    ToastKind.SUCCESS: ("circle-check", "success"),
    ToastKind.WARNING: ("triangle-alert", "warning"),
    ToastKind.ERROR: ("circle-x", "error"),
}


class Toast(QFrame):
    closed = Signal(object)  # self

    def __init__(
        self,
        title: str,
        text: str,
        kind: ToastKind,
        action_text: str | None = None,
        action: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setProperty("kind", kind.value)
        self.setFixedWidth(TOAST_WIDTH)
        self.kind = kind
        self.title_text = title
        self.body_text = text
        self._action = action

        icon_name, token = _ICONS[kind]
        color = palette.color(token)
        self.title = QLabel(title, self)
        self.title.setObjectName("toastTitle")
        self.title.setWordWrap(True)
        self.close_button = tool_button("x", N_("Закрити"), self)
        self.close_button.clicked.connect(self.dismiss)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(icon_label(icon_name, 20, color, self), 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.title, 1, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)

        # The close button makes the title row taller than the title: take that half off the top.
        title_height = self.title.fontMetrics().height()
        top = max(PADDING - (TOOL_BUTTON_SIZE - title_height) // 2, CLOSE_INSET)
        has_body = bool(text) or (bool(action_text) and action is not None)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(STRIP + PADDING, top, CLOSE_INSET, PADDING if has_body else top)
        layout.setSpacing(8)
        layout.addLayout(head)
        self.text = QLabel(text, self)
        self.text.setObjectName("toastText")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setContentsMargins(0, 0, PADDING - CLOSE_INSET, 0)
        self.text.setVisible(bool(text))
        layout.addWidget(self.text)

        self.action_button: QPushButton | None = None
        if action_text and action is not None:
            self.action_button = QPushButton(action_text, self)
            self.action_button.setProperty("buttonRole", "accent")
            self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_button.clicked.connect(self._run_action)
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, PADDING - CLOSE_INSET, 0)
            row.addStretch(1)
            row.addWidget(self.action_button)
            layout.addLayout(row)

        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.dismiss)
        if kind in (ToastKind.INFO, ToastKind.SUCCESS):  # warnings and errors stay until closed
            self._timer.start(AUTO_HIDE_MS)

    def _run_action(self) -> None:
        action = self._action
        self.dismiss()
        if action is not None:
            action()

    def dismiss(self) -> None:
        self._timer.stop()
        self.closed.emit(self)


class ToastHost(QObject):
    """Owns the toasts of one window and keeps them stacked above its bottom-right corner."""

    def __init__(self, window: QWidget, bottom_inset: Callable[[], int] = lambda: 0) -> None:
        super().__init__(window)
        self._window = window
        self._bottom_inset = bottom_inset  # e.g. the status bar height
        self._toasts: list[Toast] = []
        window.installEventFilter(self)

    @property
    def toasts(self) -> tuple[Toast, ...]:
        return tuple(self._toasts)

    def show(
        self,
        title: str,
        text: str = "",
        kind: ToastKind = ToastKind.INFO,
        action_text: str | None = None,
        action: Callable[[], None] | None = None,
    ) -> Toast:
        for old in list(self._toasts):  # the same message again replaces the old one
            if (old.title_text, old.body_text) == (title, text):
                self._remove(old)
        while len(self._toasts) >= MAX_TOASTS:
            self._remove(self._toasts[0])
        toast = Toast(title, text, kind, action_text, action, self._window)
        toast.closed.connect(self._remove)
        self._toasts.append(toast)
        toast.show()
        self._relayout()
        return toast

    def clear(self) -> None:
        for toast in list(self._toasts):
            self._remove(toast)

    def _remove(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
            toast.hide()
            toast.deleteLater()
            self._relayout()

    def _relayout(self) -> None:
        bottom = self._window.height() - self._bottom_inset() - MARGIN
        for toast in reversed(self._toasts):  # the newest one is the lowest
            # Not sizeHint(): it measures wrapped text at another width and leaves empty space.
            height = toast.heightForWidth(TOAST_WIDTH)
            toast.setGeometry(
                self._window.width() - TOAST_WIDTH - MARGIN, bottom - height, TOAST_WIDTH, height
            )
            toast.raise_()
            bottom -= height + SPACING

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._window and event.type() is QEvent.Type.Resize and self._toasts:
            self._relayout()
        return False
