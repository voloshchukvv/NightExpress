"""RttController: the "RTT" tab — the program's own log, read through the ST-Link after flashing.

After a verified flash through a probe (UploadController.probe_flashed) it attaches to the
program by itself when the program has an RTT control block (defmt-rtt), and shows the tab.
Before the next flash it steps aside: the probe can serve one program at a time (see
UploadController's ProbeUser).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject

from nightexpress.controllers.upload_controller import FlashedTarget
from nightexpress.i18n import N_, _
from nightexpress.models.state import ProjectModel, UploadModel
from nightexpress.services.probe_rs import ProbeRsError
from nightexpress.services.rtt_service import RttService, has_rtt
from nightexpress.views.main_window import RTT_TAB, MainWindow
from nightexpress.views.panels.rtt_panel import IDLE_TEXT

NO_RTT_TEXT = N_(
    "Програма не пише в RTT. Додайте до проєкту defmt і defmt-rtt — як у прикладі «RTT Hello»."
)


class RttController(QObject):
    def __init__(
        self,
        window: MainWindow,
        project_model: ProjectModel,
        upload_model: UploadModel,
        service: RttService,
        probe_command: Callable[[], Sequence[str] | None],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._panel = window.rtt_panel
        self._upload = upload_model
        self._service = service
        self._probe_command = probe_command
        self._target: FlashedTarget | None = None  # the last program flashed through a probe

        project_model.config_changed.connect(self._on_project_changed)
        self._panel.connect_toggled.connect(self._on_connect_toggled)
        service.line.connect(self._panel.output.append_line)
        service.started.connect(self._on_started)
        service.stopped.connect(self._on_stopped)

    @property
    def is_attached(self) -> bool:
        return self._service.is_running

    # --- the probe is shared with flashing --------------------------------------------------

    def release(self, then: Callable[[], None]) -> None:
        """Free the probe for flashing; ``then`` runs once it is free."""
        self._service.stop(then)

    def forget_program(self) -> None:
        """A new program goes to the board another way (USB-TTL, USB DFU): the ELF read so far
        no longer describes what runs, so stop reading and do not offer to attach to it."""
        self._target = None
        self._service.stop()
        self._panel.set_status(IDLE_TEXT)

    # --- commands --------------------------------------------------------------------------

    def attach(self) -> bool:
        """Read the output of the last program flashed through a probe."""
        target = self._target
        command = self._probe_command()
        if target is None or command is None or self._upload.state.is_busy:
            self._panel.set_connected(False)
            return False
        if self._service.is_running:
            return True
        self._panel.set_status(
            lambda: _("Підключаюся до {chip} через {probe}…").format(
                chip=target.chip, probe=target.probe_name
            )
        )
        self._service.attach(command, target.elf, target.chip, target.probe)
        return True

    def detach(self) -> None:
        self._service.stop()

    # --- events ----------------------------------------------------------------------------

    def on_flashed(self, target: FlashedTarget) -> None:
        """UploadController.probe_flashed: a new program runs on the board."""
        self._target = target
        if not has_rtt(target.elf):
            self._panel.set_status(NO_RTT_TEXT)
            return
        self._panel.output.clear()  # a new program: its output starts afresh
        if self.attach():
            self._window.show_bottom_tab(RTT_TAB)

    def _on_connect_toggled(self, connect: bool) -> None:
        if not connect:
            self.detach()
        elif self._target is None:
            self._panel.set_connected(False)
            self._panel.set_status(IDLE_TEXT)
        elif not has_rtt(self._target.elf):
            self._panel.set_connected(False)
            self._panel.set_status(NO_RTT_TEXT)
        else:
            self.attach()

    def _on_started(self) -> None:
        target = self._target
        self._panel.set_connected(True)
        if target is not None:
            self._panel.set_status(
                lambda: _("Підключено: {chip} через {probe}").format(
                    chip=target.chip, probe=target.probe_name
                )
            )

    def _on_stopped(self, error: ProbeRsError | None) -> None:
        self._panel.set_connected(False)
        if error is None:
            self._panel.set_status(N_("Відключено"))
            return
        hint = error.hint
        self._panel.set_status(lambda: _("RTT відключено: {reason}").format(reason=hint))
        if error.details:
            self._panel.output.append_line(error.details)

    def _on_project_changed(self, _config: object) -> None:
        # Another project: the program on the board is no longer the one in the editor.
        self._target = None
        self._service.stop()
        self._panel.output.clear()
        self._panel.set_status(IDLE_TEXT)
