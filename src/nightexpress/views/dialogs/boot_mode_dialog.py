"""Ask the user to put the board into bootloader mode before a UART upload."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, _, live
from nightexpress.views.theme import palette


class BootModeDialog(QDialog):
    def __init__(
        self, port_text: str, tx_pin: str, rx_pin: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        live(self.setWindowTitle, N_("Режим бутлоадера"))
        self.setMinimumWidth(520)
        title = QLabel(_("Переведіть плату в режим бутлоадера"), self)
        title.setObjectName("sectionTitle")
        # The port is shown explicitly: other devices may be attached to other adapters.
        port = QLabel(
            _("Прошивка піде через порт: {port}").format(port=f"<b>{port_text}</b>"), self
        )
        port.setTextFormat(Qt.TextFormat.RichText)
        # The markup stays here; translators get the sentences.
        actions = (
            _("Поставте перемичку <b>BOOT0 = 1</b> (BOOT1 = 0)."),
            _("Натисніть кнопку <b>Reset</b> на платі."),
            _("Натисніть «Прошити»."),
        )
        wiring = _(
            "Підключення: TX адаптера → {rx_pin}, RX адаптера → {tx_pin}, спільний GND, "
            "логіка 3.3 V. Після прошивки поверніть BOOT0 = 0 і натисніть Reset — тоді "
            "запуститься програма."
        ).format(rx_pin=rx_pin, tx_pin=tx_pin)
        steps = QLabel(
            "<ol style='margin-left:-20px'>"
            + "".join(f"<li>{action}</li>" for action in actions)
            + f"</ol><p style='color:{palette.TEXT_MUTED}'>{wiring}</p>",
            self,
        )
        steps.setTextFormat(Qt.TextFormat.RichText)
        steps.setWordWrap(True)
        self.dont_show = QCheckBox(self)
        live(self.dont_show.setText, N_("Більше не показувати цю підказку"))
        self.dont_show.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        buttons = QDialogButtonBox(self)
        self.flash_button = buttons.addButton(_("Прошити"), QDialogButtonBox.ButtonRole.AcceptRole)
        palette.set_button_role(self.flash_button, "primary")
        buttons.addButton(_("Скасувати"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        for widget in (title, port, steps, self.dont_show, buttons):
            layout.addWidget(widget)
