"""Small building blocks shared by the views (styled through object names in the QSS)."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import Text, live
from nightexpress.views.icons import icon
from nightexpress.views.theme import palette

TOOL_BUTTON_SIZE = 28


def icon_label(
    name: str, size: int = 18, color: str | None = None, parent: QWidget | None = None
) -> QLabel:
    label = QLabel(parent)
    set_icon(label, name, size, color)
    label.setFixedSize(size, size)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return label


THEMED_ICON_PROPERTY = "themedIcon"  # [name, size, colour token] of a label's picture


def set_icon(label: QLabel, name: str, size: int = 18, color: str | None = None) -> None:
    color = color or palette.TEXT
    label.setPixmap(icon(name, color).pixmap(QSize(size, size)))
    # A pixmap does not know how it was made: leave a note for the next theme (see retint).
    themed = isinstance(color, palette.ThemeColor)
    label.setProperty(THEMED_ICON_PROPERTY, [name, size, color.token] if themed else None)


def tool_button(name: str, label: Text, parent: QWidget) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("panelTool")
    button.setFixedSize(TOOL_BUTTON_SIZE, TOOL_BUTTON_SIZE)
    button.setIcon(icon(name, palette.TEXT_MUTED))
    button.setIconSize(QSize(16, 16))
    live(button.setToolTip, label)
    live(button.setAccessibleName, label)
    button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    return button


_TAB_SCROLL_NAMES = {"ScrollLeftButton": "Scroll Left", "ScrollRightButton": "Scroll Right"}


def retranslate_qt_texts(root: QWidget) -> None:
    """Texts Qt sets once when it creates a widget and leaves after its translation changes:
    the accessible names of the scroll buttons of tab bars."""
    for bar in root.findChildren(QTabBar):
        for button in bar.findChildren(QToolButton):
            source = _TAB_SCROLL_NAMES.get(button.objectName())
            if source is not None:
                button.setAccessibleName(QCoreApplication.translate("QTabBar", source))


class ElidedComboBox(QComboBox):
    """Shorten the current label without changing the text shown in the popup.

    With the ``readOnly`` property (one possible value, shown as plain text) the list does not
    open: a list with the single item it already shows would only look broken.
    """

    def showPopup(self) -> None:  # noqa: N802
        if self.property("readOnly") is not True:
            super().showPopup()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        style = QStyle.ComplexControl.CC_ComboBox
        field = self.style().subControlRect(
            style, option, QStyle.SubControl.SC_ComboBoxEditField, self
        )
        icon_width = 0 if option.currentIcon.isNull() else option.iconSize.width() + 4
        option.currentText = option.fontMetrics.elidedText(
            option.currentText, Qt.TextElideMode.ElideRight, max(0, field.width() - icon_width - 2)
        )
        painter.drawComplexControl(style, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


class ElidedLabel(QLabel):
    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event: QPaintEvent) -> None:
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width()
        )
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(self.contentsRect(), self.alignment(), text)


class Separator(QFrame):
    def __init__(self, vertical: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("separator")
        if vertical:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class Panel(QFrame):
    """Rounded panel with a header row (title + optional tool buttons) and a body."""

    def __init__(self, title: Text, body: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.title = QLabel(self)
        live(self.title.setText, title)
        self.title.setObjectName("panelTitle")
        self.header = QWidget(self)
        self.header.setObjectName("panelHeader")
        header_padding = 6
        self.header.setMinimumHeight(TOOL_BUTTON_SIZE + 2 * header_padding)
        self._header_layout = QHBoxLayout(self.header)
        self._header_layout.setContentsMargins(14, header_padding, 8, header_padding)
        self._header_layout.setSpacing(4)
        self._header_layout.addWidget(self.title)
        self._header_layout.addStretch(1)
        self.body = body
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(Separator(vertical=False, parent=self))
        layout.addWidget(body, 1)

    def add_tool_button(self, icon_name: str, tooltip: Text) -> QToolButton:
        button = tool_button(icon_name, tooltip, self.header)
        self._header_layout.addWidget(button)
        return button


class ClickableCard(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # no outline left after a click
        self._pressed = False

    def _set_pressed(self, pressed: bool) -> None:
        self._pressed = pressed
        self.setProperty("pressed", pressed)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(True)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            clicked = self._pressed and self.rect().contains(event.position().toPoint())
            self._set_pressed(False)
            if clicked:
                self.clicked.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)


class CardButton(ClickableCard):
    """An action with a title and subtitle, optionally showing project metadata."""

    def __init__(
        self,
        icon_name: str,
        title: Text,
        subtitle: Text,
        parent: QWidget | None = None,
        object_name: str = "card",
        icon_color: str | None = None,
        icon_size: int = 30,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.icon = icon_label(icon_name, icon_size - 6, icon_color, self)
        self.icon.setObjectName("cardIcon")
        self.icon.setFixedSize(42, 42)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = ElidedLabel if object_name == "recentCard" else QLabel
        self.title = label("", self)
        live(self.title.setText, title)
        self.title.setObjectName("cardTitle")
        self.subtitle = label("", self)
        live(self.subtitle.setText, subtitle)
        self.subtitle.setObjectName("cardSubtitle")
        if object_name != "recentCard":
            self.title.setWordWrap(True)
            self.subtitle.setWordWrap(True)
        self.right = ElidedLabel("", self)
        self.right.setObjectName("cardRight")
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        layout.addWidget(self.icon)
        layout.addLayout(texts, 1)
        layout.addWidget(self.right)
        for child in (self.title, self.subtitle, self.right):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


class ClickableLabel(QLabel):
    """Status-bar text that reacts to a click (only while ``clickable`` is set)."""

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._clickable = False

    def set_clickable(self, clickable: bool) -> None:
        self._clickable = clickable
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus if clickable else Qt.FocusPolicy.NoFocus)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._clickable and event.button() is Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        activation_keys = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
        if self._clickable and event.key() in activation_keys:
            self.clicked.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class Spinner(QWidget):
    """Small rotating arc shown while an operation runs; keeps its place when stopped."""

    STEP_DEGREES = 30
    INTERVAL_MS = 80

    def __init__(self, size: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)

    @property
    def is_spinning(self) -> bool:
        return self._timer.isActive()

    def set_spinning(self, spinning: bool) -> None:
        if spinning == self.is_spinning:
            return
        if spinning:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _advance(self) -> None:
        self._angle = (self._angle + self.STEP_DEGREES) % 360
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        if not self.is_spinning:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(palette.ACCENT_BRIGHT), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        painter.drawArc(rect, -self._angle * 16, 270 * 16)


class ActivityBar(QWidget):
    """Thin progress strip: idle (invisible), indeterminate (moving chunk) or a percentage."""

    HEIGHT = 2
    INTERVAL_MS = 30
    CHUNK = 0.25  # width of the moving chunk, as a share of the bar

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)  # always reserved, so the layout never jumps
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._active = False
        self._percent: int | None = None
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def percent(self) -> int | None:
        return self._percent

    def start(self, percent: int | None = None) -> None:
        """Show progress; ``None`` means the duration is unknown."""
        self._active = True
        self._percent = None if percent is None else max(0, min(100, percent))
        if self._percent is None:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def stop(self) -> None:
        self._active = False
        self._percent = None
        self._timer.stop()
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.012) % (1.0 + self.CHUNK)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        if not self._active:
            return
        painter = QPainter(self)
        width = self.width()
        if self._percent is None:
            left = int((self._phase - self.CHUNK) * width)
            painter.fillRect(
                left, 0, int(self.CHUNK * width), self.height(), QColor(palette.ACCENT_BRIGHT)
            )
        else:
            painter.fillRect(0, 0, width, self.height(), QColor(palette.BORDER))
            painter.fillRect(
                0, 0, width * self._percent // 100, self.height(), QColor(palette.ACCENT_BRIGHT)
            )


class MeterBar(QWidget):
    """Rounded usage bar; turns yellow and then red as it fills up."""

    HEIGHT = 6
    WARNING_PERCENT = 80
    DANGER_PERCENT = 95

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._percent: float | None = None

    @property
    def percent(self) -> float | None:
        return self._percent

    @property
    def color(self) -> str:
        percent = self._percent or 0
        if percent >= self.DANGER_PERCENT:
            return palette.ERROR
        if percent >= self.WARNING_PERCENT:
            return palette.WARNING
        return palette.ACCENT

    def set_percent(self, percent: float | None) -> None:
        self._percent = None if percent is None else max(0.0, min(100.0, percent))
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2
        painter.setBrush(QColor(palette.BORDER))
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        if self._percent:
            # Even a tiny program gets a visible dot, so "built" differs from "not built yet".
            width = max(self.height(), self.width() * self._percent / 100)
            painter.setBrush(QColor(self.color))
            painter.drawRoundedRect(QRectF(0, 0, width, self.height()), radius, radius)
