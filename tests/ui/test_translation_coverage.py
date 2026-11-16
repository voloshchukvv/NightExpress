"""Every text the views show goes through the translation functions.

A made-up language translates each text of the template to the same text with a mark in
front. A Ukrainian text on screen without the mark never passed through ``_``: it would stay
Ukrainian in every language.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QLabel,
    QLineEdit,
    QMenu,
    QTabBar,
    QWidget,
)

from nightexpress import i18n
from nightexpress.app import install_qt_translation
from nightexpress.models.entities import ToolStatus
from nightexpress.services.language_catalog import LanguageCatalog
from nightexpress.views.dialogs.about_dialog import AboutDialog
from nightexpress.views.dialogs.boot_mode_dialog import BootModeDialog
from nightexpress.views.dialogs.shortcuts_dialog import ShortcutsDialog
from nightexpress.views.dialogs.toolchain_dialog import ToolchainDialog
from nightexpress.views.main_window import MainWindow

pytestmark = [pytest.mark.ui, pytest.mark.usefixtures("no_debug_probes")]

MARK = "¤"
CYRILLIC = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")
TEMPLATE = Path(str(i18n.locales_directory())) / "nightexpress.pot"


def write_marked_catalog(root: Path) -> Path:
    """A catalog in ``root`` whose every translation is the source text with MARK in front."""
    with TEMPLATE.open("rb") as stream:
        template = read_po(stream)
    catalog = Catalog(locale="en")
    for message in template:
        if not message.id:
            continue
        if isinstance(message.id, tuple):
            forms = tuple(MARK + form for form in message.id)
            catalog.add(message.id, forms, context=message.context)
        else:
            catalog.add(message.id, MARK + message.id, context=message.context)
    directory = root / "en" / "LC_MESSAGES"
    directory.mkdir(parents=True)
    with (directory / "nightexpress.mo").open("wb") as stream:
        write_mo(stream, catalog)
    return root


@pytest.fixture
def marked_language(tmp_path: Path, qapp) -> Iterator[None]:
    """Install the marked language (see ``write_marked_catalog``)."""
    # Qt's own texts (tool tips of the tab scroll buttons) are Qt's to translate: keep them
    # English here, so that only the texts of this application can be Ukrainian.
    install_qt_translation(qapp, "en")
    i18n.install("en", write_marked_catalog(tmp_path / "locales"))
    yield
    install_qt_translation(qapp, i18n.SOURCE_LANGUAGE)


def shown_texts(root: QWidget) -> list[str]:
    """Everything ``root`` and its children can show: texts, tool tips, titles, list items."""
    texts = []
    for widget in [root, *root.findChildren(QWidget)]:
        texts += [widget.toolTip(), widget.windowTitle(), widget.accessibleName()]
        if isinstance(widget, QLabel | QAbstractButton):
            texts.append(widget.text())
        elif isinstance(widget, QLineEdit):
            texts.append(widget.placeholderText())
        elif isinstance(widget, QComboBox):
            texts += [widget.itemText(i) for i in range(widget.count())]
        elif isinstance(widget, QTabBar):
            texts += [widget.tabText(i) for i in range(widget.count())]
            texts += [widget.tabToolTip(i) for i in range(widget.count())]
        elif isinstance(widget, QMenu):
            texts.append(widget.title())
    for action in root.findChildren(QAction):
        texts += [action.text(), action.toolTip(), action.statusTip()]
    return texts


def untranslated(root: QWidget) -> list[str]:
    return sorted({t for t in shown_texts(root) if CYRILLIC.search(t) and MARK not in t})


def test_the_marked_language_is_installed(marked_language: None) -> None:
    assert i18n._("Зібрати") == MARK + "Зібрати"


def test_main_window_shows_no_untranslated_text(qtbot, dispose, marked_language: None) -> None:
    window = MainWindow(app_name="NightExpress", version="1.2.3")
    dispose(window)
    assert window.toolbar.verify_button.text() == MARK + "Зібрати"  # the check does check
    assert untranslated(window) == []


def test_dialogs_show_no_untranslated_text(qtbot, marked_language: None) -> None:
    missing = ToolStatus(
        name="cargo",
        found=False,
        required=True,
        install_command="curl https://sh.rustup.rs | sh",
        help_url="https://rustup.rs",
    )
    toolchain = ToolchainDialog()
    toolchain.set_statuses([missing])
    dialogs = [
        AboutDialog("NightExpress", "1.2.3"),
        ShortcutsDialog([("Файл", [("Зберегти", "Ctrl+S")])]),
        BootModeDialog("PL2303 (usbserial-120)", "PA9", "PA10"),
        toolchain,
    ]
    for dialog in dialogs:
        qtbot.addWidget(dialog)
    # The groups of the shortcuts page come translated from the window: not this dialog's job.
    assert untranslated(dialogs[0]) == []
    assert [t for t in untranslated(dialogs[1]) if t not in {"Файл", "Зберегти"}] == []
    assert untranslated(dialogs[2]) == []
    assert untranslated(dialogs[3]) == []


def test_texts_of_services_and_models_are_translated(marked_language: None) -> None:
    from nightexpress.models.entities import project_name_error
    from nightexpress.models.qt_models.diagnostics_tree_model import DiagnosticsTreeModel
    from nightexpress.services.serial_bootloader import BootloaderError, BootloaderErrorKind

    # Texts kept in module-level tables are translated when they are asked for.
    for kind in BootloaderErrorKind:
        assert BootloaderError(kind).hint.startswith(MARK)
        assert str(BootloaderError(kind)).startswith(MARK)
    assert (project_name_error("") or "").startswith(MARK)
    model = DiagnosticsTreeModel()
    headers = [model.horizontalHeaderItem(column).text() for column in range(model.columnCount())]
    assert all(header.startswith(MARK) for header in headers)


def all_shown_texts(roots: list[QWidget]) -> list[str]:
    texts = []
    for root in roots:
        texts += shown_texts(root)
    return texts


def untranslated_in(roots: list[QWidget]) -> list[str]:
    # Language names are written in their own languages.
    names = {language.name for language in LanguageCatalog.load().languages}
    shown = set(all_shown_texts(roots)) - names
    return sorted(t for t in shown if CYRILLIC.search(t) and MARK not in t)


def test_switching_the_language_redoes_every_text(qtbot, dispose, settings_store, tmp_path, qapp):
    """Switch to the marked language and back, in the editor and on the start screen: every
    text changes, and back in Ukrainian everything is exactly as before."""
    import shutil

    from nightexpress.app import build_app
    from nightexpress.controllers.language_controller import switch_language

    project = tmp_path / "Blink"
    reference = Path(__file__).resolve().parents[2] / "reference" / "blinky_f103c6"
    shutil.copytree(reference, project, ignore=shutil.ignore_patterns("target", "*.bin"))
    settings_store.set_projects_directory(tmp_path)
    settings_store.set_recent_projects([project])
    settings_store.set_open_last_project(False)
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    window = controller.window
    dispose(window)
    controller.start()
    marked = write_marked_catalog(tmp_path / "locales")

    def qt_translation(language: str) -> None:
        install_qt_translation(qapp, language)

    def settle() -> set[str]:
        qapp.processEvents()
        # Rebuilt rows replace the old ones, which go at the next turn of the event loop.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return set(all_shown_texts([window]))

    def round_trip() -> None:
        before = settle()
        assert any(CYRILLIC.search(text) for text in before)
        try:
            switch_language("en", qt_translation, [window], marked)
            settle()
            assert untranslated_in([window]) == []
        finally:
            switch_language(i18n.SOURCE_LANGUAGE, qt_translation, [window])
        after = settle()
        assert not [t for t in after if MARK in t]
        assert sorted(after ^ before) == []

    round_trip()  # the start screen
    controller.projects.open_project(project)
    controller.projects.show_settings()  # the settings dialog is a child of the window
    round_trip()  # the editor
    # States shown by methods: a running upload, problem counts, the first-run tips.
    window.set_busy(True, "upload")
    window.set_problem_counts(2, 1)
    window.start_first_run_tour()
    window.show_result(lambda: i18n._("Зібрано за {seconds} с").format(seconds=3), True)
    tools = ToolchainDialog(window)  # kept by its controller between openings
    tools.set_statuses(
        [
            ToolStatus("cargo", True, version="1.90.0", path=Path("/bin/cargo")),
            ToolStatus("probe-rs", False, required=False, install_command="cargo install x"),
        ]
    )
    tools.show()
    round_trip()
