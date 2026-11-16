"""The "UART" tab: serial monitor for the board's USART."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QKeyEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, Text, _, live, on_change
from nightexpress.models.entities import SerialPortInfo
from nightexpress.models.state import DisplayMode, LineEnding, SerialMonitorModel
from nightexpress.models.state.serial_monitor_model import COMMON_BAUD_RATES
from nightexpress.views.icons import LABEL_ICON_SIZE, icon, label_icon
from nightexpress.views.panels.uart_render import MonitorRenderer
from nightexpress.views.theme import palette
from nightexpress.views.theme.text_colors import colored, recolor
from nightexpress.views.widgets import ElidedComboBox, ElidedLabel, Separator, tool_button

MAX_BLOCKS = 5000
MAX_HISTORY = 100
MAX_PAUSED_BYTES = 1024 * 1024
NO_PORT_TEXT = N_("Виберіть адаптер")
NO_PORTS_FOUND_TEXT = N_("Адаптерів не знайдено")
LINE_ENDINGS = (
    (LineEnding.NONE, N_("Без закінчення")),
    (LineEnding.LF, "LF (\\n)"),
    (LineEnding.CR, "CR (\\r)"),
    (LineEnding.CRLF, "CR+LF"),
)


class HistoryLineEdit(QLineEdit):
    """Line edit that recalls what was sent before with the Up/Down keys (as a shell does)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._position = 0  # == len(history) while typing a new line
        self._draft = ""

    @property
    def history(self) -> tuple[str, ...]:
        return tuple(self._history)

    def remember(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
            del self._history[:-MAX_HISTORY]
        self._position = len(self._history)
        self._draft = ""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Up and self._position > 0:
            if self._position == len(self._history):
                self._draft = self.text()
            self._position -= 1
            self.setText(self._history[self._position])
        elif event.key() == Qt.Key.Key_Down and self._position < len(self._history):
            self._position += 1
            at_end = self._position == len(self._history)
            self.setText(self._draft if at_end else self._history[self._position])
        else:
            super().keyPressEvent(event)


class UartPanel(QWidget):
    connect_toggled = Signal(bool)
    port_selected = Signal(object)  # stable id or None
    baud_selected = Signal(int)
    display_mode_selected = Signal(object)  # DisplayMode
    line_ending_selected = Signal(object)  # LineEnding
    send_requested = Signal(str)
    clear_requested = Signal()
    save_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("UartPanel")
        self._renderer = MonitorRenderer()
        self._mode = DisplayMode.TEXT

        self.port_combo = ElidedComboBox(self)
        self.port_combo.setFixedWidth(216)
        self.port_combo.setIconSize(QSize(16, 16))
        live(self.port_combo.setAccessibleName, N_("UART-адаптер"))
        self.port_combo.currentTextChanged.connect(self.port_combo.setToolTip)
        self.baud_combo = QComboBox(self)
        for baud in COMMON_BAUD_RATES:
            self.baud_combo.addItem("", baud)
        on_change(self.baud_combo, self._name_bauds, "names")
        self.connect_button = QPushButton(self)
        self.connect_button.setObjectName("connectButton")
        self.connect_button.setCheckable(True)
        self.connect_button.setMinimumWidth(124)
        self.connect_button.setIconSize(LABEL_ICON_SIZE)
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("", DisplayMode.TEXT)
        live(self.mode_combo.setItemText, N_("Текст"), 0)
        self.mode_combo.addItem("HEX", DisplayMode.HEX)
        self.timestamps = QCheckBox(self)
        live(self.timestamps.setText, N_("Час"))
        self.autoscroll = QCheckBox(self)
        live(self.autoscroll.setText, N_("Автопрокрутка"))
        self.autoscroll.setChecked(True)
        self.pause_button = tool_button("pause", N_("Пауза"), self)
        live(self.pause_button.setToolTip, N_("Пауза: не показувати нові дані, доки не зняти"))
        self.pause_button.setCheckable(True)
        for toggle in (self.connect_button, self.timestamps, self.autoscroll, self.pause_button):
            toggle.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.save_button = tool_button("save", N_("Зберегти вивід у файл…"), self)
        self.clear_button = tool_button("trash-2", N_("Очистити"), self)
        self.status_dot = QLabel("●", self)
        self.status_dot.setObjectName("uartDot")
        self.status = ElidedLabel("", self)
        self.status.setObjectName("mutedText")
        self._paused = bytearray()
        self._note_format = colored(QTextCharFormat(), "text_muted")
        self._status_error = False

        controls = QHBoxLayout()
        controls.setSpacing(7)
        for widget in (self.port_combo, self.baud_combo, self.connect_button):
            controls.addWidget(widget)
        controls.addWidget(Separator(parent=self))
        for widget in (
            self.mode_combo,
            self.timestamps,
            self.autoscroll,
            self.pause_button,
            self.save_button,
            self.clear_button,
        ):
            controls.addWidget(widget)
        controls.addSpacing(10)
        controls.addWidget(self.status_dot)
        controls.addWidget(self.status, 1)

        self.output = QPlainTextEdit(self)
        self.output.setObjectName("uartOutput")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(MAX_BLOCKS)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(font.pointSize() + 1)
        self.output.setFont(font)

        self.input = HistoryLineEdit(self)
        live(self.input.setPlaceholderText, N_("Надіслати на плату…  (↑ ↓ — попередні рядки)"))
        self.ending_combo = QComboBox(self)
        for index, (ending, text) in enumerate(LINE_ENDINGS):
            self.ending_combo.addItem("", ending)
            live(self.ending_combo.setItemText, text, index)
        self.send_button = QPushButton(label_icon("play", palette.ACCENT_TEXT), "", self)
        live(self.send_button.setText, N_("Надіслати"))
        self.send_button.setProperty("buttonRole", "primary")
        self.send_button.setIconSize(LABEL_ICON_SIZE)
        send = QHBoxLayout()
        send.setSpacing(10)
        send.addWidget(self.input, 1)
        send.addWidget(self.ending_combo)
        send.addWidget(self.send_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addLayout(controls)
        layout.addWidget(self.output, 1)
        layout.addLayout(send)

        self.connect_button.toggled.connect(self.connect_toggled)
        self.port_combo.activated.connect(lambda _i: self.port_selected.emit(self.current_port_id))
        self.baud_combo.activated.connect(lambda _i: self.baud_selected.emit(self.current_baud))
        self.mode_combo.activated.connect(
            lambda _i: self.display_mode_selected.emit(self.mode_combo.currentData())
        )
        self.ending_combo.activated.connect(
            lambda _i: self.line_ending_selected.emit(self.ending_combo.currentData())
        )
        self.clear_button.clicked.connect(self.clear_requested)
        self.save_button.clicked.connect(self.save_requested)
        self.pause_button.toggled.connect(self.set_paused)
        self.send_button.clicked.connect(self._send)
        self.input.returnPressed.connect(self._send)
        self.set_ports([], None)
        self.set_connected(False)

    # --- binding ---------------------------------------------------------------------------

    def bind(self, model: SerialMonitorModel) -> None:
        model.data_received.connect(self.append_data)
        model.cleared.connect(self.clear_output)
        model.open_changed.connect(self.set_connected)
        model.baud_rate_changed.connect(self.set_baud)
        model.display_mode_changed.connect(self.set_display_mode)
        model.line_ending_changed.connect(self.set_line_ending)
        model.autoscroll_changed.connect(self.autoscroll.setChecked)
        self.set_baud(model.baud_rate)
        self.set_line_ending(model.line_ending)

    # --- state -----------------------------------------------------------------------------

    def set_ports(
        self,
        ports: Iterable[SerialPortInfo],
        current: str | None,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        labels = labels or {}
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItem(_(NO_PORT_TEXT), None)
        for port in ports:
            text = port.label + (f" — {labels[port.stable_id]}" if port.stable_id in labels else "")
            self.port_combo.addItem(icon("usb", palette.TEXT_MUTED), text, port.stable_id)
        no_ports = self.port_combo.count() == 1
        if no_ports:
            # A list holding only the prompt the box already shows would say nothing.
            self.port_combo.addItem(_(NO_PORTS_FOUND_TEXT), None)
            self.port_combo.model().item(1).setEnabled(False)
        self.port_combo.view().setRowHidden(0, no_ports)
        index = self.port_combo.findData(current) if current else 0
        self.port_combo.setCurrentIndex(max(index, 0))
        self.port_combo.setToolTip(self.port_combo.currentText())
        self.port_combo.blockSignals(False)
        # Not the whole list again after a change of the language: the user may have picked
        # another port since. Only the rows without a port carry translated texts.
        on_change(self.port_combo, self._name_placeholder_rows, "placeholders")

    def _name_placeholder_rows(self) -> None:
        for row in range(self.port_combo.count()):
            if self.port_combo.itemData(row) is None:
                text = NO_PORT_TEXT if row == 0 else NO_PORTS_FOUND_TEXT
                self.port_combo.setItemText(row, _(text))
        self.port_combo.setToolTip(self.port_combo.currentText())

    @property
    def current_port_id(self) -> str | None:
        return self.port_combo.currentData()

    @property
    def current_baud(self) -> int:
        return int(self.baud_combo.currentData())

    def set_connected(self, connected: bool) -> None:
        self.connect_button.blockSignals(True)
        self.connect_button.setChecked(connected)
        self.connect_button.blockSignals(False)
        live(self.connect_button.setText, lambda: _("Відключити") if connected else _("Підключити"))
        self.connect_button.setIcon(label_icon("unlink" if connected else "link", palette.TEXT))
        self.status_dot.setVisible(connected)
        self.port_combo.setEnabled(not connected)
        for widget in (self.input, self.send_button):
            widget.setEnabled(connected)

    def set_baud(self, baud: int) -> None:
        index = self.baud_combo.findData(baud)
        if index < 0:
            self.baud_combo.addItem("", baud)
            self._name_bauds()
            index = self.baud_combo.count() - 1
        self.baud_combo.setCurrentIndex(index)

    def _name_bauds(self) -> None:
        for index in range(self.baud_combo.count()):
            baud = self.baud_combo.itemData(index)
            self.baud_combo.setItemText(index, _("{baud} бод").format(baud=baud))

    def set_display_mode(self, mode: DisplayMode) -> None:
        self._mode = mode
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
        self._renderer.reset()

    def set_line_ending(self, ending: LineEnding) -> None:
        self.ending_combo.setCurrentIndex(self.ending_combo.findData(ending))

    def set_status(self, text: Text, error: bool = False) -> None:
        """``text``: see i18n.live."""
        live(self.status.setText, text)
        live(self.status.setToolTip, text)
        self._status_error = error
        self.status.setStyleSheet(f"color: {palette.ERROR}" if error else "")

    def refresh_theme(self) -> None:
        self._note_format = colored(QTextCharFormat(), "text_muted")
        recolor(self.output.document())
        self.status.setStyleSheet(f"color: {palette.ERROR}" if self._status_error else "")

    # --- output ----------------------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        return self.pause_button.isChecked()

    def set_paused(self, paused: bool) -> None:
        """While paused nothing is lost: the data waits and appears when the pause ends."""
        if self.pause_button.isChecked() != paused:
            self.pause_button.setChecked(paused)  # re-enters through toggled
            return
        self.pause_button.setIcon(
            icon("play" if paused else "pause", palette.WARNING if paused else palette.TEXT_MUTED)
        )
        live(
            self.pause_button.setToolTip,
            lambda: (
                _("Продовжити показ")
                if paused
                else _("Пауза: не показувати нові дані, доки не зняти")
            ),
        )
        if not paused and self._paused:
            waiting, self._paused = bytes(self._paused), bytearray()
            self.append_data(waiting)

    @property
    def paused_bytes(self) -> int:
        return len(self._paused)

    def append_note(self, text: str) -> None:
        """A line from the IDE itself (e.g. "— reconnected —"), not from the board."""
        cursor = QTextCursor(self.output.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.output.document().isEmpty() and cursor.positionInBlock() > 0:
            cursor.insertText("\n")
        cursor.insertText(f"— {text} —\n", self._note_format)
        cursor.setCharFormat(QTextCharFormat())
        self._renderer.reset()
        if self.autoscroll.isChecked():
            bar = self.output.verticalScrollBar()
            bar.setValue(bar.maximum())

    def append_data(self, data: bytes) -> None:
        if self.is_paused:
            self._paused += data
            del self._paused[:-MAX_PAUSED_BYTES]
            return
        text = self._renderer.render(data, self._mode, self.timestamps.isChecked())
        if not text:
            return
        bar = self.output.verticalScrollBar()
        cursor = QTextCursor(self.output.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, QTextCharFormat())
        if self.autoscroll.isChecked():
            bar.setValue(bar.maximum())

    def clear_output(self) -> None:
        self.output.clear()
        self._paused.clear()
        self._renderer.reset()

    def text(self) -> str:
        return self.output.toPlainText()

    def _send(self) -> None:
        if self.input.isEnabled():
            text = self.input.text()
            self.send_requested.emit(text)
            self.input.remember(text)
            self.input.clear()
