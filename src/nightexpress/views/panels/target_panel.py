"""Right-hand "Target" panel: the chip the project is built for, and what helps to flash it.

The top part shows the chip and its facts; below it: memory usage, wiring, board facts and the
last upload.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, Text, _, live, redo_on_switch
from nightexpress.models.entities import (
    BoardDefinition,
    ChipDefinition,
    MemoryUsage,
    UploadMethod,
)
from nightexpress.views import formatting
from nightexpress.views.formatting import group_digits
from nightexpress.views.theme import palette
from nightexpress.views.toolbar_view import board_label
from nightexpress.views.widgets import ClickableLabel, MeterBar, Separator, icon_label

UNKNOWN = "—"
NO_BUILD_TEXT = N_("З'явиться після збірки")
METHOD_NAMES = {
    UploadMethod.PROBE_RS: "ST-Link (SWD)",
    UploadMethod.SERIAL_BOOTLOADER: N_("USB-TTL (UART-бутлоадер)"),
    UploadMethod.USB_DFU: N_("USB плати (DFU)"),
}
HELP_TOPICS = {
    UploadMethod.PROBE_RS: "stlink",
    UploadMethod.SERIAL_BOOTLOADER: "usb_ttl",
    UploadMethod.USB_DFU: "usb_dfu",
}
# The board's own USB socket carries the chip's USB pins: only a cable is needed.
DFU_WIRING = (("USB", "USB (PA11/PA12)"),)
# SWD pins are the same on every STM32.
SWD_WIRING = (
    ("SWDIO", "PA13 (SWDIO)"),
    ("SWCLK", "PA14 (SWCLK)"),
    ("GND", "GND"),
    ("3.3V", "3.3V"),
)


def core_name(core: str) -> str:
    """cortex-m3 -> ARM Cortex-M3."""
    return "ARM " + "-".join(part.capitalize() for part in core.split("-"))


def percent_text(percent: float) -> str:
    return formatting.percent("<1" if 0 < percent < 1 else f"{percent:.0f}")


def usage_text(used: int, total: int) -> str:
    """ "1,064 / 32,768 bytes (3%)" in English."""
    return _("{used} / {total} байт ({percent})").format(
        used=group_digits(used), total=group_digits(total), percent=percent_text(100 * used / total)
    )


def wiring(method: UploadMethod, chip: ChipDefinition | None) -> tuple[tuple[str, str], ...]:
    """(adapter pin, board pin) pairs for the upload method."""
    if method is UploadMethod.PROBE_RS:
        return SWD_WIRING
    if method is UploadMethod.USB_DFU:
        return DFU_WIRING
    if chip is None or chip.bootloader is None:
        return ()
    # Crossed: the adapter transmits into the chip's RX and listens on its TX.
    return (("TX", chip.bootloader.rx_pin), ("RX", chip.bootloader.tx_pin), ("GND", "GND"))


class _MemoryRow(QWidget):
    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.caption = QLabel(caption, self)
        self.caption.setObjectName("fieldCaption")
        self.value = QLabel(self)
        self.value.setObjectName("mutedText")
        self.bar = MeterBar(self)
        head = QHBoxLayout()
        head.addWidget(self.caption)
        head.addStretch(1)
        head.addWidget(self.value)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(head)
        layout.addWidget(self.bar)
        self.set_usage(None)

    @redo_on_switch
    def set_usage(self, used: int | None, total: int = 0) -> None:
        if used is None or total <= 0:
            self.value.setText(_(NO_BUILD_TEXT))
            self.value.setObjectName("mutedText")
            self.bar.set_percent(None)
        else:
            self.value.setText(usage_text(used, total))
            self.value.setObjectName("")
            self.bar.set_percent(100 * used / total)
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)


class TargetPanel(QScrollArea):
    help_requested = Signal(str)  # topic id ("stlink" / "usb_ttl" / "usb_dfu")
    pin_copied = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TargetPanel")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        content = QWidget(self)
        content.setObjectName("targetContent")
        content.setAutoFillBackground(False)
        self.setWidget(content)

        self._board: BoardDefinition | None = None
        self._chip: ChipDefinition | None = None
        self._selected_method: UploadMethod | None = None

        # --- chip ------------------------------------------------------------------------
        self.chip_title = QLabel(UNKNOWN, content)
        self.chip_title.setObjectName("chipTitle")
        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        title_row.addWidget(icon_label("cpu", 34, palette.TEXT, content))
        title_row.addWidget(self.chip_title, 1)

        self.core = QLabel(UNKNOWN, content)
        self.flash = QLabel(UNKNOWN, content)
        self.ram = QLabel(UNKNOWN, content)
        self.probe = QLabel(UNKNOWN, content)
        self.target = QLabel(UNKNOWN, content)
        form = self._form(
            content,
            (
                (N_("Ядро:"), self.core),
                (N_("Flash:"), self.flash),
                (N_("RAM:"), self.ram),
                (N_("Пробник:"), self.probe),
                (N_("Ціль (target):"), self.target),
            ),
        )

        # --- memory usage ------------------------------------------------------------------
        self.flash_usage = _MemoryRow("Flash", content)
        self.ram_usage = _MemoryRow("RAM", content)

        # --- connection --------------------------------------------------------------------
        self.method = QLabel(UNKNOWN, content)
        self.method_note = QLabel("", content)
        self.method_note.setObjectName("mutedText")
        self.method_note.setWordWrap(True)
        self.wiring_grid = QGridLayout()
        self.wiring_grid.setHorizontalSpacing(10)
        self.wiring_grid.setVerticalSpacing(8)
        self.wiring_grid.setColumnStretch(2, 1)
        self._wiring_widgets: list[QWidget] = []
        self.wiring_note = QLabel("", content)
        self.wiring_note.setObjectName("mutedText")
        self.wiring_note.setWordWrap(True)
        self.help_link = QPushButton(content)
        live(self.help_link.setText, N_("Докладніше  ›"))
        self.help_link.setObjectName("linkButton")
        self.help_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.help_link.clicked.connect(self._on_help_clicked)

        # --- board facts -------------------------------------------------------------------
        self.led = ClickableLabel(UNKNOWN, content)
        self.button = ClickableLabel(UNKNOWN, content)
        self.uart = QLabel(UNKNOWN, content)
        for label in (self.led, self.button, self.uart):
            label.setWordWrap(True)  # the panel can be made narrow
        self.led.clicked.connect(lambda: self._copy_pin(self._board.led.pin if self._led() else ""))
        self.button.clicked.connect(lambda: self._copy_pin(self._on_board_button_pin()))
        self._board_form = self._form(
            content,
            ((N_("Світлодіод:"), self.led), (N_("Кнопка:"), self.button), (N_("UART:"), self.uart)),
        )

        # --- last upload -------------------------------------------------------------------
        self.last_upload = QLabel("", content)
        self.last_upload.setWordWrap(True)
        self.last_upload_title = self._section(N_("Остання прошивка"), content)
        self._last_upload_separator = Separator(vertical=False, parent=content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(16)
        layout.addLayout(title_row)
        layout.addWidget(Separator(vertical=False, parent=content))
        layout.addLayout(form)
        layout.addWidget(Separator(vertical=False, parent=content))

        layout.addWidget(self._section(N_("Пам'ять"), content))
        layout.addWidget(self.flash_usage)
        layout.addWidget(self.ram_usage)
        layout.addWidget(Separator(vertical=False, parent=content))

        layout.addWidget(self._section(N_("Підключення"), content))
        layout.addWidget(self.method)
        layout.addWidget(self.method_note)
        layout.addLayout(self.wiring_grid)
        layout.addWidget(self.wiring_note)
        layout.addWidget(self.help_link, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(Separator(vertical=False, parent=content))

        layout.addWidget(self._section(N_("Плата"), content))
        layout.addLayout(self._board_form)
        layout.addWidget(self._last_upload_separator)
        layout.addWidget(self.last_upload_title)
        layout.addWidget(self.last_upload)
        layout.addStretch(1)

        self.set_board(None, None)
        self.set_last_upload(None)

    # --- construction helpers --------------------------------------------------------------

    @staticmethod
    def _section(text: Text, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        live(label.setText, text)
        label.setObjectName("targetSection")
        return label

    @staticmethod
    def _form(parent: QWidget, rows: Sequence[tuple[Text, QWidget]]) -> QFormLayout:
        form = QFormLayout()
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(12)
        for caption, value in rows:
            label = QLabel(parent)
            live(label.setText, caption)
            label.setObjectName("fieldCaption")
            form.addRow(label, value)
        return form

    # --- state -----------------------------------------------------------------------------

    @redo_on_switch
    def set_board(self, board: BoardDefinition | None, chip: ChipDefinition | None) -> None:
        self._board, self._chip = board, chip
        if board is None or chip is None:
            for label in (self.chip_title, self.core, self.flash, self.ram, self.target):
                label.setText(UNKNOWN)
            self.chip_title.setToolTip("")
        else:
            self.chip_title.setText(board_label(board))
            self.chip_title.setToolTip(board.display_name)
            self.core.setText(core_name(chip.core))
            self.flash.setText(f"{chip.flash_kb} KB")
            self.ram.setText(f"{chip.ram_kb} KB")
            self.target.setText(chip.target_triple)
        self._show_board_facts()
        self._show_connection()

    def set_probe(self, text: str | None) -> None:
        live(self.probe.setText, lambda: text or _("не вибрано"))

    def set_upload_method(self, method: UploadMethod | None) -> None:
        """Method of the device selected in the toolbar; None = nothing selected."""
        self._selected_method = method
        self._show_connection()

    def set_memory_usage(self, usage: MemoryUsage | None) -> None:
        if usage is None:
            self.flash_usage.set_usage(None)
            self.ram_usage.set_usage(None)
        else:
            self.flash_usage.set_usage(usage.flash_used, usage.flash_total)
            self.ram_usage.set_usage(usage.ram_used, usage.ram_total)

    def set_last_upload(self, text: Text | None) -> None:
        """E.g. "at 05:02 · 1,064 bytes · ST-Link" (see i18n.live); None hides the section."""
        live(self.last_upload.setText, text or "")
        for widget in (self.last_upload, self.last_upload_title, self._last_upload_separator):
            widget.setVisible(bool(text))

    @property
    def shown_method(self) -> UploadMethod | None:
        """The method whose wiring is displayed (selected device, else the board's default)."""
        if self._selected_method is not None:
            return self._selected_method
        return self._board.default_upload_method if self._board else None

    @property
    def wiring_rows(self) -> tuple[tuple[str, str], ...]:
        method = self.shown_method
        return wiring(method, self._chip) if method is not None else ()

    # --- internals -------------------------------------------------------------------------

    def _led(self) -> bool:
        return self._board is not None and self._board.led is not None

    def _on_board_button_pin(self) -> str:
        button = self._board.button if self._board else None
        return button.pin if button and button.on_board else ""

    @redo_on_switch
    def _show_board_facts(self) -> None:
        board = self._board
        led = board.led if board else None
        if led is None:
            self.led.setText(_("немає") if board else UNKNOWN)
        else:
            text = (
                _("{pin} · світить при низькому рівні")
                if led.active_low
                else _("{pin} · світить при високому рівні")
            )
            self.led.setText(text.format(pin=led.pin))
        button_pin = self._on_board_button_pin()
        self.button.setText(_("{pin} · на платі").format(pin=button_pin) if button_pin else "")
        self._board_form.setRowVisible(self.button, bool(button_pin))
        uart = board.uart if board else None
        self.uart.setText(
            f"{uart.peripheral} · TX {uart.tx_pin} · RX {uart.rx_pin}" if uart else UNKNOWN
        )
        for label, pin in (
            (self.led, led.pin if led else None),
            (self.button, button_pin),
        ):
            label.set_clickable(bool(pin))
            label.setToolTip(_("Клацніть, щоб скопіювати «{pin}»").format(pin=pin) if pin else "")

    @redo_on_switch
    def _show_connection(self) -> None:
        method = self.shown_method
        self.method.setText(_(METHOD_NAMES[method]) if method else UNKNOWN)
        self.method_note.setText(
            "" if self._selected_method is not None or method is None
            else _("Пристрій не вибрано — показано типовий спосіб для цієї плати.")
        )  # fmt: skip
        self.method_note.setVisible(bool(self.method_note.text()))

        for widget in self._wiring_widgets:
            self.wiring_grid.removeWidget(widget)
            widget.deleteLater()
        self._wiring_widgets = []
        parent = self.widget()
        for row, (adapter_pin, board_pin) in enumerate(self.wiring_rows):
            cells = (QLabel(adapter_pin, parent), QLabel("→", parent), QLabel(board_pin, parent))
            cells[0].setObjectName("wiringPin")
            cells[1].setObjectName("mutedText")
            for column, cell in enumerate(cells):
                self.wiring_grid.addWidget(cell, row, column)
            self._wiring_widgets.extend(cells)

        if method is UploadMethod.PROBE_RS:
            note = _("Перемичка BOOT0 = 0.")
        elif method is UploadMethod.SERIAL_BOOTLOADER:
            note = _("Перед прошиванням: BOOT0 = 1 і Reset. Після — поверніть BOOT0 = 0.")
        elif method is UploadMethod.USB_DFU:
            note = _("Перед прошиванням: затисніть BOOT0, натисніть Reset, відпустіть BOOT0.")
        else:
            note = ""
        self.wiring_note.setText(note)
        self.wiring_note.setVisible(bool(note))
        self.help_link.setVisible(method is not None)

    def _on_help_clicked(self) -> None:
        method = self.shown_method
        if method is not None:
            self.help_requested.emit(HELP_TOPICS[method])

    def _copy_pin(self, pin: str) -> None:
        if pin:
            QGuiApplication.clipboard().setText(pin)
            self.pin_copied.emit(pin)
