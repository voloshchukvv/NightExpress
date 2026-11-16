""" "Build and flash": build, then flash and verify — over SWD (probe-rs: check the chip,
download, reset), through the UART bootloader or over USB DFU (dfu-util), depending on the
selected device."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from nightexpress.controllers.build_controller import BuildController
from nightexpress.controllers.ports_controller import connection_name
from nightexpress.i18n import N_, Text, _, resolve
from nightexpress.models.entities import (
    BuildSummary,
    ChipDefinition,
    DfuDeviceInfo,
    OperationState,
    ProbeInfo,
    SerialPortInfo,
    UploadMethod,
    UploadProgress,
    UploadStage,
)
from nightexpress.models.state import PortsModel, ProjectModel, UploadModel
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.cargo_service import BuildResult
from nightexpress.services.dfu_util import DfuError, DfuErrorKind, DfuService
from nightexpress.services.elf_tools import ElfToolsError, elf_to_bin
from nightexpress.services.probe_rs import (
    ChipCheck,
    DownloadResult,
    ProbeRsError,
    ProbeRsErrorKind,
    ProbeRsService,
    describe_dev_id,
)
from nightexpress.services.process_runner import ProcessOutcome
from nightexpress.services.serial_bootloader import BootloaderError, BootloaderErrorKind
from nightexpress.services.serial_flash_service import SerialFlashService
from nightexpress.services.settings_store import SettingsStore
from nightexpress.views.formatting import byte_count, decimal
from nightexpress.views.main_window import FLASH_TAB, MainWindow
from nightexpress.views.toast import ToastKind

# Short status line per failure (the hint with details goes to the log).
FAILURE_TITLES = {
    ProbeRsErrorKind.PROBE_NOT_FOUND: N_("Прошивання не вдалося: ST-Link не знайдено"),
    ProbeRsErrorKind.PROBE_UNAVAILABLE: N_("Прошивання не вдалося: ST-Link зайнятий"),
    ProbeRsErrorKind.TARGET_NOT_RESPONDING: N_("Прошивання не вдалося: чип не відповідає"),
    ProbeRsErrorKind.UNKNOWN_CHIP: N_("Прошивання не вдалося: probe-rs не знає чипа"),
    ProbeRsErrorKind.VERIFY_FAILED: N_("Прошивання не вдалося: перевірка не пройшла"),
    ProbeRsErrorKind.WRONG_CHIP: N_("Прошивання скасовано: підключено інший чип"),
    ProbeRsErrorKind.NOT_INSTALLED: N_("Прошивання не вдалося: probe-rs не встановлено"),
    ProbeRsErrorKind.CANCELLED: N_("Прошивання зупинено"),
    ProbeRsErrorKind.TIMEOUT: N_("Прошивання не вдалося: probe-rs не відповідає"),
    ProbeRsErrorKind.UNKNOWN: N_("Прошивання не вдалося"),
}


SERIAL_FAILURE_TITLES = {
    BootloaderErrorKind.PORT_UNAVAILABLE: N_("Прошивання не вдалося: порт недоступний"),
    BootloaderErrorKind.NO_RESPONSE: N_("Прошивання не вдалося: бутлоадер не відповідає"),
    BootloaderErrorKind.NACK: N_("Прошивання не вдалося: бутлоадер відхилив команду"),
    BootloaderErrorKind.PROTECTED: N_("Прошивання не вдалося: flash захищена"),
    BootloaderErrorKind.WRONG_CHIP: N_("Прошивання скасовано: підключено інший чип"),
    BootloaderErrorKind.TOO_LARGE: N_("Прошивання скасовано: прошивка завелика"),
    BootloaderErrorKind.VERIFY_FAILED: N_("Прошивання не вдалося: перевірка не пройшла"),
    BootloaderErrorKind.CANCELLED: N_("Прошивання зупинено"),
}


DFU_FAILURE_TITLES = {
    DfuErrorKind.NOT_INSTALLED: N_("Прошивання не вдалося: dfu-util не встановлено"),
    DfuErrorKind.NO_DEVICE: N_("Прошивання не вдалося: плату в режимі DFU не знайдено"),
    DfuErrorKind.ACCESS_DENIED: N_("Прошивання не вдалося: немає доступу до USB"),
    DfuErrorKind.VERIFY_FAILED: N_("Прошивання не вдалося: перевірка не пройшла"),
    DfuErrorKind.CANCELLED: N_("Прошивання зупинено"),
    DfuErrorKind.TIMEOUT: N_("Прошивання не вдалося: dfu-util не відповідає"),
    DfuErrorKind.UNKNOWN: N_("Прошивання не вдалося"),
}


STAGE_TEXTS = {
    UploadStage.WAITING_FOR_BOOT_MODE: N_("Чекаю на бутлоадер…"),
    UploadStage.CONNECTING: N_("Підключаюся до бутлоадера…"),
    UploadStage.CHECKING_CHIP: N_("Перевіряю чип…"),
    UploadStage.ERASING: N_("Стираю flash…"),
    UploadStage.PROGRAMMING: N_("Записую"),
    UploadStage.VERIFYING: N_("Перевіряю записане"),
    UploadStage.RESETTING: N_("Запускаю програму…"),
}
CANCEL_TIMEOUT_MS = 8000


class SerialPortUser(Protocol):
    """Something holding a serial port that must step aside during a UART upload."""

    def release(self, device: str) -> bool: ...

    def restore(self) -> None: ...


class ProbeUser(Protocol):
    """Something holding the debug probe (the RTT reader) that must step aside for flashing."""

    def release(self, then: Callable[[], None]) -> None: ...

    def forget_program(self) -> None:
        """The board gets a new program another way: what the reader knows is out of date."""


@dataclass(frozen=True)
class FlashedTarget:
    """What was just flashed through a probe, for whoever reads the board afterwards."""

    elf: Path
    chip: str  # probe-rs chip name
    probe: str  # probe-rs selector
    probe_name: str  # e.g. "STLink V2"


class UploadController(QObject):
    probe_flashed = Signal(object)  # FlashedTarget, after a verified flash through a probe

    def __init__(
        self,
        window: MainWindow,
        project_model: ProjectModel,
        upload_model: UploadModel,
        ports_model: PortsModel,
        catalog: BoardCatalog,
        builds: BuildController,
        probe_rs: ProbeRsService,
        probe_command: Callable[[], Sequence[str] | None],
        install_hint: str = "",
        serial_flash: SerialFlashService | None = None,
        settings: SettingsStore | None = None,
        serial_monitor: SerialPortUser | None = None,
        probe_user: ProbeUser | None = None,
        now: Callable[[], datetime] = datetime.now,
        dfu: DfuService | None = None,
        dfu_command: Callable[[], Sequence[str] | None] = lambda: None,
        dfu_install_hint: str = "",
        dfu_install_command: str = "",
    ) -> None:
        super().__init__(window)
        self._window = window
        self._project = project_model
        self._upload = upload_model
        self._ports = ports_model
        self._catalog = catalog
        self._builds = builds
        self._probe_rs = probe_rs
        self._probe_command = probe_command
        self._install_hint = install_hint
        self._serial = serial_flash
        self._settings = settings
        self._serial_target: SerialPortInfo | None = None
        self._dfu = dfu
        self._dfu_command = dfu_command
        self._dfu_install_hint = dfu_install_hint
        self._dfu_install_command = dfu_install_command
        self._dfu_target: DfuDeviceInfo | None = None
        self._monitor = serial_monitor
        self._probe_user = probe_user
        self._monitor_released = False
        self._waiting_for_build = False
        self._chip: ChipDefinition | None = None
        self._probe: ProbeInfo | None = None
        self._elf: Path | None = None
        self._started = time.monotonic()
        self._now = now

        window.flash_panel.bind(upload_model)
        window.upload_requested.connect(self.upload)
        window.stop_requested.connect(self._on_stop_requested)
        builds.build_finished.connect(self._on_build_finished)
        project_model.config_changed.connect(self._on_project_changed)
        probe_rs.log_line.connect(upload_model.append_log)
        probe_rs.chip_checked.connect(self._on_chip_checked)
        probe_rs.download_finished.connect(self._on_downloaded)
        upload_model.state_changed.connect(self._on_state_changed)
        upload_model.progress_changed.connect(self._on_progress)
        if serial_flash is not None:
            serial_flash.log_line.connect(upload_model.append_log)
            serial_flash.progress.connect(upload_model.set_progress)
            serial_flash.finished.connect(self._on_serial_finished)
        if dfu is not None:
            dfu.log_line.connect(upload_model.append_log)
            dfu.progress.connect(upload_model.set_progress)
            dfu.finished.connect(self._on_dfu_finished)

    @property
    def is_busy(self) -> bool:
        return self._upload.state.is_busy

    # --- commands --------------------------------------------------------------------------

    def upload(self) -> bool:
        if self.is_busy or self._builds.is_busy:
            return False
        config = self._project.config
        if config is None:
            self._window.show_error(
                _("Немає проєкту"), _("Спочатку створіть або відкрийте проєкт.")
            )
            return False
        try:
            board = self._catalog.get_board(config.board_id)
        except KeyError:
            self._window.show_error(
                _("Невідома плата"),
                _("Плата «{board_id}» невідома.").format(board_id=config.board_id),
            )
            return False
        target = self._ports.selected
        if target is None:
            if self._ports.targets:
                # Not an error: the next step is a choice, so offer it right away.
                self._window.show_message(_("Виберіть пристрій для прошивання"), 8000)
                self._window.show_device_menu()
            elif board.supports(UploadMethod.USB_DFU) and self._dfu_command() is None:
                self._suggest_dfu_util()
            elif board.supports(UploadMethod.USB_DFU):
                self._window.notify(
                    _("Пристрій не знайдено"),
                    _(
                        "Підключіть ST-Link або плату через USB у режимі DFU: затисніть BOOT0, "
                        "натисніть і відпустіть Reset, відпустіть BOOT0."
                    ),
                    ToastKind.WARNING,
                    _("Як прошити через USB"),
                    lambda: self._window.help_requested.emit("usb_dfu"),
                )
            else:
                self._window.notify(
                    _("Пристрій не знайдено"),
                    _(
                        "Підключіть ST-Link (або USB-TTL адаптер) до комп'ютера й плати — "
                        "він з'явиться в меню «Вибрати пристрій»."
                    ),
                    ToastKind.WARNING,
                    _("Як підключити ST-Link"),
                    lambda: self._window.help_requested.emit("stlink"),
                )
            return False
        chip = self._catalog.chip_for(board)
        self._dfu_target = None
        if isinstance(target, DfuDeviceInfo):
            if self._dfu is None or not board.supports(UploadMethod.USB_DFU):
                self._window.show_error(
                    "USB DFU",
                    _("Плата «{board}» не підтримує прошивку через USB DFU.").format(
                        board=board.display_name
                    ),
                )
                return False
            command = self._dfu_command()
            if not command:
                self._window.show_error(_("dfu-util не знайдено"), self._dfu_install_hint)
                return False
            self._dfu.set_command(command)
            self._serial_target, self._probe, self._dfu_target = None, None, target
        elif isinstance(target, SerialPortInfo):
            if (
                self._serial is None
                or not board.supports(UploadMethod.SERIAL_BOOTLOADER)
                or chip.bootloader is None
            ):
                self._window.show_error(
                    "USB-TTL",
                    _("Плата «{board}» не підтримує прошивку через UART.").format(
                        board=board.display_name
                    ),
                )
                return False
            self._serial_target, self._probe = target, None
        else:
            if not board.supports(UploadMethod.PROBE_RS):
                self._window.show_error(
                    "ST-Link",
                    _("Плата «{board}» не підтримує SWD.").format(board=board.display_name),
                )
                return False
            command = self._probe_command()
            if not command:
                self._window.show_error(_("probe-rs не знайдено"), self._install_hint)
                return False
            self._probe_rs.set_command(command)
            self._serial_target, self._probe = None, target

        self._chip = chip
        self._started = time.monotonic()
        self._waiting_for_build = True
        if not self._builds.verify():
            # Nothing started (e.g. a file could not be saved): the previous upload's log and
            # result stay on screen.
            self._waiting_for_build = False
            return False
        if self._waiting_for_build:  # not if the build has already ended inside verify()
            self._upload.reset_results()
            self._upload.set_state(OperationState.PREPARING)
        return True

    def _suggest_dfu_util(self) -> None:
        """No device, and a board in DFU mode cannot even be seen: dfu-util is missing."""
        command = self._dfu_install_command
        text = _(
            "Без dfu-util плату в режимі DFU не видно. Встановіть його або підключіть ST-Link."
        )
        if command:
            text += "\n" + command
            self._window.notify(
                _("Встановіть dfu-util"),
                text,
                ToastKind.WARNING,
                _("Скопіювати команду"),
                lambda: QGuiApplication.clipboard().setText(command),
            )
            return
        self._window.notify(  # Windows: an installer and a driver, no command
            _("Встановіть dfu-util"),
            text + "\n" + self._dfu_install_hint,
            ToastKind.WARNING,
            _("Як прошити через USB"),
            lambda: self._window.help_requested.emit("usb_dfu"),
        )

    def _on_stop_requested(self) -> None:
        # During the build phase the build controller hears the same Stop and cancels cargo;
        # doing it here as well would say "Stopping…" twice.
        if not self._waiting_for_build:
            self.cancel()

    def cancel(self) -> None:
        if self._waiting_for_build:
            self._builds.cancel()
        elif self._dfu is not None and self._dfu.is_busy:
            self._upload.append_log(_("Зупиняю…"))
            self._dfu.cancel()
        elif self._serial is not None and self._serial.is_busy:
            self._upload.append_log(_("Зупиняю…"))
            self._serial.cancel()
        elif self._probe_rs.is_busy:
            self._upload.append_log(_("Зупиняю…"))
            self._probe_rs.cancel()

    def _on_project_changed(self, _config: object) -> None:
        # The project was closed or replaced: its firmware must not keep going to the board.
        # During the build phase the build controller cancels cargo, which ends the upload too.
        if self.is_busy and not self._waiting_for_build:
            self.cancel()

    # --- pipeline --------------------------------------------------------------------------

    def _on_build_finished(self, result: BuildResult) -> None:
        if not self._waiting_for_build:
            return  # a plain "Build" run
        self._waiting_for_build = False
        if not self.is_busy:
            # The build ended inside verify() (cargo could not even start, e.g. the project
            # folder is gone), before upload() marked this run as started.
            self._upload.reset_results()
            self._upload.set_state(OperationState.PREPARING)
        if result.outcome is ProcessOutcome.CANCELLED:
            self._finish(False, N_("Прошивання зупинено"), OperationState.CANCELLED)
            return
        if result.success and result.elf_path is None:
            self._upload.append_log(
                _(
                    "Збірка не створила виконуваного файлу: у проєкті немає програми "
                    "(src/main.rs або [[bin]]), тож прошивати нічого."
                )
            )
            self._finish(
                False, N_("Прошивання скасовано: немає виконуваного файлу"), OperationState.FAILED
            )
            return
        if not result.success or result.elf_path is None:
            self._upload.append_log(_("Збірка не вдалася — прошивання скасовано. Див. «Проблеми»."))
            # The build controller has already reported the failure and opened its tab.
            self._finish(
                False,
                N_("Прошивання скасовано: помилка збірки"),
                OperationState.FAILED,
                report=False,
            )
            return
        self._elf = result.elf_path
        if self._dfu_target is not None:
            self._start_dfu(result.elf_path)
            return
        if self._serial_target is not None:
            self._start_serial(result.elf_path)
            return
        chip, probe = self._chip, self._probe
        assert chip is not None and probe is not None
        self._upload.set_state(OperationState.RUNNING)
        self._window.show_flash_log()
        self._upload.append_log(
            _("Програматор: {name} ({selector})").format(name=probe.name, selector=probe.selector)
        )
        self._upload.append_log(_("Перевіряю чип {chip} (DBGMCU_IDCODE)…").format(chip=chip.id))
        self._upload.set_progress(UploadProgress(UploadStage.CHECKING_CHIP))

        def check_chip() -> None:
            if self.is_busy:  # not cancelled while the probe was being freed
                self._probe_rs.check_chip(
                    chip.probe_rs_name, probe.selector, chip.idcode_address, chip.dev_id
                )

        if self._probe_user is not None:
            self._probe_user.release(check_chip)  # the RTT reader keeps the probe busy
        else:
            check_chip()

    def _on_chip_checked(self, check: ChipCheck) -> None:
        if not self.is_busy:
            return
        chip, probe = self._chip, self._probe
        assert chip is not None and probe is not None and self._elf is not None
        if check.error is not None:
            self._fail(check.error)
            return
        if not check.ok:
            found = describe_dev_id(check.dev_id) if check.dev_id else _("нічого (0)")
            self._fail(
                ProbeRsError(
                    ProbeRsErrorKind.WRONG_CHIP,
                    _(
                        "Проєкт зібрано для {chip} (DEV_ID 0x{dev_id:03X}), а підключено: {found}. "
                        "Перевірте плату або виберіть іншу плату для проєкту."
                    ).format(chip=chip.id, dev_id=chip.dev_id, found=found),
                )
            )
            return
        self._upload.append_log(
            _("Чип: {chip}, IDCODE 0x{idcode:08X} ✓").format(
                chip=describe_dev_id(chip.dev_id), idcode=check.idcode
            )
        )
        self._upload.append_log(_("Прошиваю й перевіряю записане (probe-rs download --verify)…"))
        self._upload.set_progress(UploadProgress(UploadStage.PROGRAMMING))
        self._probe_rs.download(self._elf, chip.probe_rs_name, probe.selector)

    def _on_downloaded(self, result: DownloadResult) -> None:
        if not self.is_busy:
            return
        if result.error is not None:
            self._fail(result.error)
            return
        seconds = decimal(time.monotonic() - self._started, 2)
        self._upload.append_log(_("Плату перезапущено — програма працює."))
        chip, probe, elf = self._chip, self._probe, self._elf
        self._finish(
            True,
            lambda: _("Прошивку завершено й перевірено ({seconds} с)").format(seconds=seconds),
            OperationState.SUCCEEDED,
        )
        if chip is not None and probe is not None and elf is not None:
            self.probe_flashed.emit(
                FlashedTarget(elf, chip.probe_rs_name, probe.selector, probe.name)
            )

    def _on_progress(self, progress: UploadProgress | None) -> None:
        if progress is None or not self.is_busy:
            return
        stage, percent = progress.stage, progress.percent

        def text() -> str:
            shown = _(STAGE_TEXTS[stage])
            return shown if percent is not None or shown.endswith("…") else shown + "…"

        self._window.show_activity(text, percent)

    # --- USB-TTL (UART bootloader) -----------------------------------------------------------

    def _start_serial(self, elf: Path) -> None:
        chip, target, serial = self._chip, self._serial_target, self._serial
        assert chip is not None and chip.bootloader is not None
        assert target is not None and serial is not None
        try:
            image = elf_to_bin(elf)
        except ElfToolsError as exc:
            self._upload.append_log(_("Не вдалося підготувати образ: {error}").format(error=exc))
            self._finish(False, N_("Прошивання не вдалося"), OperationState.FAILED)
            return
        port_text = self._window.toolbar.port_text(target)
        if self._settings is None or not self._settings.boot_dialog_hidden():
            asked = time.monotonic()
            accepted, hide = self._window.ask_boot_mode(
                port_text, chip.bootloader.tx_pin, chip.bootloader.rx_pin
            )
            self._started += time.monotonic() - asked  # reading the dialog is not flashing time
            if hide and self._settings is not None:
                self._settings.set_boot_dialog_hidden(True)
            if not accepted:
                self._finish(False, N_("Прошивання скасовано"), OperationState.CANCELLED)
                return
        self._upload.set_state(OperationState.RUNNING)
        self._window.show_flash_log()
        if self._probe_user is not None:
            self._probe_user.forget_program()
        if self._monitor is not None and self._monitor.release(target.device):
            self._monitor_released = True
            self._upload.append_log(_("Монітор UART тимчасово відключено від цього порту."))
        self._upload.append_log(
            _("Порт: {port_text} (UART-бутлоадер, 115200 8E1)").format(port_text=port_text)
        )
        self._upload.append_log(
            _("Образ: {count} байт з адреси 0x{address:08X}").format(
                count=len(image.data), address=image.address
            )
        )
        serial.flash(target.device, image, chip.dev_id, chip.flash_bytes)

    def _on_serial_finished(self, error: BootloaderError | None) -> None:
        if not self.is_busy:
            return
        if self._monitor_released and self._monitor is not None:
            self._monitor_released = False
            self._monitor.restore()
        if error is None:
            seconds = decimal(time.monotonic() - self._started, 2)
            # GO is sent, but on the F1 it does not reliably start the program (seen on
            # hardware 15.11.2026), so the user is asked for the Reset that always works.
            self._upload.append_log(
                _(
                    "Щоб запустити програму, поверніть BOOT0 = 0 і натисніть Reset. "
                    "Поки BOOT0 = 1, плата після Reset вмикає бутлоадер, а не програму."
                )
            )
            self._finish(
                True,
                lambda: _("Прошивку завершено й перевірено ({seconds} с)").format(seconds=seconds),
                OperationState.SUCCEEDED,
            )
            return
        self._upload.append_log(error.hint)
        if error.details and error.details != error.hint:
            self._upload.append_log(f"  {error.details}")
        cancelled = error.kind is BootloaderErrorKind.CANCELLED
        self._finish(
            False,
            SERIAL_FAILURE_TITLES[error.kind],
            OperationState.CANCELLED if cancelled else OperationState.FAILED,
        )

    # --- USB DFU (dfu-util) ----------------------------------------------------------------

    def _start_dfu(self, elf: Path) -> None:
        target, dfu = self._dfu_target, self._dfu
        assert target is not None and dfu is not None
        try:
            image = elf_to_bin(elf)
        except ElfToolsError as exc:
            self._upload.append_log(_("Не вдалося підготувати образ: {error}").format(error=exc))
            self._finish(False, N_("Прошивання не вдалося"), OperationState.FAILED)
            return
        self._upload.set_state(OperationState.RUNNING)
        self._window.show_flash_log()
        if self._probe_user is not None:
            self._probe_user.forget_program()
        self._upload.append_log(
            _("Пристрій: {device} {serial}")
            .format(device=target.label, serial=target.serial_number or target.path)
            .rstrip()
        )
        self._upload.set_progress(UploadProgress(UploadStage.ERASING))
        dfu.flash(target, image)

    def _on_dfu_finished(self, error: DfuError | None) -> None:
        if not self.is_busy:
            return
        if error is None:
            seconds = decimal(time.monotonic() - self._started, 2)
            self._upload.append_log(_("Записане збігається з прошивкою ✓"))
            self._upload.append_log(_("Плата вийшла з бутлоадера — програма працює."))
            self._finish(
                True,
                lambda: _("Прошивку завершено й перевірено ({seconds} с)").format(seconds=seconds),
                OperationState.SUCCEEDED,
            )
            return
        self._upload.append_log(error.hint)
        if error.details and error.details != error.hint:
            self._upload.append_log(f"  {error.details}")
        cancelled = error.kind is DfuErrorKind.CANCELLED
        self._finish(
            False,
            DFU_FAILURE_TITLES[error.kind],
            OperationState.CANCELLED if cancelled else OperationState.FAILED,
        )

    def _fail(self, error: ProbeRsError) -> None:
        self._upload.append_log(error.hint)
        if error.details:
            for line in error.details.splitlines():
                self._upload.append_log(f"  {line}")
        cancelled = error.kind is ProbeRsErrorKind.CANCELLED
        self._finish(
            False,
            FAILURE_TITLES[error.kind],
            OperationState.CANCELLED if cancelled else OperationState.FAILED,
        )

    def _finish(
        self, success: bool, message: Text, state: OperationState, report: bool = True
    ) -> None:
        """``message``: a marked text or a function (see i18n.live): the status bar keeps it."""
        self._upload.set_progress(None)
        self._upload.set_summary(BuildSummary(success, resolve(message)))
        self._upload.set_message(resolve(message))
        window = self._window
        if not report:
            pass
        elif state is OperationState.CANCELLED:
            window.show_result(message, None, timeout_ms=CANCEL_TIMEOUT_MS)
        elif success:
            elapsed = time.monotonic() - self._started
            window.show_result(
                lambda: _("Прошито за {seconds} с").format(seconds=decimal(elapsed, 1)),
                True,
                FLASH_TAB,
            )
            window.target_view.set_last_upload(self._last_upload_text())
            window.notify_tab(FLASH_TAB)
        else:
            window.show_result(message, False, FLASH_TAB)
            window.show_flash_log()  # a failure must not go unnoticed
        self._upload.set_state(state)

    def _last_upload_text(self) -> Callable[[], str]:
        """ "at 05:02 · 1,064 bytes · ST-Link", built again after a change of the language."""
        when = self._now()
        usage = self._builds.memory_usage
        device = connection_name(self._dfu_target or self._serial_target or self._probe)

        def text() -> str:
            parts = [when.strftime(_("о %H:%M"))]
            if usage is not None:
                parts.append(byte_count(usage.flash_used))
            if device:
                parts.append(device)
            return " · ".join(parts)

        return text

    def _on_state_changed(self, state: OperationState) -> None:
        # How it ended is reported by _finish, which knows the details.
        if state is OperationState.RUNNING:
            self._window.show_activity(N_("Прошиваю…"))
