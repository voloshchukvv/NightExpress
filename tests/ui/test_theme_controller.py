"""Choosing a theme in the settings."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from nightexpress.controllers.theme_controller import SYSTEM_CHOICE, ThemeController
from nightexpress.services.theme_catalog import ThemeCatalog
from nightexpress.views.main_window import MainWindow
from nightexpress.views.theme import palette

CATALOG = ThemeCatalog.load()


class Scheme:
    """Stands in for the operating system's light/dark setting."""

    def __init__(self) -> None:
        self.value = Qt.ColorScheme.Dark

    def __call__(self) -> Qt.ColorScheme:
        return self.value


@pytest.fixture
def app():
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    yield application
    palette.apply_theme(application, CATALOG.default)
    application.setStyleSheet("")


def window_colour(application: QApplication) -> str:
    return application.palette().color(QPalette.ColorRole.Window).name()


def test_saved_theme_is_applied_and_listed(qtbot, dispose, app, settings_store) -> None:
    settings_store.set_theme("ocean")
    controller = ThemeController(app, CATALOG, settings_store, Scheme())
    controller.apply()
    assert palette.current().id == "ocean"
    assert window_colour(app) == CATALOG.get("ocean").color("background")

    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    controller.attach(window)
    for panel in (window.welcome.settings, window.settings_dialog.application):
        combo = panel.theme_combo
        names = [combo.itemText(i) for i in range(combo.count())]
        assert names == ["Як у системі", "Графіт", "Висока контрастність", "Океан", "Світла"]
        assert combo.currentData() == "ocean"


def test_picking_a_theme_applies_saves_and_syncs_both_settings_pages(
    qtbot, dispose, app, settings_store
) -> None:
    controller = ThemeController(app, CATALOG, settings_store, Scheme())
    controller.apply()
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    controller.attach(window)
    in_dialog = window.settings_dialog.application.theme_combo
    on_welcome = window.welcome.settings.theme_combo
    assert in_dialog.currentData() == on_welcome.currentData() == "graphite"

    index = in_dialog.findData("light")
    in_dialog.setCurrentIndex(index)
    with qtbot.waitSignal(palette.signals.changed):
        in_dialog.activated.emit(index)  # the user picked "Light"
    assert palette.current().id == "light" and settings_store.theme() == "light"
    assert window_colour(app) == CATALOG.get("light").color("background")
    assert on_welcome.currentData() == "light"  # the other copy of the settings follows

    with qtbot.assertNotEmitted(palette.signals.changed):
        controller.select("light")  # the same again: nothing to repaint


def test_system_choice_follows_the_operating_system(qtbot, app, settings_store) -> None:
    scheme = Scheme()
    controller = ThemeController(app, CATALOG, settings_store, scheme)
    controller.select(SYSTEM_CHOICE)
    assert settings_store.theme() == "system" and palette.current().id == "graphite"

    scheme.value = Qt.ColorScheme.Light
    controller.on_system_scheme_changed()
    assert palette.current().id == "light"
    scheme.value = Qt.ColorScheme.Unknown  # the platform does not say: stay with the default
    controller.on_system_scheme_changed()
    assert palette.current().id == "graphite"

    controller.select("ocean")
    scheme.value = Qt.ColorScheme.Light
    controller.on_system_scheme_changed()  # a fixed choice ignores the system
    assert palette.current().id == "ocean"


def test_a_theme_that_no_longer_exists_becomes_the_default(qtbot, app, settings_store) -> None:
    settings_store.set_theme("removed-theme")
    controller = ThemeController(app, CATALOG, settings_store, Scheme())
    controller.apply()
    assert controller.choice == "graphite" and palette.current().id == "graphite"
    controller.select("no-such-theme")
    assert controller.choice == "graphite"
