"""Which interface language is shown: the user's choice from the settings, kept between runs.

A new choice applies at once: ``i18n.switch`` redoes every text on screen (see ``i18n.live``),
Qt's own texts change with its translation.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib.resources.abc import Traversable

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QWidget

from nightexpress import i18n
from nightexpress.i18n import N_, Text
from nightexpress.services.language_catalog import SYSTEM_LANGUAGE, LanguageCatalog
from nightexpress.services.settings_store import SettingsStore
from nightexpress.views.main_window import MainWindow
from nightexpress.views.settings_panel import AppSettingsPanel
from nightexpress.views.widgets import retranslate_qt_texts

SYSTEM_LANGUAGE_NAME = N_("Як у системі")


def switch_language(
    language: str,
    qt_translation: Callable[[str], object] | None = None,
    roots: Sequence[QWidget] = (),
    directory: Traversable | None = None,
) -> None:
    """Show ``language`` everywhere: Qt's own texts first, then ours (see ``i18n.switch``).

    ``roots``: the windows whose widgets Qt does not retranslate by itself.
    ``directory``: where the catalogs are, for tests.
    """
    if qt_translation is not None:
        qt_translation(language)
        # Qt retranslates its widgets on these events; ours come after, so a button whose
        # standard text we replaced ("Done") keeps our text.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LanguageChange)
        for root in roots:
            retranslate_qt_texts(root)
    i18n.switch(language, directory)


class LanguageController(QObject):
    switched = Signal(str)  # the code of the language now on screen

    def __init__(
        self,
        catalog: LanguageCatalog,
        settings: SettingsStore,
        system_languages: Callable[[], Sequence[str]],
        qt_translation: Callable[[str], object] | None = None,
        parent: QObject | None = None,
    ) -> None:
        """``system_languages``: the languages of the operating system, the preferred first.
        ``qt_translation``: installs Qt's own translation (standard buttons) for a language."""
        super().__init__(parent)
        self._catalog = catalog
        self._settings = settings
        self._system_languages = system_languages
        self._qt_translation = qt_translation
        self._panels: tuple[AppSettingsPanel, ...] = ()
        self._window: MainWindow | None = None
        self._choice = self._known(settings.language())

    @property
    def choice(self) -> str:
        """What the user picked: a language code or SYSTEM_LANGUAGE."""
        return self._choice

    @property
    def choices(self) -> list[tuple[str, Text]]:
        """(code, name) for the list in the settings: the system rule first, then the
        languages, each named in itself."""
        languages: list[tuple[str, Text]] = [
            (language.code, lambda name=language.name: name) for language in self._catalog.languages
        ]
        return [(SYSTEM_LANGUAGE, SYSTEM_LANGUAGE_NAME), *languages]

    def _known(self, choice: str) -> str:
        """``choice``, or the system rule if it names a language that no longer exists."""
        return choice if choice in {code for code, _name in self.choices} else SYSTEM_LANGUAGE

    def language_for(self, choice: str) -> str:
        """The code of the language ``choice`` stands for on this computer."""
        return self._catalog.resolve(choice, self._system_languages())

    def install(self) -> None:
        """At start-up, before any view exists: the chosen language for every text."""
        language = self.language_for(self._choice)
        i18n.install(language)
        if self._qt_translation is not None:
            self._qt_translation(language)

    def attach(self, window: MainWindow) -> None:
        """Show the choice in both copies of the settings and follow what the user picks."""
        self._window = window
        self._panels = (window.welcome.settings, window.settings_dialog.application)
        for panel in self._panels:
            panel.set_languages(self.choices)
            panel.set_language(self._choice)
            panel.language_changed.connect(self.select)

    def select(self, choice: str) -> None:
        choice = self._known(choice)
        if choice == self._choice:
            return
        self._choice = choice
        self._settings.set_language(choice)
        for panel in self._panels:
            panel.set_language(choice)
        language = self.language_for(choice)
        if language != i18n.current():
            self._switch(language)

    def _switch(self, language: str) -> None:
        roots = [self._window] if self._window is not None else []
        switch_language(language, self._qt_translation, roots)
        self.switched.emit(language)
