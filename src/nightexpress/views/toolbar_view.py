"""Command row: project, board (chip), target, profile, Build, Build & Flash, connection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from nightexpress.i18n import N_, Text, _, live, on_change, redo_on_switch
from nightexpress.models.entities import (
    BoardDefinition,
    BuildProfile,
    DfuDeviceInfo,
    ProbeInfo,
    SerialPortInfo,
    UploadTarget,
)
from nightexpress.views.icons import LABEL_ICON_SIZE, icon, label_icon
from nightexpress.views.theme import palette
from nightexpress.views.widgets import ElidedComboBox

OPEN_OTHER = "__open_other__"
VERIFY, UPLOAD = "verify", "upload"
STOP_TEXT = N_("Зупинити")
COMMAND_TEXTS = {VERIFY: N_("Зібрати"), UPLOAD: N_("Зібрати й прошити")}
COMMAND_TOOLTIPS = {
    VERIFY: N_("Зібрати проєкт (⌘B / Ctrl+B)"),
    UPLOAD: N_("Зібрати й прошити плату (⌘U / Ctrl+U)"),
}
NO_DEVICE_TEXT = N_("Вибрати пристрій")
PROBES_HEADER = N_("Програматори")
ADAPTERS_HEADER = N_("USB-TTL адаптери")
DFU_HEADER = N_("Плати в режимі DFU (USB)")


def device_kind(target: UploadTarget) -> str:
    """What the button says is connected: "ST-Link", "USB DFU", "CH340"…"""
    if isinstance(target, ProbeInfo):
        return "ST-Link"
    if isinstance(target, DfuDeviceInfo):
        return "USB DFU"
    return target.adapter_kind.display_name


def port_tooltip(target: UploadTarget) -> str:
    if isinstance(target, SerialPortInfo):
        return target.device
    if isinstance(target, DfuDeviceInfo):
        return target.serial_number or target.path
    return ""


PROFILE_NAMES = {BuildProfile.DEV: "debug", BuildProfile.RELEASE: "release"}


def board_label(board: BoardDefinition) -> str:
    """The chip marking people see on the board, e.g. STM32F103C6T6A."""
    return next((a for a in board.aliases if a.upper().startswith("STM32")), board.chip_id)


class RightAlignedMenu(QMenu):
    """A button menu that opens leftwards from the button's right edge.

    Qt aligns a button menu with the button's left edge; for a button at the right end of the
    toolbar that puts the menu past the window's edge.
    """

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        anchor = self.parentWidget()
        if anchor is None:
            return
        right = anchor.mapToGlobal(QPoint(anchor.width(), 0)).x()
        window_left = anchor.window().mapToGlobal(QPoint(0, 0)).x()
        self.move(max(window_left, right - self.width()), self.y())


TARGET_MAX_WIDTH = 200
TARGET_CHROME_WIDTH = 46  # the target field around its text: icon, gap and paddings


class ToolbarView(QWidget):
    verify_requested = Signal()  # "Build"
    upload_requested = Signal()  # "Build and flash"
    stop_requested = Signal()  # the running command's button turns into "Stop"
    project_selected = Signal(object)  # Path
    open_project_requested = Signal()
    board_selected = Signal(str)
    profile_selected = Signal(object)  # BuildProfile
    port_selected = Signal(object)  # stable id or None
    rename_port_requested = Signal(str)  # stable id
    ports_refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolbarView")
        self._updating = False

        self.project_combo = self._combo(160, N_("Проєкт"))
        self.board_combo = self._combo(190, N_("Плата (чип)"))
        self.target_combo = self._combo(TARGET_MAX_WIDTH, N_("Ціль компіляції: визначається чипом"))
        self.target_combo.setProperty("readOnly", True)  # one option; styled as plain info
        self.target_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # nothing to choose with keys
        self.profile_combo = self._combo(112, N_("Профіль збірки"))
        for profile in (BuildProfile.DEV, BuildProfile.RELEASE):
            self.profile_combo.addItem(
                icon("layers", palette.TEXT_MUTED), PROFILE_NAMES[profile], profile
            )

        self.verify_button = QPushButton(self)
        self.verify_button.setObjectName("buildButton")
        self.verify_button.setProperty("buttonRole", "accent")
        self.upload_button = QPushButton(self)
        self.upload_button.setObjectName("flashButton")
        self.upload_button.setProperty("buttonRole", "primary")
        for button in (self.verify_button, self.upload_button):
            button.setIconSize(LABEL_ICON_SIZE)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.verify_button.clicked.connect(lambda: self._on_command_clicked(VERIFY))
        self.upload_button.clicked.connect(lambda: self._on_command_clicked(UPLOAD))
        self._running: str | None = None
        self._show_running()

        self.connection_button = QToolButton(self)
        self.connection_button.setObjectName("connectionButton")
        self.connection_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.connection_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.connection_button.setIconSize(LABEL_ICON_SIZE)
        live(
            self.connection_button.setToolTip, N_("Вибрати програматор або адаптер для прошивання")
        )
        self.connection_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.port_menu = RightAlignedMenu(self.connection_button)
        self.port_menu.aboutToShow.connect(self.ports_refresh_requested)
        self.connection_button.setMenu(self.port_menu)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 8)
        layout.setSpacing(8)
        for combo in (self.project_combo, self.board_combo, self.target_combo, self.profile_combo):
            layout.addWidget(combo)
        layout.addStretch(1)
        layout.addWidget(self.verify_button)
        layout.addWidget(self.upload_button)
        layout.addWidget(self.connection_button)

        self.project_combo.activated.connect(self._on_project_activated)
        self.board_combo.activated.connect(self._on_board_activated)
        self.profile_combo.activated.connect(self._on_profile_activated)
        self._targets: list[UploadTarget] = []
        self._labels: Mapping[str, str] = {}
        self._selected_port: str | None = None
        self.set_ports([])
        self.set_profile(BuildProfile.RELEASE)
        # Registered last: the widths are measured after the texts above are redone.
        on_change(self, self._fit_after_switch, "fit")

    def _combo(self, width: int, tooltip: Text) -> QComboBox:
        combo = ElidedComboBox(self)
        combo.setFixedWidth(width)
        combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        def describe() -> None:
            what = _(tooltip) if isinstance(tooltip, str) else tooltip()
            text = combo.currentText()
            combo.setToolTip(f"{what}\n{text}" if text else what)
            combo.setAccessibleName(what)

        combo.currentTextChanged.connect(describe)
        on_change(combo, describe, "tooltip")
        combo.setIconSize(QSize(16, 16))
        return combo

    # --- projects --------------------------------------------------------------------------

    @redo_on_switch
    def set_projects(self, projects: Iterable[tuple[Path, str]], current: Path | None) -> None:
        with self._quiet():
            self.project_combo.clear()
            folder = icon("folder", palette.TEXT_MUTED)
            for path, name in projects:
                self.project_combo.addItem(folder, name, path)
                self.project_combo.setItemData(
                    self.project_combo.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole
                )
            self.project_combo.addItem(
                icon("folder-open", palette.TEXT_MUTED), _("Відкрити…"), OPEN_OTHER
            )
            index = self.project_combo.findData(current) if current is not None else -1
            self.project_combo.setCurrentIndex(max(index, 0))

    @property
    def current_project(self) -> Path | None:
        data = self.project_combo.currentData()
        return data if isinstance(data, Path) else None

    def _on_project_activated(self, index: int) -> None:
        data = self.project_combo.itemData(index)
        if data == OPEN_OTHER:
            self.open_project_requested.emit()
        elif isinstance(data, Path):
            self.project_selected.emit(data)

    # --- board / target / profile ----------------------------------------------------------

    @redo_on_switch
    def set_boards(self, boards: Iterable[BoardDefinition]) -> None:
        current = self.current_board_id
        with self._quiet():
            self.board_combo.clear()
            chip = icon("cpu", palette.TEXT_MUTED)
            for board in boards:
                self.board_combo.addItem(chip, board_label(board), board.id)
                self.board_combo.setItemData(
                    self.board_combo.count() - 1, board.display_name, Qt.ItemDataRole.ToolTipRole
                )
            self._select(self.board_combo, current)

    def set_current_board(self, board_id: str | None) -> None:
        with self._quiet():
            self._select(self.board_combo, board_id)

    @property
    def current_board_id(self) -> str | None:
        return self.board_combo.currentData()

    def set_targets(self, targets: Iterable[str], current: str | None = None) -> None:
        """Targets the chip supports (normally exactly one), so a wrong one cannot be picked."""
        with self._quiet():
            self.target_combo.clear()
            for target in targets:
                self.target_combo.addItem(icon("crosshair", palette.TEXT_MUTED), target, target)
            self._select(self.target_combo, current)
        self._fit_target_width()

    def _fit_target_width(self) -> None:
        """The target is plain text: as wide as its name, so the row has no hole after it."""
        combo = self.target_combo
        combo.ensurePolished()
        text_width = combo.fontMetrics().horizontalAdvance(combo.currentText())
        combo.setFixedWidth(min(TARGET_MAX_WIDTH, TARGET_CHROME_WIDTH + text_width))

    def set_profile(self, profile: BuildProfile) -> None:
        with self._quiet():
            self._select(self.profile_combo, profile)

    @property
    def current_profile(self) -> BuildProfile:
        return self.profile_combo.currentData() or BuildProfile.RELEASE

    def _on_board_activated(self, _index: int) -> None:
        if not self._updating and self.current_board_id is not None:
            self.board_selected.emit(self.current_board_id)

    def _on_profile_activated(self, _index: int) -> None:
        if not self._updating:
            self.profile_selected.emit(self.current_profile)

    # --- connection / ports ----------------------------------------------------------------

    def set_ports(
        self, targets: Iterable[UploadTarget], labels: Mapping[str, str] | None = None
    ) -> None:
        self._targets = list(targets)
        self._labels = labels or {}
        if self._selected_port and not any(
            t.stable_id == self._selected_port for t in self._targets
        ):
            self._selected_port = None
        self._rebuild_port_menu()

    def set_current_port(self, stable_id: str | None) -> None:
        self._selected_port = stable_id
        self._rebuild_port_menu()

    @property
    def current_port_id(self) -> str | None:
        return self._selected_port

    def port_text(self, target: UploadTarget) -> str:
        several_probes = sum(isinstance(t, ProbeInfo) for t in self._targets) > 1
        text = target.label
        if several_probes and isinstance(target, ProbeInfo) and target.serial_number:
            text += f" · {target.serial_number[-6:]}"  # only needed to tell two ST-Links apart
        if target.stable_id in self._labels:
            text += f" — {self._labels[target.stable_id]}"
        return text

    def menu_text(self, target: UploadTarget) -> str:
        """Menu row: what the device is, the user's name for it, then the port."""
        if not isinstance(target, SerialPortInfo):
            return self.port_text(target)
        text = target.adapter_kind.display_name
        if target.stable_id in self._labels:
            text += f" «{self._labels[target.stable_id]}»"
        short = target.device.removeprefix("/dev/cu.").removeprefix("/dev/")
        return f"{text} · {short}"

    def show_port_menu(self) -> None:
        """Open the device menu under the connection button (blocks until it closes)."""
        self.connection_button.showMenu()

    @redo_on_switch
    def _rebuild_port_menu(self) -> None:
        self.port_menu.clear()
        selected = next((t for t in self._targets if t.stable_id == self._selected_port), None)
        if selected is None:
            self.connection_button.setIcon(label_icon("unlink", palette.TEXT_MUTED))
            self.connection_button.setText(_(NO_DEVICE_TEXT))
            self.connection_button.setProperty("connected", False)
        else:
            self.connection_button.setIcon(label_icon("link", palette.SUCCESS))
            kind = device_kind(selected)
            self.connection_button.setText(_("{device} підключено").format(device=kind))
            self.connection_button.setProperty("connected", True)
        self.connection_button.style().unpolish(self.connection_button)
        self.connection_button.style().polish(self.connection_button)
        self._update_target_visibility()

        if not self._targets:
            empty = self.port_menu.addAction(_("Пристроїв не знайдено"))
            empty.setEnabled(False)
        groups = (
            (PROBES_HEADER, [t for t in self._targets if isinstance(t, ProbeInfo)]),
            (DFU_HEADER, [t for t in self._targets if isinstance(t, DfuDeviceInfo)]),
            (ADAPTERS_HEADER, [t for t in self._targets if isinstance(t, SerialPortInfo)]),
        )
        for header, targets in groups:
            if not targets:
                continue
            title = self.port_menu.addAction(_(header))
            title.setEnabled(False)
            title.setProperty("header", True)
            for target in targets:
                action = self.port_menu.addAction(self.menu_text(target))
                action.setToolTip(port_tooltip(target))
                action.setCheckable(True)
                action.setChecked(target.stable_id == self._selected_port)
                action.setData(target.stable_id)
                action.triggered.connect(
                    lambda _=False, sid=target.stable_id: self._choose_port(sid)
                )
        if self._selected_port is not None:
            self.port_menu.addSeparator()
            selected_id = self._selected_port
            self.port_menu.addAction(
                _("Назвати пристрій…"), lambda: self.rename_port_requested.emit(selected_id)
            )
            self.port_menu.addAction(_("Від'єднати"), lambda: self._choose_port(None))

    def _choose_port(self, stable_id: str | None) -> None:
        self._selected_port = stable_id
        self._rebuild_port_menu()
        self.port_selected.emit(stable_id)

    # --- state -----------------------------------------------------------------------------

    def set_busy(self, busy: bool, operation: str | None = None) -> None:
        self._set_running(operation if busy else None)
        self.verify_button.setEnabled(not busy or self._running == VERIFY)
        self.upload_button.setEnabled(not busy or self._running == UPLOAD)
        for widget in (self.board_combo, self.profile_combo, self.project_combo):
            widget.setEnabled(not busy)

    @property
    def running(self) -> str | None:
        return self._running

    def _set_running(self, operation: str | None) -> None:
        """Turn the button of the running command (VERIFY / UPLOAD) into "Stop"."""
        if operation == self._running:
            return
        self._running = operation
        self._show_running()

    @redo_on_switch
    def _show_running(self) -> None:
        buttons = (
            (VERIFY, self.verify_button, "hammer", palette.ACCENT_BRIGHT),
            (UPLOAD, self.upload_button, "play", palette.ACCENT_TEXT),
        )
        for name, button, icon_name, color in buttons:
            stop = name == self._running
            button.setText(_(STOP_TEXT) if stop else _(COMMAND_TEXTS[name]))
            button.setIcon(
                label_icon("square", palette.ERROR) if stop else label_icon(icon_name, color)
            )
            button.setToolTip(
                _("Зупинити поточну операцію (⌘. / Ctrl+.)") if stop else _(COMMAND_TOOLTIPS[name])
            )
            button.setProperty("running", stop)
            button.style().unpolish(button)
            button.style().polish(button)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._reserve_stop_width()
        self.connection_button.setFixedHeight(self.upload_button.sizeHint().height())
        self._update_target_visibility()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_target_visibility()

    def _update_target_visibility(self) -> None:
        # Count the target even while hidden so revealing it cannot squeeze the row.
        layout = self.layout()
        widgets = (
            self.project_combo,
            self.board_combo,
            self.target_combo,
            self.profile_combo,
            self.verify_button,
            self.upload_button,
            self.connection_button,
        )
        margins = layout.contentsMargins()
        required = margins.left() + margins.right() + layout.spacing() * (len(widgets) - 1)
        required += sum(
            min(widget.maximumWidth(), max(widget.minimumWidth(), widget.sizeHint().width()))
            for widget in widgets
        )
        hide_target = self.width() < required
        if self.target_combo.isHidden() != hide_target:
            self.target_combo.setHidden(hide_target)
            layout.activate()

    def _reserve_stop_width(self) -> None:
        """Make both command buttons wide enough for "Stop", so the row never shifts."""
        for name, button in ((VERIFY, self.verify_button), (UPLOAD, self.upload_button)):
            shown = button.text()
            button.ensurePolished()
            widths = []
            for text in (_(COMMAND_TEXTS[name]), _(STOP_TEXT)):
                button.setText(text)
                widths.append(button.sizeHint().width())
            button.setFixedWidth(max(widths))
            button.setText(shown)

    def _fit_after_switch(self) -> None:
        """The texts are in another language now: measure the buttons and the row again."""
        self._reserve_stop_width()
        self._fit_target_width()
        self._update_target_visibility()

    def _on_command_clicked(self, name: str) -> None:
        if self._running == name:
            self.stop_requested.emit()
        elif name == VERIFY:
            self.verify_requested.emit()
        else:
            self.upload_requested.emit()

    # --- internals -------------------------------------------------------------------------

    def _quiet(self) -> _Quiet:
        return _Quiet(self)

    @staticmethod
    def _select(combo: QComboBox, data: object) -> None:
        index = combo.findData(data) if data is not None else -1
        combo.setCurrentIndex(index if index >= 0 else 0 if combo.count() else -1)


class _Quiet:
    """Programmatic updates must never echo back as user intents."""

    def __init__(self, view: ToolbarView) -> None:
        self._view = view

    def __enter__(self) -> None:
        self._view._updating = True

    def __exit__(self, *_exc: object) -> None:
        self._view._updating = False
