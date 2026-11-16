"""Upload targets in the connection menu: ST-Link probes (probe-rs), boards in USB DFU mode
(dfu-util) and USB-serial adapters.

Selection rules: a device the project remembers is selected whenever it is
connected, and nothing else replaces it; only a project with no remembered device gets the single
connected device automatically. The user's choice is remembered per project — including
"Disconnect", after which nothing is selected automatically until the user picks a device.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from nightexpress.i18n import _
from nightexpress.models.entities import (
    DfuDeviceInfo,
    ProbeInfo,
    ProjectConfig,
    SerialPortInfo,
    UploadMethod,
    UploadTarget,
)
from nightexpress.models.state import PortsModel, ProjectModel, Screen
from nightexpress.services.dfu_util import DfuService
from nightexpress.services.probe_rs import ProbeRsService
from nightexpress.services.serial_port_service import SerialPortService
from nightexpress.services.settings_store import SettingsStore
from nightexpress.views.main_window import MainWindow
from nightexpress.views.toast import ToastKind

REFRESH_INTERVAL_MS = 3000


def connection_name(target: UploadTarget | None) -> str | None:
    if target is None:
        return None
    if isinstance(target, ProbeInfo):
        return "ST-Link" if "link" in target.name.lower() else target.name
    if isinstance(target, DfuDeviceInfo):
        return "USB DFU"
    return target.adapter_kind.display_name


def upload_method(target: UploadTarget) -> UploadMethod:
    if isinstance(target, ProbeInfo):
        return UploadMethod.PROBE_RS
    if isinstance(target, DfuDeviceInfo):
        return UploadMethod.USB_DFU
    return UploadMethod.SERIAL_BOOTLOADER


def distinct_targets(targets: Iterable[UploadTarget]) -> list[UploadTarget]:
    """The same devices with unique stable ids, as the model requires.

    Two clone adapters without a serial number share one USB identity: they are told apart by
    their port names. Whatever still collides (e.g. two probes without a serial number) cannot
    be addressed separately, so only the first one is kept.
    """
    items = list(targets)
    ids = [t.stable_id for t in items]
    result: list[UploadTarget] = []
    seen: set[str] = set()
    for target in items:
        if isinstance(target, SerialPortInfo) and ids.count(target.stable_id) > 1:
            target = replace(target, shares_usb_identity=True)
        if target.stable_id not in seen:
            seen.add(target.stable_id)
            result.append(target)
    return result


class PortsController(QObject):
    def __init__(
        self,
        window: MainWindow,
        ports_model: PortsModel,
        project_model: ProjectModel,
        probe_rs: ProbeRsService,
        serial: SerialPortService,
        settings: SettingsStore,
        probe_command: Callable[[], Sequence[str] | None],
        is_busy: Callable[[], bool] = lambda: False,
        refresh_interval_ms: int = REFRESH_INTERVAL_MS,
        dfu: DfuService | None = None,
        dfu_command: Callable[[], Sequence[str] | None] = lambda: None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._ports = ports_model
        self._project = project_model
        self._probe_rs = probe_rs
        self._serial = serial
        self._settings = settings
        self._probe_command = probe_command
        self._is_busy = is_busy
        self._dfu = dfu
        self._dfu_command = dfu_command
        self._serial_ports: list[SerialPortInfo] = []
        self._dfu_devices: list[DfuDeviceInfo] = []
        self._known_ids: set[str] | None = None  # None until the first listing
        self._project_path: Path | None = None  # whose device is selected now
        self._announced: set[str] = set()  # devices already offered in this session

        probe_rs.probes_listed.connect(self._on_probes_listed)
        if dfu is not None:
            dfu.devices_listed.connect(self._on_dfu_listed)
        window.toolbar.ports_refresh_requested.connect(self.refresh)
        self._detached_without_project = False
        window.toolbar.port_selected.connect(self.select)
        window.toolbar.rename_port_requested.connect(self.rename)
        ports_model.selection_changed.connect(self._on_selection_changed)
        project_model.config_changed.connect(self._on_project_changed)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if refresh_interval_ms > 0:
            self._timer.start(refresh_interval_ms)

    # --- refresh ---------------------------------------------------------------------------

    def refresh(self) -> None:
        self._serial_ports = self._serial.list_ports()
        command = self._dfu_command() if self._dfu is not None else None
        if self._dfu is not None and command:
            self._dfu.set_command(command)
            # The probes are listed once the DFU listing (this one or a running one) is in.
            self._dfu.list_devices()
            return
        self._dfu_devices = []
        self._list_probes()

    def _on_dfu_listed(self, devices: list[DfuDeviceInfo]) -> None:
        self._dfu_devices = list(devices)
        self._list_probes()

    def _list_probes(self) -> None:
        command = self._probe_command()
        if command:
            self._probe_rs.set_command(command)
            # The result arrives via probes_listed (also when a listing was already running).
            self._probe_rs.list_probes()
        else:
            self._on_probes_listed([])

    def _tick(self) -> None:
        if (
            self._window.isVisible()
            and self._window.current_screen is Screen.EDITOR
            and not self._is_busy()
        ):
            self.refresh()

    def _on_probes_listed(self, probes: list[ProbeInfo]) -> None:
        targets = distinct_targets([*probes, *self._dfu_devices, *self._serial_ports])
        self._ports.set_targets(targets)
        self._auto_select()
        self._announce_new(targets)
        labels = {
            t.stable_id: label
            for t in targets
            if (label := self._settings.adapter_label(t.stable_id))
        }
        self._window.toolbar.set_ports(targets, labels)
        self._window.toolbar.set_current_port(
            self._ports.selected.stable_id if self._ports.selected else None
        )

    # --- selection -------------------------------------------------------------------------

    def _auto_select(self) -> None:
        if self._ports.selected is not None or self._detached():
            return
        config = self._project.config
        remembered = self._settings.project_upload_target(config.path) if config else None
        if remembered:
            # Only ever the remembered device: if it is unplugged, never substitute another one
            # (the remaining adapter may be wired to a completely different device).
            if self._ports.find(remembered):
                self._ports.select(remembered)
        elif len(self._ports.targets) == 1:
            self._ports.select(self._ports.targets[0].stable_id)

    def _announce_new(self, targets: Sequence[UploadTarget]) -> None:
        """Offer devices plugged in while nothing is selected; the choice stays the user's.

        Nothing is selected or opened here — the toast only saves a trip
        to the menu.
        """
        ids = {t.stable_id for t in targets}
        first_listing, known = self._known_ids is None, self._known_ids or set()
        self._known_ids = ids
        self._announced &= ids  # an unplugged device may be offered again later
        if first_listing or self._ports.selected is not None or self._project.config is None:
            return
        fresh = [t for t in targets if t.stable_id not in known | self._announced]
        if not fresh:
            return
        self._announced |= {t.stable_id for t in fresh}
        if len(fresh) == 1:
            target = fresh[0]
            self._window.notify(
                _("Знайдено: {device}").format(device=self._window.toolbar.menu_text(target)),
                _("Використати цей пристрій для прошивання?"),
                ToastKind.INFO,
                _("Використати"),
                lambda: self.select(target.stable_id),
            )
        else:
            self._window.notify(
                _("Знайдено пристроїв: {count}").format(count=len(fresh)),
                _("Виберіть, через який прошивати плату."),
                ToastKind.INFO,
                _("Вибрати пристрій"),
                self._window.show_device_menu,
            )

    def select(self, stable_id: str | None) -> None:
        """The user picked a device in the connection menu (or "Disconnect")."""
        if stable_id is not None and self._ports.find(stable_id) is None:
            self.refresh()
            return
        self._ports.select(stable_id)
        self._detached_without_project = stable_id is None
        config = self._project.config
        if config is not None:
            self._settings.set_project_upload_target(config.path, stable_id)
            self._settings.set_project_device_detached(config.path, stable_id is None)

    def _detached(self) -> bool:
        """Did the user disconnect on purpose? Then the single adapter is not re-selected."""
        config = self._project.config
        if config is None:
            return self._detached_without_project
        return self._settings.project_device_detached(config.path)

    def rename(self, stable_id: str) -> None:
        """Let the user name an adapter, e.g. "to STM32" or "modem"."""
        current = self._settings.adapter_label(stable_id) or ""
        label = self._window.ask_text(_("Назва пристрою"), _("Як називати цей пристрій:"), current)
        if label is None:
            return
        self._settings.set_adapter_label(stable_id, label)
        self._on_probes_listed([t for t in self._ports.targets if isinstance(t, ProbeInfo)])

    def _on_selection_changed(self, target: UploadTarget | None) -> None:
        method = upload_method(target) if target is not None else None
        self._window.set_connection(connection_name(target), method)
        self._window.toolbar.set_current_port(target.stable_id if target else None)

    def _on_project_changed(self, config: ProjectConfig | None) -> None:
        if config is None:
            return
        remembered = self._settings.project_upload_target(config.path)
        if remembered and self._ports.find(remembered):
            self._ports.select(remembered)
        elif config.path != self._project_path:
            # Another project: the previous project's device must not flash this one.
            self._ports.select(None)
        self._project_path = config.path
        self.refresh()
