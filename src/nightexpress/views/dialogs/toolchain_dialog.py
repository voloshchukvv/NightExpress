"""Status of the Rust/embedded tools with what to do about the missing ones.

For each tool: found or not, version, path and what it is needed for. A missing tool shows the
command to install it ("Copy the command") and a link to the instructions ("Instructions").
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, _, live, redo_on_switch
from nightexpress.models.entities import ToolStatus
from nightexpress.views.icons import LABEL_ICON_SIZE, label_icon
from nightexpress.views.theme import palette
from nightexpress.views.widgets import icon_label, set_icon, tool_button

KEY_CAP_HEIGHT = 22  # as on the shortcuts page

# What each tool is for, so a missing optional tool does not look like a breakage.
TOOL_PURPOSES = {
    "rustup": N_("Встановлює Rust і компоненти для STM32 (target, llvm-tools)."),
    "cargo": N_("Збирає проєкт — без нього не працює «Зібрати»."),
    "rustc": N_("Компілятор Rust."),
    "probe-rs": N_("Прошивання через ST-Link. Для прошивання через USB-TTL не потрібен."),
    "dfu-util": N_("Необов'язково: прошивання через USB плати (DFU), напр. Black Pill."),
    "rust-analyzer": N_("Необов'язково: підказки в редакторі (з'являться після MVP)."),
}


class ToolRow(QFrame):
    """One tool: status icon, name and version, purpose, and the fix when it is missing."""

    copy_requested = Signal(str)  # command
    open_url_requested = Signal(str)

    def __init__(
        self, status: ToolStatus, parent: QWidget | None = None, same_fix_as: str | None = None
    ) -> None:
        """``same_fix_as``: an earlier row with the same command (cargo comes with rustup)."""
        super().__init__(parent)
        # The look of a row on the shortcuts page; the version is its "key cap".
        self.setObjectName("shortcutRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.status = status
        if status.found:
            name, color = "circle-check", palette.SUCCESS
        elif status.required:
            name, color = "circle-x", palette.ERROR
        else:
            name, color = "circle-alert", palette.WARNING
        self.mark = icon_label(name, 22, color, self)

        title = f"<b>{status.name}</b>"
        if not status.found:
            if status.path is not None:
                state = _("не працює")
            else:
                state = _("не знайдено") if status.required else _("не встановлено")
            title += f"&nbsp;&nbsp;<span style='color:{color}'>{state}</span>"
        self.title = QLabel(title, self)
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.version = QLabel(status.version or "" if status.found else "", self)
        self.version.setObjectName("keyCap")
        self.version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version.setFixedHeight(KEY_CAP_HEIGHT)
        self.version.setVisible(bool(self.version.text()))
        self.purpose = QLabel(_(TOOL_PURPOSES.get(status.name, "")), self)
        self.purpose.setObjectName("mutedText")
        self.purpose.setWordWrap(True)

        heading = QHBoxLayout()
        heading.addWidget(self.title, 1)
        heading.addWidget(self.version)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addLayout(heading)
        text.addWidget(self.purpose)

        self.path = QLabel(str(status.path) if status.found and status.path else "", self)
        self.path.setObjectName("mutedText")
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path.setVisible(bool(self.path.text()))
        text.addWidget(self.path)

        # The fix: a broken tool explains what went wrong; a missing one just needs installing.
        self.hint = QLabel(status.install_hint if status.path is not None else "", self)
        self.hint.setWordWrap(True)
        self.hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.command = QLineEdit(status.install_command, self)
        self.command.setReadOnly(True)
        self.command.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.command.setCursorPosition(0)
        self.copy_button = QPushButton(label_icon("copy", palette.TEXT), "", self)
        live(self.copy_button.setText, N_("Скопіювати команду"))
        self.copy_button.setIconSize(LABEL_ICON_SIZE)
        self.copy_button.clicked.connect(lambda: self.copy_requested.emit(status.install_command))
        self.help_button = QPushButton(self)
        live(self.help_button.setText, N_("Інструкція  ↗"))
        self.help_button.setObjectName("linkButton")
        self.help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.help_button.setToolTip(status.help_url)
        self.help_button.clicked.connect(lambda: self.open_url_requested.emit(status.help_url))
        if status.found:
            for widget in (self.hint, self.command, self.copy_button, self.help_button):
                widget.hide()
        else:
            # Windows has an installer instead of a command: the hint says where to get it.
            if not status.install_command:
                self.hint.setText(status.install_hint)
            has_command = bool(status.install_command)
            if same_fix_as is not None and status.path is None:
                self.hint.setText(
                    _("Встановиться разом із {tool} — команда вище.").format(tool=same_fix_as)
                )
                has_command = False
            self.hint.setVisible(bool(self.hint.text()))
            self.command.setVisible(has_command)
            self.copy_button.setVisible(has_command)
            self.help_button.setVisible(bool(status.help_url) and same_fix_as is None)
            text.addWidget(self.hint)
            text.addWidget(self.command)
            actions = QHBoxLayout()
            actions.setSpacing(16)
            actions.addWidget(self.copy_button)
            actions.addWidget(self.help_button)
            actions.addStretch(1)
            text.addLayout(actions)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 8, 9)
        layout.setSpacing(12)
        layout.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)


class ToolchainDialog(QDialog):
    """A sheet like the other help pages: the user copies a command, runs it in a terminal
    application and presses "Check again"."""

    recheck_requested = Signal()
    open_url_requested = Signal(str)
    command_copied = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        live(self.setWindowTitle, N_("Інструменти Rust"))
        self.resize(560, 660)

        # Opened as a sheet of the main window (no title bar on macOS): the title and the
        # close button are its own, as on the shortcuts page.
        title = QLabel(self)
        live(title.setText, N_("Інструменти Rust"))
        title.setObjectName("panelTitle")
        self.close_button = tool_button("x", N_("Закрити"), self)
        self.close_button.clicked.connect(self.reject)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.close_button)

        self.header_icon = icon_label("refresh-cw", 30, palette.TEXT_MUTED, self)
        self.header = QLabel(self)
        self.header.setObjectName("envTitle")
        self.summary = QLabel(self)
        self.summary.setObjectName("mutedText")
        self.summary.setWordWrap(True)
        header_text = QVBoxLayout()
        header_text.setSpacing(4)
        header_text.addWidget(self.header)
        header_text.addWidget(self.summary)
        top = QHBoxLayout()
        top.setContentsMargins(10, 0, 10, 0)
        top.setSpacing(14)
        top.addWidget(self.header_icon, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(header_text, 1)

        self.rows: dict[str, ToolRow] = {}
        self._statuses: list[ToolStatus] | None = None
        self._rows_host = QWidget(self)
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_host.setObjectName("shortcutsList")
        self._rows_layout.setContentsMargins(0, 0, 6, 0)
        self._rows_layout.setSpacing(0)
        group = QLabel(self._rows_host)
        live(group.setText, N_("Інструменти"))
        group.setObjectName("shortcutGroup")
        group.setIndent(0)  # A styled frame adds an implicit text indent by default.
        self._rows_layout.addWidget(group)
        self._rows_layout.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._rows_host)
        scroll.viewport().setAutoFillBackground(False)
        self._rows_host.setAutoFillBackground(False)

        self.recheck_button = QPushButton(label_icon("refresh-cw", palette.TEXT), "", self)
        live(self.recheck_button.setText, N_("Перевірити знову"))
        self.recheck_button.setIconSize(LABEL_ICON_SIZE)
        self.recheck_button.setProperty("buttonRole", "accent")
        self.recheck_button.clicked.connect(self.recheck_requested)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(10, 0, 6, 0)
        buttons.addWidget(self.recheck_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 14, 16)
        layout.setSpacing(12)
        layout.addLayout(head)
        layout.addLayout(top)
        layout.addWidget(scroll, 1)
        layout.addLayout(buttons)
        # Otherwise the close button would open with a keyboard focus outline.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()
        self.set_checking(True)

    @redo_on_switch
    def set_checking(self, checking: bool) -> None:
        self.recheck_button.setEnabled(not checking)
        if checking:
            set_icon(self.header_icon, "refresh-cw", 30, palette.TEXT_MUTED)
            self.header.setText(_("Перевіряю інструменти…"))
            self.summary.setText(_("Це займає кілька секунд."))

    def refresh_theme(self) -> None:
        if self._statuses is not None:  # the rows carry colours inside their text
            self.set_statuses(self._statuses)

    @redo_on_switch
    def set_statuses(self, statuses: Iterable[ToolStatus]) -> None:
        items = self._statuses = list(statuses)
        for row in self.rows.values():
            row.deleteLater()
        self.rows = {}
        first_with_command: dict[str, str] = {}  # command -> the tool whose row shows it
        for index, status in enumerate(items):
            command = status.install_command if not status.found else ""
            same_fix_as = first_with_command.get(command) if command else None
            if command and same_fix_as is None:
                first_with_command[command] = status.name
            row = ToolRow(status, self._rows_host, same_fix_as)
            row.copy_requested.connect(self._copy)
            row.open_url_requested.connect(self.open_url_requested)
            self._rows_layout.insertWidget(index + 1, row)  # after the group title
            row.show()  # the layout would only show it on the next event loop pass
            self.rows[status.name] = row

        blocking = [s.name for s in items if s.is_blocking]
        optional = [s.name for s in items if not s.found and not s.required]
        if blocking:
            set_icon(self.header_icon, "circle-alert", 30, palette.ERROR)
            self.header.setText(_("Потрібне налаштування"))
            self.summary.setText(
                _(
                    "Без {tools} проєкт не збереться. Скопіюйте команду, виконайте "
                    "її в терміналі й натисніть «Перевірити знову»."
                ).format(tools=", ".join(blocking))
            )
        else:
            set_icon(self.header_icon, "circle-check", 30, palette.SUCCESS)
            self.header.setText(_("Середовище готове"))
            self.summary.setText(
                _("Можна збирати й прошивати. Необов'язкові: {tools}.").format(
                    tools=", ".join(optional)
                )
                if optional
                else _("Усі інструменти на місці.")
            )
        self.set_checking(False)

    def _copy(self, command: str) -> None:
        QGuiApplication.clipboard().setText(command)
        self.command_copied.emit(command)
