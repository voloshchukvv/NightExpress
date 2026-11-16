"""AppController: application lifecycle (startup, screen switching, shutdown)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray

from nightexpress.controllers.build_controller import BuildController
from nightexpress.controllers.project_controller import ProjectController
from nightexpress.controllers.toolchain_controller import ToolchainController
from nightexpress.controllers.upload_controller import UploadController
from nightexpress.i18n import N_, _
from nightexpress.models.entities.font import DEFAULT_FONT_SIZE, FontArea
from nightexpress.models.state import AppModel, Screen
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.settings_store import SettingsStore
from nightexpress.views.main_window import MainWindow
from nightexpress.views.settings_panel import FONT_AREA_NAMES, OPTIONS, AppSettingsPanel
from nightexpress.views.toast import ToastKind
from nightexpress.views.welcome_view import SETTINGS

HELP_TOPICS = {
    "stlink": (
        N_("Як підключити ST-Link"),
        N_(
            "SWDIO → SWDIO (PA13), SWCLK → SWCLK (PA14), GND → GND, 3.3V → 3.3V. "
            "Перемичка BOOT0 = 0. Потім виберіть ST-Link у меню «Вибрати пристрій»."
        ),
    ),
    "usb_ttl": (
        N_("Як підключити USB-TTL"),
        N_(
            "Для STM32F1: TX адаптера → PA10, RX адаптера → PA9, GND → GND, рівень 3.3 V. "
            "Перед прошиванням: BOOT0 = 1 і натиснути Reset; після — повернути BOOT0 = 0."
        ),
    ),
    "usb_dfu": (
        N_("Як прошити через USB (DFU)"),
        N_(
            "Для Black Pill (STM32F401/F411) адаптер не потрібен: під'єднайте плату кабелем USB, "
            "затисніть BOOT0, натисніть і відпустіть Reset (NRST), відпустіть BOOT0. Плата "
            "з'явиться в меню «Вибрати пристрій» як STM32 BOOTLOADER. Потрібен dfu-util; після "
            "прошивання програма запускається сама."
        ),
    ),
}


class AppController:
    def __init__(
        self,
        window: MainWindow,
        app_model: AppModel,
        settings: SettingsStore,
        catalog: BoardCatalog,
        projects: ProjectController,
        toolchain: ToolchainController,
        builds: BuildController,
        check_tools_on_start: bool = True,
        uploads: UploadController | None = None,
    ) -> None:
        self._window = window
        self._app = app_model
        self._settings = settings
        self._catalog = catalog
        self.projects = projects
        self.toolchain = toolchain
        self.builds = builds
        self.uploads = uploads
        self._check_tools_on_start = check_tools_on_start

        app_model.screen_changed.connect(window.set_screen)
        # The cards also show when a project was opened, which changes without the list.
        app_model.screen_changed.connect(self._on_screen_changed)
        app_model.recent_projects_changed.connect(self._show_recent_projects)
        window.home_requested.connect(lambda: app_model.set_screen(Screen.WELCOME))
        window.settings_requested.connect(self.show_settings)
        window.help_requested.connect(self.show_help)
        window.welcome.change_projects_directory_requested.connect(self._change_projects_directory)
        preferences = window.settings_dialog.application
        preferences.projects_directory_requested.connect(self._change_projects_directory)
        window.font_size_step_requested.connect(self._step_font_size)
        for panel in self._settings_panels():
            panel.option_changed.connect(self._set_option)
            panel.font_size_changed.connect(self._set_font_size)
            panel.single_font_size_changed.connect(self._set_single_font_size)

    @property
    def window(self) -> MainWindow:
        return self._window

    def start(self) -> None:
        self._window.toolbar.set_boards(self._catalog.boards)
        self._window.set_projects_directory(self._settings.projects_directory())
        options = self._options()
        for panel in self._settings_panels():
            panel.set_options(options)
        self._window.editor_tabs.editor.set_bracket_pair_guides(options["bracket_pair_guides"])
        self._window.editor_tabs.editor.set_minimap(options["minimap"])
        self._show_font_sizes()
        self._app.set_recent_projects(self._settings.recent_projects())
        self._show_recent_projects()
        self._restore_window()
        self._window.set_screen(self._app.screen)
        self._window.show()
        self._open_last_project()
        if self._check_tools_on_start:
            self.toolchain.check()

    # --- remembered state (U8.1, U8.2) -------------------------------------------------------

    def _settings_panels(self) -> tuple[AppSettingsPanel, ...]:
        """The same preferences live on the start screen and in the editor's dialog."""
        return (self._window.welcome.settings, self._window.settings_dialog.application)

    def _options(self) -> dict[str, bool]:
        return {key: getattr(self._settings, key)() for key in OPTIONS}

    def _set_option(self, key: str, value: bool) -> None:
        if key in OPTIONS:
            getattr(self._settings, f"set_{key}")(value)
            for panel in self._settings_panels():
                panel.set_options({key: value})
            if key == "bracket_pair_guides":
                self._window.editor_tabs.editor.set_bracket_pair_guides(value)
            if key == "minimap":
                self._window.editor_tabs.editor.set_minimap(value)

    def _restore_window(self) -> None:
        geometry = self._settings.window_geometry()
        if geometry is not None:
            self._window.restoreGeometry(QByteArray(geometry))
        self._window.restore_layout(self._settings.window_layout())

    def save_window_state(self) -> None:
        """At exit: window size and position, panels; the open project's tabs and cursors."""
        self.projects.save_session()
        self._settings.set_window_geometry(bytes(self._window.saveGeometry().data()))
        self._settings.set_window_layout(self._window.layout_state())

    def _open_last_project(self) -> None:
        if not self._settings.open_last_project():
            return
        last = next(iter(self._app.recent_projects), None)
        if last is not None and last.is_dir():
            self.projects.open_project(last)

    def show_settings(self) -> None:
        if self._app.screen is Screen.EDITOR:
            self.projects.show_settings()
        else:
            self._window.welcome.show_page(SETTINGS)

    def show_help(self, topic: str) -> None:
        """Short wiring note in a toast (full help pages come with stage 10.3)."""
        if topic in HELP_TOPICS:
            title, text = HELP_TOPICS[topic]
            self._window.notify(_(title), _(text), ToastKind.WARNING)  # stays until read: no timer

    def _on_screen_changed(self, screen: Screen) -> None:
        if screen is Screen.WELCOME:
            self._show_recent_projects()

    def _show_recent_projects(self, *_args: object) -> None:
        self._window.set_recent_projects(self.projects.recent_projects())

    def _change_projects_directory(self) -> None:
        chosen = self._window.ask_directory(
            _("Тека для нових проєктів"), self._settings.projects_directory()
        )
        if chosen is not None:
            self._settings.set_projects_directory(chosen)
            self._window.set_projects_directory(chosen)

    # --- font sizes: one for every text area, or one for each -------------------------------

    def _set_font_size(self, area: FontArea, size: int) -> None:
        """A size picked in the settings. While one size serves every area, they all take it."""
        single = self._settings.single_font_size()
        for target in FontArea if single else (area,):
            self._settings.set_font_size(target, size)
        self._show_font_sizes()

    def _set_single_font_size(self, single: bool) -> None:
        self._settings.set_single_font_size(single)
        if single:  # the areas come together at the editor's size
            self._set_font_size(FontArea.EDITOR, self._settings.font_size(FontArea.EDITOR))
        else:  # nothing changes on screen yet: every area starts from the common size
            self._show_font_sizes()

    def _show_font_sizes(self) -> None:
        """The saved sizes in both copies of the settings and in the text areas themselves."""
        sizes = {area: self._settings.font_size(area) for area in FontArea}  # clamped
        for panel in self._settings_panels():
            panel.set_font_sizes(sizes, self._settings.single_font_size())
        for area, size in sizes.items():
            self._window.set_font_size(area, size)

    def _step_font_size(self, focused: FontArea, step: int) -> None:
        """Cmd+= / Cmd+- / Cmd+0: the same settings as on the settings page. With a size per
        area the keys act on the area that has the keyboard focus."""
        single = self._settings.single_font_size()
        area = FontArea.EDITOR if single else focused
        size = DEFAULT_FONT_SIZE if step == 0 else self._settings.font_size(area) + step
        self._set_font_size(area, size)
        size = self._settings.font_size(area)
        if single:
            text = _("Розмір шрифту: {size} pt").format(size=size)
        else:
            text = _("Розмір шрифту ({area}): {size} pt").format(
                area=_(FONT_AREA_NAMES[area]).lower(), size=size
            )
        self._window.show_message(text, 2000)
