"""The "RTT" tab: what the program on the board logs through the debug probe (defmt over RTT).

A row with the connect button and the state, the log under it. The log is an OutputPanel, so
the tools of the bottom panel (search, wrap, copy, clear) work on it as on the build log.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from nightexpress.i18n import N_, Text, _, live
from nightexpress.views.icons import LABEL_ICON_SIZE, label_icon
from nightexpress.views.panels.output_panel import OutputPanel
from nightexpress.views.theme import palette
from nightexpress.views.widgets import ElidedLabel

IDLE_TEXT = N_(
    "Не підключено. Прошийте через ST-Link проєкт із defmt (приклад «RTT Hello») — "
    "вивід з'явиться тут."
)


class RttPanel(QWidget):
    connect_toggled = Signal(bool)  # the user wants to attach (True) or detach (False)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RttPanel")
        self.connect_button = QPushButton(self)
        self.connect_button.setObjectName("connectButton")  # styled like the UART one
        self.connect_button.setCheckable(True)
        self.connect_button.setMinimumWidth(124)
        self.connect_button.setIconSize(LABEL_ICON_SIZE)
        self.connect_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.status_dot = QLabel("●", self)
        self.status_dot.setObjectName("uartDot")
        self.status = ElidedLabel("", self)
        self.status.setObjectName("mutedText")
        self.output = OutputPanel(self)

        controls = QHBoxLayout()
        controls.setSpacing(7)
        controls.addWidget(self.connect_button)
        controls.addSpacing(10)
        controls.addWidget(self.status_dot)
        controls.addWidget(self.status, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addLayout(controls)
        layout.addWidget(self.output, 1)

        self.connect_button.toggled.connect(self.connect_toggled)
        self.set_connected(False)
        self.set_status(IDLE_TEXT)

    def set_connected(self, connected: bool) -> None:
        self.connect_button.blockSignals(True)
        self.connect_button.setChecked(connected)
        self.connect_button.blockSignals(False)
        live(self.connect_button.setText, lambda: _("Відключити") if connected else _("Підключити"))
        self.connect_button.setIcon(label_icon("unlink" if connected else "link", palette.TEXT))
        self.status_dot.setVisible(connected)

    def set_status(self, text: Text) -> None:
        """``text``: see i18n.live."""
        live(self.status.setText, text)
        live(self.status.setToolTip, text)
