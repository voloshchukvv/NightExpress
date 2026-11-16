"""Which colour theme is active: the user's choice from the settings, kept between runs."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication

from nightexpress.i18n import N_, Text
from nightexpress.models.entities.theme import Theme
from nightexpress.services.settings_store import SettingsStore
from nightexpress.services.theme_catalog import ThemeCatalog
from nightexpress.views.main_window import MainWindow
from nightexpress.views.settings_panel import AppSettingsPanel
from nightexpress.views.theme.palette import apply_theme

# Not a theme but a rule: the light theme while the system is light, the default one otherwise.
SYSTEM_CHOICE = "system"
SYSTEM_CHOICE_NAME = N_("Як у системі")
LIGHT_THEME_ID = "light"


class ThemeController(QObject):
    def __init__(
        self,
        app: QApplication,
        catalog: ThemeCatalog,
        settings: SettingsStore,
        color_scheme: Callable[[], Qt.ColorScheme] | None = None,
    ) -> None:
        super().__init__(app)
        self._app = app
        self._catalog = catalog
        self._settings = settings
        self._color_scheme = color_scheme or app.styleHints().colorScheme
        self._panels: tuple[AppSettingsPanel, ...] = ()
        self._choice = self._known(settings.theme())
        app.styleHints().colorSchemeChanged.connect(self.on_system_scheme_changed)

    @property
    def choice(self) -> str:
        """What the user picked: a theme id or SYSTEM_CHOICE."""
        return self._choice

    @property
    def choices(self) -> list[tuple[str, Text]]:
        """(id, name) for the list in the settings: the system rule first, then the themes.
        The names follow the language (see i18n.live)."""
        themes: list[tuple[str, Text]] = [
            (theme.id, lambda theme=theme: theme.name) for theme in self._catalog.themes
        ]
        return [(SYSTEM_CHOICE, SYSTEM_CHOICE_NAME), *themes]

    def _known(self, choice: str) -> str:
        """``choice``, or the default theme if it names a theme that no longer exists."""
        return choice if choice in {c for c, _name in self.choices} else self._catalog.default.id

    def theme_for(self, choice: str) -> Theme:
        if choice == SYSTEM_CHOICE:
            light = self._color_scheme() is Qt.ColorScheme.Light
            return self._catalog.get(LIGHT_THEME_ID) if light else self._catalog.default
        return self._catalog.get(choice)

    def apply(self) -> None:
        """Make the chosen theme the look of the application (also of windows already open)."""
        apply_theme(self._app, self.theme_for(self._choice))

    def attach(self, window: MainWindow) -> None:
        """Show the choice in both copies of the settings and follow what the user picks."""
        self._panels = (window.welcome.settings, window.settings_dialog.application)
        for panel in self._panels:
            panel.set_themes(self.choices)
            panel.set_theme(self._choice)
            panel.theme_changed.connect(self.select)

    def select(self, choice: str) -> None:
        choice = self._known(choice)
        if choice == self._choice:
            return
        self._choice = choice
        self._settings.set_theme(choice)
        for panel in self._panels:
            panel.set_theme(choice)
        self.apply()

    def on_system_scheme_changed(self, _scheme: object = None) -> None:
        if self._choice == SYSTEM_CHOICE:
            self.apply()
