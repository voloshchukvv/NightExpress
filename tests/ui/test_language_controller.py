"""Choosing the interface language in the settings: the window switches at once."""

from __future__ import annotations

from pathlib import Path

import pytest

from nightexpress import i18n
from nightexpress.controllers.language_controller import LanguageController
from nightexpress.services.language_catalog import SYSTEM_LANGUAGE, LanguageCatalog
from nightexpress.views.main_window import MainWindow

pytestmark = pytest.mark.ui


@pytest.fixture
def catalog(tmp_path: Path) -> LanguageCatalog:
    """Ukrainian and English: only the names matter here, the texts stay untranslated."""
    for code, name in (("uk", "Українська"), ("en", "English")):
        (tmp_path / code).mkdir()
        (tmp_path / code / "language.toml").write_text(f'name = "{name}"\n', encoding="utf-8")
    return LanguageCatalog.load(tmp_path)


@pytest.fixture
def window(qtbot, dispose) -> MainWindow:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    return window


def controller_for(catalog, settings, system=("uk-UA",), qt=None) -> LanguageController:
    return LanguageController(catalog, settings, lambda: list(system), qt)


def test_saved_language_is_listed_in_both_settings_pages(window, catalog, settings_store) -> None:
    settings_store.set_language("en")
    controller = controller_for(catalog, settings_store)
    controller.attach(window)
    for panel in (window.welcome.settings, window.settings_dialog.application):
        combo = panel.language_combo
        names = [combo.itemText(i) for i in range(combo.count())]
        assert names == ["Як у системі", "Українська", "English"]  # each in its own language
        assert combo.currentData() == "en"


def test_picking_a_language_switches_the_window_at_once(
    qtbot, window, catalog, settings_store
) -> None:
    window.show()
    qt_languages: list[str] = []
    switched: list[str] = []
    controller = controller_for(catalog, settings_store, qt=qt_languages.append)
    controller.switched.connect(switched.append)
    controller.attach(window)
    in_dialog = window.settings_dialog.application
    on_welcome = window.welcome.settings

    index = in_dialog.language_combo.findData("en")
    in_dialog.language_combo.setCurrentIndex(index)
    in_dialog.language_combo.activated.emit(index)  # the user picked English

    assert settings_store.language() == "en" and controller.choice == "en"
    assert i18n.current() == "en" and qt_languages == ["en"] and switched == ["en"]
    assert on_welcome.language_combo.currentData() == "en"  # the other copy follows
    assert window.toasts.toasts == ()  # nothing to restart

    controller.select("en")  # the same again changes nothing
    assert switched == ["en"]
    controller.select("uk")
    assert i18n.current() == "uk" and switched == ["en", "uk"]


def test_system_choice_follows_the_languages_of_the_computer(catalog, settings_store) -> None:
    settings_store.set_language(SYSTEM_LANGUAGE)
    controller = controller_for(catalog, settings_store, system=("en-US", "uk-UA"))
    assert controller.language_for(SYSTEM_LANGUAGE) == "en"
    controller.install()
    assert i18n.current() == "en"

    german = controller_for(catalog, settings_store, system=("de-DE",))
    assert german.language_for(SYSTEM_LANGUAGE) == "en"  # not ours: English


def test_picking_the_system_rule_that_means_the_same_language_asks_nothing(
    window, catalog, settings_store
) -> None:
    controller = controller_for(catalog, settings_store, system=("uk-UA",))  # saved: "uk"
    controller.attach(window)
    switched: list[str] = []
    controller.switched.connect(switched.append)
    controller.select(SYSTEM_LANGUAGE)
    assert settings_store.language() == SYSTEM_LANGUAGE
    assert switched == [] and i18n.current() == "uk"


def test_a_removed_language_means_the_system_rule(window, catalog, settings_store) -> None:
    settings_store.set_language("eo")
    controller = controller_for(catalog, settings_store)
    assert controller.choice == SYSTEM_LANGUAGE
    controller.attach(window)
    assert window.welcome.settings.language_combo.currentData() == SYSTEM_LANGUAGE
    controller.select("xx")  # an unknown code picked: also the system rule, nothing changes
    assert controller.choice == SYSTEM_LANGUAGE and settings_store.language() == "eo"
