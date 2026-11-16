"""Composition root: the only place where models, views, controllers and services are created
and wired together.

All other modules receive their dependencies through constructors and never create them
themselves, so controllers can be tested with fake services and views with fake models.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication

from nightexpress import APP_NAME, __version__
from nightexpress.controllers.app_controller import AppController
from nightexpress.controllers.board_controller import BoardController
from nightexpress.controllers.build_controller import BuildController
from nightexpress.controllers.busy_tracker import BusyTracker
from nightexpress.controllers.examples_controller import ExamplesController
from nightexpress.controllers.language_controller import LanguageController
from nightexpress.controllers.peripheral_controller import PeripheralController
from nightexpress.controllers.ports_controller import PortsController
from nightexpress.controllers.project_controller import ProjectController
from nightexpress.controllers.rtt_controller import RttController
from nightexpress.controllers.serial_monitor_controller import SerialMonitorController
from nightexpress.controllers.terminal_controller import TerminalController
from nightexpress.controllers.theme_controller import ThemeController
from nightexpress.controllers.toolchain_controller import ToolchainController
from nightexpress.controllers.upload_controller import UploadController
from nightexpress.models.state import (
    AppModel,
    BuildModel,
    PortsModel,
    ProjectModel,
    SerialMonitorModel,
    ToolchainModel,
    UploadModel,
)
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.board_migration import BoardMigration
from nightexpress.services.cargo_service import CargoService
from nightexpress.services.dfu_util import DfuService
from nightexpress.services.file_service import FileService
from nightexpress.services.language_catalog import LanguageCatalog
from nightexpress.services.probe_rs import ProbeRsService
from nightexpress.services.project_config_repository import ProjectConfigRepository
from nightexpress.services.project_generator import ProjectGenerator
from nightexpress.services.rtt_service import RttService
from nightexpress.services.serial_flash_service import SerialFlashService
from nightexpress.services.serial_monitor_service import SerialMonitorService
from nightexpress.services.serial_port_service import SerialPortService
from nightexpress.services.settings_store import SettingsStore
from nightexpress.services.theme_catalog import ThemeCatalog
from nightexpress.services.toolchain_probe import ToolchainProbe, install_hint, install_info
from nightexpress.views.main_window import MainWindow

_QT_TRANSLATOR = "nightexpressQtTranslator"  # the property of the application that holds it


def install_qt_translation(qapp: QApplication, language: str) -> bool:
    """Qt's own texts (buttons of standard dialogs, names of keys) in the interface language.

    Returns False when Qt has no translation for the language: its texts stay English.
    """
    previous = qapp.property(_QT_TRANSLATOR)
    if isinstance(previous, QTranslator):
        qapp.removeTranslator(previous)
        previous.deleteLater()
        qapp.setProperty(_QT_TRANSLATOR, None)
    translator = QTranslator(qapp)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load(QLocale(language), "qtbase", "_", directory):
        translator.deleteLater()
        return False
    qapp.installTranslator(translator)
    qapp.setProperty(_QT_TRANSLATOR, translator)
    return True


def build_app(
    argv: Sequence[str],
    settings: SettingsStore | None = None,
    check_tools_on_start: bool = True,
) -> tuple[QApplication, AppController]:
    """Create the QApplication and the object graph without starting the event loop."""
    qapp = QApplication.instance()
    if not isinstance(qapp, QApplication):
        qapp = QApplication(list(argv))
    qapp.setApplicationName(APP_NAME)
    qapp.setOrganizationName(APP_NAME)
    qapp.setApplicationVersion(__version__)

    # Services
    settings = settings or SettingsStore()
    # The language comes first of all: every text is translated at the moment it is created.
    languages = LanguageController(
        LanguageCatalog.load(),
        settings,
        QLocale.system().uiLanguages,
        lambda language: install_qt_translation(qapp, language),
        parent=qapp,
    )
    languages.install()
    # Then the theme: views read their colours while they are being created.
    themes = ThemeController(qapp, ThemeCatalog.load(), settings)
    themes.apply()
    catalog = BoardCatalog.load()
    files = FileService()
    config_repository = ProjectConfigRepository(files)
    generator = ProjectGenerator()
    probe = ToolchainProbe()

    # Models
    app_model = AppModel()
    project_model = ProjectModel()
    toolchain_model = ToolchainModel()
    build_model = BuildModel()
    upload_model = UploadModel()
    ports_model = PortsModel()

    # Views
    window = MainWindow(app_name=APP_NAME, version=__version__)
    themes.attach(window)
    languages.attach(window)

    # Controllers
    projects = ProjectController(
        window,
        app_model,
        project_model,
        catalog,
        settings,
        generator,
        config_repository,
        files,
        rustfmt_command=lambda: (str(found),) if (found := probe.find("rustfmt")) else None,
        tool_env={**os.environ, "PATH": probe.search_path},
    )
    ExamplesController(window, project_model, catalog, projects)
    toolchain = ToolchainController(toolchain_model, probe, window)
    if check_tools_on_start:
        # The hints of missing tools are written in the language of the check.
        languages.switched.connect(lambda _language: toolchain.check())

    def cargo_command() -> tuple[str, ...] | None:
        cargo = probe.find("cargo")
        return (str(cargo),) if cargo else None

    def probe_rs_command() -> tuple[str, ...] | None:
        tool = probe.find("probe-rs")
        return (str(tool),) if tool else None

    def dfu_util_command() -> tuple[str, ...] | None:
        tool = probe.find("dfu-util")
        return (str(tool),) if tool else None

    cargo = CargoService(search_path=probe.search_path, parent=window)
    builds = BuildController(
        window,
        project_model,
        build_model,
        catalog,
        cargo,
        cargo_command,
        save_all=projects.prepare_build,
        open_file=projects.open_file,
        install_hint=install_hint("cargo"),
    )
    probe_rs = ProbeRsService(search_path=probe.search_path, parent=window)
    # Application exit: stop child processes instead of leaving them orphaned.
    qapp.aboutToQuit.connect(probe_rs.shutdown)
    qapp.aboutToQuit.connect(cargo.shutdown)
    monitor_service = SerialMonitorService(parent=window)
    qapp.aboutToQuit.connect(monitor_service.close)  # stop the reader thread, free the port
    terminal = TerminalController(window, project_model, search_path=probe.search_path)
    qapp.aboutToQuit.connect(terminal.shutdown)  # the shell and whatever it still runs
    monitor = SerialMonitorController(
        window, SerialMonitorModel(), ports_model, project_model, settings, monitor_service
    )
    serial_flash = SerialFlashService(parent=window)
    qapp.aboutToQuit.connect(serial_flash.shutdown)  # do not keep writing flash after exit
    dfu = DfuService(search_path=probe.search_path, parent=window)
    qapp.aboutToQuit.connect(dfu.shutdown)
    rtt_service = RttService(search_path=probe.search_path, parent=window)
    qapp.aboutToQuit.connect(rtt_service.shutdown)  # free the probe
    rtt = RttController(window, project_model, upload_model, rtt_service, probe_rs_command)
    uploads = UploadController(
        window,
        project_model,
        upload_model,
        ports_model,
        catalog,
        builds,
        probe_rs,
        probe_rs_command,
        install_hint=install_hint("probe-rs"),
        serial_flash=serial_flash,
        settings=settings,
        serial_monitor=monitor,
        probe_user=rtt,  # it steps aside before flashing
        dfu=dfu,
        dfu_command=dfu_util_command,
        dfu_install_hint=install_hint("dfu-util"),
        dfu_install_command=install_info("dfu-util").command,
    )
    uploads.probe_flashed.connect(rtt.on_flashed)
    busy = BusyTracker(window, build_model, upload_model)
    BoardController(
        window,
        project_model,
        build_model,
        catalog,
        BoardMigration(files, config_repository, generator),
        config_repository,
        save_all=projects.save_all,
        refresh_files=projects.check_external_changes,
        is_busy=lambda: busy.busy,
    )
    PeripheralController(
        window,
        project_model,
        catalog,
        save_all=projects.save_all,
        refresh_files=projects.check_external_changes,
        open_file=projects.open_file,
        is_busy=lambda: busy.busy,
        files=files,
    )
    PortsController(
        window,
        ports_model,
        project_model,
        probe_rs,
        SerialPortService(),
        settings,
        probe_rs_command,
        is_busy=lambda: busy.busy,
        dfu=dfu,
        dfu_command=dfu_util_command,
    )
    controller = AppController(
        window,
        app_model,
        settings,
        catalog,
        projects,
        toolchain,
        builds,
        check_tools_on_start,
        uploads,
    )
    qapp.aboutToQuit.connect(controller.save_window_state)  # sizes, panels, open tabs
    return qapp, controller


def run(argv: Sequence[str]) -> int:
    qapp, controller = build_app(argv)
    controller.start()
    return qapp.exec()
