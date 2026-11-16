"""The UART tab: connect to a USB-serial adapter, show what the board sends, send lines back.

The monitor port is chosen independently of the upload target (flash through ST-Link, read
through a USB-TTL adapter). Same selection rule as for uploads: a remembered port is never
replaced by another one; with nothing remembered only a single connected adapter is picked.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QObject

from nightexpress.i18n import N_, _
from nightexpress.models.entities import ProjectConfig, SerialPortInfo, UploadTarget
from nightexpress.models.state import LineEnding, PortsModel, ProjectModel, SerialMonitorModel
from nightexpress.services.file_service import FileService, FileServiceError
from nightexpress.services.serial_monitor_service import SerialMonitorService
from nightexpress.services.settings_store import SettingsStore
from nightexpress.views.main_window import MainWindow


class SerialMonitorController(QObject):
    def __init__(
        self,
        window: MainWindow,
        model: SerialMonitorModel,
        ports: PortsModel,
        project: ProjectModel,
        settings: SettingsStore,
        service: SerialMonitorService,
        files: FileService | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._model = model
        self._ports = ports
        self._project = project
        self._settings = settings
        self._service = service
        self._files = files or FileService()
        self._now = now
        self._released: str | None = None  # device closed for an upload, to reopen afterwards

        panel = window.uart_panel
        panel.bind(model)
        model.set_baud_rate(settings.monitor_baud_rate())
        panel.connect_toggled.connect(self._on_connect_toggled)
        panel.port_selected.connect(self.select_port)
        panel.baud_selected.connect(self.set_baud_rate)
        panel.display_mode_selected.connect(model.set_display_mode)
        panel.line_ending_selected.connect(self.set_line_ending)
        panel.autoscroll.toggled.connect(model.set_autoscroll)
        panel.clear_requested.connect(model.clear)
        panel.send_requested.connect(self.send)
        panel.save_requested.connect(self.save_output)
        window.serial_monitor_requested.connect(self.show)
        service.data_received.connect(model.append_data)
        service.connection_lost.connect(self._on_connection_lost)
        ports.targets_changed.connect(self._on_targets_changed)
        project.config_changed.connect(self._on_project_changed)

    # --- ports -----------------------------------------------------------------------------

    def _serial_ports(self) -> tuple[SerialPortInfo, ...]:
        return self._ports.serial_ports

    def _on_targets_changed(self, _targets: tuple[UploadTarget, ...]) -> None:
        current = self._model.port
        ports = self._serial_ports()
        if current is not None and not self._model.is_open:
            # Follow the adapter if it reappeared under another device name.
            current = next((p for p in ports if p.stable_id == current.stable_id), None)
            self._model.set_port(current)
        if self._model.port is None:
            self._auto_select(ports)
        self._refresh_panel()

    def _auto_select(self, ports: tuple[SerialPortInfo, ...]) -> None:
        config = self._project.config
        remembered = self._settings.project_monitor_port(config.path) if config else None
        if remembered:
            self._model.set_port(next((p for p in ports if p.stable_id == remembered), None))
        elif len(ports) == 1:
            self._model.set_port(ports[0])

    def _refresh_panel(self) -> None:
        ports = self._serial_ports()
        labels = {
            p.stable_id: label
            for p in ports
            if (label := self._settings.adapter_label(p.stable_id))
        }
        current = self._model.port.stable_id if self._model.port else None
        self._window.uart_panel.set_ports(ports, current, labels)

    def select_port(self, stable_id: str | None) -> None:
        port = next((p for p in self._serial_ports() if p.stable_id == stable_id), None)
        self._model.set_port(port)
        config = self._project.config
        if config is not None:
            self._settings.set_project_monitor_port(config.path, port.stable_id if port else None)

    def _on_project_changed(self, config: ProjectConfig | None) -> None:
        if config is None:
            self.disconnect()
            return
        # Speed and line ending belong to the firmware, so they are remembered per project.
        baud = self._settings.project_monitor_baud_rate(config.path)
        self._model.set_baud_rate(baud or self._settings.monitor_baud_rate())
        ending = self._settings.project_monitor_line_ending(config.path)
        if ending in {e.value for e in LineEnding}:
            self._model.set_line_ending(LineEnding(ending))
        self._model.set_port(None)
        self._auto_select(self._serial_ports())
        self._refresh_panel()

    # --- connection ------------------------------------------------------------------------

    def show(self) -> None:
        """Menu "UART monitor": open the tab and connect if a port is chosen."""
        self._window.show_uart()
        if not self._model.is_open and self._model.port is not None:
            self.connect()

    def connect(self) -> bool:
        port = self._model.port
        if port is None:
            self._window.uart_panel.set_status(
                N_("Виберіть USB-TTL адаптер у списку ліворуч."), error=True
            )
            self._model.set_open(False)
            self._window.uart_panel.set_connected(False)
            return False
        try:
            self._service.open(port.device, self._model.baud_rate)
        except Exception as exc:  # pyserial: SerialException / OSError
            self._window.uart_panel.set_status(
                lambda error=exc: _("Не вдалося відкрити {label}: {error}").format(
                    label=port.label, error=error
                ),
                error=True,
            )
            self._model.set_open(False)
            self._window.uart_panel.set_connected(False)
            return False
        self._model.set_open(True)
        baud_rate = self._model.baud_rate
        self._window.uart_panel.set_status(
            lambda: _("Підключено: {label}, {baud_rate} 8N1").format(
                label=port.label, baud_rate=baud_rate
            )
        )
        return True

    def disconnect(self) -> None:
        self._service.close()
        self._released = None
        if self._model.is_open:
            self._model.set_open(False)
            self._window.uart_panel.set_status(N_("Відключено"))

    def _on_connect_toggled(self, connect: bool) -> None:
        if connect:
            self.connect()
        else:
            self.disconnect()

    def set_baud_rate(self, baud: int) -> None:
        self._model.set_baud_rate(baud)
        self._settings.set_monitor_baud_rate(baud)
        config = self._project.config
        if config is not None:
            self._settings.set_project_monitor_baud_rate(config.path, baud)
        if self._model.is_open:
            self._service.close()
            self.connect()

    def set_line_ending(self, ending: LineEnding) -> None:
        self._model.set_line_ending(ending)
        config = self._project.config
        if config is not None:
            self._settings.set_project_monitor_line_ending(config.path, ending.value)

    def save_output(self) -> bool:
        """Save what the monitor shows to a text file chosen by the user."""
        panel = self._window.uart_panel
        config = self._project.config
        name = f"uart-{self._now():%Y%m%d-%H%M%S}.log"
        start = (config.path if config is not None else self._settings.projects_directory()) / name
        path = self._window.ask_save_file(
            _("Зберегти вивід UART"), start, _("Журнал (*.log *.txt)")
        )
        if path is None:
            return False
        try:
            self._files.write_text_atomic(path, panel.text())
        except FileServiceError as exc:
            panel.set_status(str(exc), error=True)
            return False
        self._window.show_message(_("Вивід UART збережено: {name}").format(name=path.name))
        return True

    def send(self, text: str) -> None:
        data = text.encode("utf-8") + self._model.line_ending.bytes
        if not data:
            return
        try:
            self._service.write(data)
        except Exception as exc:  # pyserial: SerialException / OSError / RuntimeError
            self._window.uart_panel.set_status(
                lambda error=exc: _("Не вдалося надіслати: {error}").format(error=error),
                error=True,
            )

    def _on_connection_lost(self, reason: str) -> None:
        self._model.set_open(False)
        self._window.uart_panel.set_status(
            lambda: _("Адаптер відключено ({reason})").format(reason=reason), error=True
        )

    # --- coordination with uploads -----------------------------------------------------------

    def release(self, device: str) -> bool:
        """Free ``device`` for the bootloader; returns True if the monitor was using it."""
        if self._service.is_open and self._service.device == device:
            self._service.close()
            self._model.set_open(False)
            self._released = device
            self._window.uart_panel.set_status(N_("Порт звільнено на час прошивки…"))
            self._window.uart_panel.append_note(_("порт звільнено на час прошивання"))
            return True
        return False

    def restore(self) -> None:
        if self._released is not None:
            self._released = None
            if self.connect():
                self._window.uart_panel.append_note(_("перепідключено після прошивання"))
