import os

# Widget tests run without a display (CI, SSH).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# QtWebEngine (Monaco editor) cannot use the GPU without a display.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-logging --log-level=3")


import pytest

from nightexpress import i18n
from nightexpress.services.theme_catalog import ThemeCatalog
from nightexpress.views.theme import palette

# Views read their colours from the active theme; in the application app.py sets it.
palette.set_current(ThemeCatalog.load().default)


@pytest.fixture(autouse=True)
def source_language():
    """The tests compare Ukrainian texts, whatever the language of the machine they run on."""
    i18n.install(i18n.SOURCE_LANGUAGE)
    yield
    i18n.install(i18n.SOURCE_LANGUAGE)


@pytest.fixture
def dispose(qtbot):
    """Close and delete widgets that host QtWebEngine before the browser profile goes away."""
    widgets = []
    yield widgets.append
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    qtbot.wait(10)


@pytest.fixture
def settings_store(tmp_path):
    """Isolated settings: tests must never touch the user's real QSettings."""
    from PySide6.QtCore import QSettings

    from nightexpress.services.settings_store import SettingsStore

    store = SettingsStore(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    store.set_language(i18n.SOURCE_LANGUAGE)  # not "system": see the source_language fixture
    return store


@pytest.fixture
def no_debug_probes(monkeypatch):
    """The application finds no debug probe: tests that build it must not depend on (or talk
    to) an ST-Link that happens to be plugged in, whose listing arrives at any moment."""
    from nightexpress.services.probe_rs import ProbeRsService

    def list_probes(self: ProbeRsService) -> bool:
        self.probes_listed.emit([])
        return True

    monkeypatch.setattr(ProbeRsService, "list_probes", list_probes)
