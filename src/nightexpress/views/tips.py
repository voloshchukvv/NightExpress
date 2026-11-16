"""First-run tour: a few bubbles pointing at the controls a newcomer needs, one at a time.

Each bubble is a child of the main window with a small arrow towards its target. It sits below
the target when there is room (arrow up), otherwise above it (arrow down), and follows the
window when it is resized.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from nightexpress.i18n import N_, Text, _, live, redo_on_switch, resolve
from nightexpress.views.theme import palette

ARROW = 9  # px
MARGIN = 12  # from the window edges
WIDTH = 330


@dataclass(frozen=True)
class Tip:
    title: Text  # see i18n.live
    text: Text
    target: Callable[[], QRect]  # where to point, in window coordinates
    before: Callable[[], None] | None = None  # e.g. make the bottom panel visible


class TipBubble(QWidget):
    next_requested = Signal()
    skip_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("tipBubble")
        self.setFixedWidth(WIDTH)
        self._arrow_x = WIDTH // 2
        self._arrow_up = True
        self.step = QLabel(self)
        self.step.setObjectName("tipStep")
        self.title = QLabel(self)
        self.title.setObjectName("tipTitle")
        self.text = QLabel(self)
        self.text.setObjectName("tipText")
        self.text.setWordWrap(True)
        self.skip_button = QPushButton(self)
        live(self.skip_button.setText, N_("Пропустити"))
        self.skip_button.setObjectName("linkButton")
        self.skip_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.skip_button.clicked.connect(self.skip_requested)
        self.next_button = QPushButton(self)
        live(self.next_button.setText, N_("Далі"))
        self.next_button.setProperty("buttonRole", "primary")
        self.next_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_button.clicked.connect(self.next_requested)
        buttons = QHBoxLayout()
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        buttons.addWidget(self.next_button)
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(8)
        self._layout.addWidget(self.step)
        self._layout.addWidget(self.title)
        self._layout.addWidget(self.text)
        self._layout.addSpacing(4)
        self._layout.addLayout(buttons)
        self._set_margins()

    @property
    def arrow_up(self) -> bool:
        return self._arrow_up

    @redo_on_switch
    def show_tip(self, tip: Tip, index: int, count: int) -> None:
        self.step.setText(_("{current} з {total}").format(current=index + 1, total=count))
        self.title.setText(resolve(tip.title))
        self.text.setText(resolve(tip.text))
        last = index == count - 1
        self.next_button.setText(_("Зрозуміло") if last else _("Далі"))
        self.skip_button.setVisible(not last)
        self._fit()

    def point_at(self, target: QRect) -> None:
        """Place the bubble next to ``target`` (window coordinates) and aim the arrow at it."""
        window = self.parentWidget()
        assert window is not None
        self._arrow_up = target.bottom() + self._fit() + MARGIN < window.height()
        self._set_margins()
        self._fit()
        x = target.center().x() - self.width() // 2
        x = max(MARGIN, min(x, window.width() - self.width() - MARGIN))
        y = target.bottom() + 2 if self._arrow_up else target.top() - self.height() - 2
        self.move(x, y)
        self._arrow_x = max(18, min(target.center().x() - x, self.width() - 18))
        self.update()

    def _fit(self) -> int:
        """Height for the fixed width: adjustSize() ignores the wrapped lines of the text."""
        height = self._layout.totalHeightForWidth(WIDTH)
        self.resize(WIDTH, height)
        return height

    def _set_margins(self) -> None:
        top, bottom = (ARROW, 0) if self._arrow_up else (0, ARROW)
        self._layout.setContentsMargins(18, 14 + top, 18, 14 + bottom)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._arrow_up:
            body.setTop(body.top() + ARROW)
        else:
            body.setBottom(body.bottom() - ARROW)
        path = QPainterPath()
        path.addRoundedRect(body, 8, 8)
        tip_y = 0.5 if self._arrow_up else self.height() - 0.5
        base_y = body.top() if self._arrow_up else body.bottom()
        arrow = QPainterPath()
        arrow.moveTo(QPointF(self._arrow_x - ARROW, base_y))
        arrow.lineTo(QPointF(self._arrow_x, tip_y))
        arrow.lineTo(QPointF(self._arrow_x + ARROW, base_y))
        arrow.closeSubpath()
        path = path.united(arrow)
        painter.setPen(QPen(QColor(palette.ACCENT_BRIGHT), 1))
        painter.setBrush(QColor(palette.SURFACE_ALT))
        painter.drawPath(path)


class TipTour(QObject):
    """Shows the tips one after another; ``finished`` after the last one or "Skip"."""

    finished = Signal()

    def __init__(self, window: QWidget, tips: Sequence[Tip]) -> None:
        super().__init__(window)
        self._window = window
        self._tips = list(tips)
        self._index = -1
        self.bubble = TipBubble(window)
        self.bubble.hide()
        self.bubble.next_requested.connect(self.next)
        self.bubble.skip_requested.connect(self.stop)
        window.installEventFilter(self)

    @property
    def index(self) -> int:
        """The tip on screen (0-based), -1 when the tour is over."""
        return self._index

    def start(self) -> None:
        self._index = -1
        self.next()

    def next(self) -> None:
        self._index += 1
        if self._index >= len(self._tips):
            self.stop()
            return
        tip = self._tips[self._index]
        if tip.before is not None:
            tip.before()
        self.bubble.show_tip(tip, self._index, len(self._tips))
        self.bubble.show()
        self.bubble.raise_()
        self._place()

    def stop(self) -> None:
        self._index = -1
        self.bubble.hide()
        self._window.removeEventFilter(self)
        self.bubble.deleteLater()
        self.finished.emit()

    def _place(self) -> None:
        if 0 <= self._index < len(self._tips):
            self.bubble.point_at(self._tips[self._index].target())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._window and event.type() is QEvent.Type.Resize:
            self._place()
        return super().eventFilter(watched, event)


def widget_rect(widget: QWidget, window: QWidget) -> QRect:
    """``widget``'s geometry in ``window`` coordinates."""
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
