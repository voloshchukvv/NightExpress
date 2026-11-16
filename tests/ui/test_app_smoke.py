import shutil
from itertools import pairwise
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from nightexpress import APP_NAME, __version__
from nightexpress.app import build_app
from nightexpress.models.entities import BuildProfile
from nightexpress.models.entities.font import FontArea
from nightexpress.models.state import Screen
from nightexpress.views.theme import palette

pytestmark = [pytest.mark.ui, pytest.mark.usefixtures("no_debug_probes")]

REFERENCE = Path(__file__).resolve().parents[2] / "reference" / "blinky_f103c6"


def test_main_window_starts_on_welcome_screen(dispose, settings_store) -> None:
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    dispose(controller.window)

    controller.start()

    window = controller.window
    assert window.isVisible()
    assert window.windowTitle() == APP_NAME
    assert window.version_text == f"{APP_NAME} {__version__}"
    assert window.current_screen is Screen.WELCOME
    assert window.toolbar.current_board_id == "stm32f103c6-minimal"


def test_dark_theme_applied(dispose, settings_store) -> None:
    qapp, controller = build_app([], settings_store, check_tools_on_start=False)
    dispose(controller.window)

    highlight = qapp.palette().color(QPalette.ColorRole.Highlight)
    assert highlight == QColor(palette.ACCENT)
    # With a stylesheet Qt wraps Fusion in a proxy style, so check the stylesheet instead.
    assert 'QPushButton[buttonRole="primary"]' in qapp.styleSheet()


def test_editor_toolbar_fits_at_minimum_width(qtbot, dispose, settings_store) -> None:
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    window = controller.window
    dispose(window)
    toolbar = window.toolbar
    path = Path("/work/blink")
    toolbar.set_projects([(path, "Довга назва проєкту зі STM32 " * 5)], path)
    toolbar.set_targets(["thumbv7m-none-eabi"])
    window.set_screen(Screen.EDITOR)
    window.show()

    controls = [
        toolbar.project_combo,
        toolbar.board_combo,
        toolbar.target_combo,
        toolbar.profile_combo,
        toolbar.verify_button,
        toolbar.upload_button,
        toolbar.connection_button,
    ]
    for width, show_target in ((1100, False), (1440, True), (1100, False)):
        window.resize(width, 700)
        qtbot.waitUntil(lambda expected=show_target: toolbar.target_combo.isVisible() == expected)
        rects = [control.geometry() for control in controls if control.isVisible()]
        assert all(toolbar.rect().contains(rect) for rect in rects)
        assert all(left.right() < right.left() for left, right in pairwise(rects))


def test_settings_page_and_font_size(qtbot, dispose, settings_store) -> None:
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    dispose(controller.window)
    controller.start()
    window = controller.window
    assert window.welcome.settings.font_sizes[FontArea.EDITOR].value() == 14

    window.settings_requested.emit()
    assert window.welcome.current_page == "settings"
    window.welcome.settings.font_sizes[FontArea.EDITOR].setValue(17)
    assert settings_store.font_size() == 17

    controller._app.set_screen(Screen.EDITOR)  # screens always change through the model
    assert window.current_screen is Screen.EDITOR
    qtbot.mouseClick(window.header.home_button, Qt.MouseButton.LeftButton)
    assert window.current_screen is Screen.WELCOME
    assert not window.header.home_button.isEnabled()


def test_project_settings_preserve_editor_context(qtbot, dispose, settings_store, tmp_path) -> None:
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    window = controller.window
    dispose(window)
    controller.start()
    window.confirm_close = None  # the test deliberately leaves an unsaved buffer
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        '[package]\nname = "demo"\n'
        '[package.metadata.nightexpress]\nboard = "stm32f103c6-minimal"\n',
        encoding="utf-8",
    )
    source = tmp_path / "src" / "main.rs"
    source.parent.mkdir()
    source.write_text("fn main() {}\n", encoding="utf-8")
    assert controller.projects.open_project(tmp_path)
    assert controller.projects.open_file(manifest)
    assert controller.projects.open_file(source)
    window.editor_tabs.editor._on_content_changed(str(source), "fn main() { loop {} }\n", 1)

    window.actions_by_name["settings"].trigger()
    dialog = window.settings_dialog
    assert dialog.isVisible()
    assert dialog.tabs.currentIndex() == 0
    assert dialog.board_name.text() == "STM32F103C6T6A"
    assert dialog.target_triple.text() == "thumbv7m-none-eabi"
    assert window.current_screen is Screen.EDITOR and window.editor_page.isVisible()

    profile = dialog.profile_combo
    profile.setCurrentIndex(profile.findData(BuildProfile.DEV))
    profile.activated.emit(profile.currentIndex())
    assert window.toolbar.current_profile is BuildProfile.DEV
    assert settings_store.project_build_profile(tmp_path) is BuildProfile.DEV

    dialog.tabs.setCurrentIndex(1)
    dialog.application.font_sizes[FontArea.EDITOR].setValue(17)
    assert settings_store.font_size() == 17
    assert window.welcome.settings.font_sizes[FontArea.EDITOR].value() == 17
    target = tmp_path / "projects"
    window.ask_directory = lambda *_args: target
    dialog.application.change_dir.click()
    assert settings_store.projects_directory() == target
    assert window.welcome.settings.projects_dir.text() == str(target)
    assert dialog.application.projects_dir.text() == str(target)
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert window.current_screen is Screen.EDITOR
    assert window.editor_tabs.paths == (source, manifest)
    assert window.editor_tabs.current_path == source
    assert window.editor_tabs.text(source) == "fn main() { loop {} }\n"
    assert controller.projects._project.is_dirty(source)

    window.actions_by_name["settings"].trigger()
    assert dialog.tabs.currentIndex() == 0
    assert dialog.profile_combo.currentData() is BuildProfile.DEV
    dialog.reject()


def test_font_size_shortcuts(dispose, settings_store) -> None:
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    dispose(controller.window)
    controller.start()
    window = controller.window
    window.font_size_step_requested.emit(FontArea.EDITOR, 1)
    assert settings_store.font_size() == 15
    assert (
        window.welcome.settings.font_sizes[FontArea.EDITOR].value() == 15
    )  # the settings page follows
    window.font_size_step_requested.emit(FontArea.EDITOR, 0)
    assert settings_store.font_size() == 14
    for _ in range(30):
        window.font_size_step_requested.emit(FontArea.EDITOR, 1)
    assert settings_store.font_size() == 28  # clamped


def test_window_layout_and_last_project_survive_restart(
    qtbot, dispose, settings_store, tmp_path
) -> None:
    project = tmp_path / "blink"
    shutil.copytree(REFERENCE, project, ignore=shutil.ignore_patterns("target", "*.bin"))
    _, first = build_app([], settings_store, check_tools_on_start=False)
    dispose(first.window)
    first.start()
    assert first.window.current_screen is Screen.WELCOME  # no recent project yet
    first.projects.open_project(project)
    first.window.resize(1300, 850)
    first.window.actions_by_name["toggle_project"].trigger()
    first.save_window_state()

    _, second = build_app([], settings_store, check_tools_on_start=False)
    dispose(second.window)
    second.start()
    window = second.window
    assert window.current_screen is Screen.EDITOR  # the last project reopened
    assert window.windowTitle().startswith("blink")
    assert not window.actions_by_name["toggle_project"].isChecked()
    # The geometry was saved and applied. (The offscreen test screen is 800x800, so Qt clamps
    # it to the window minimum instead of 1300: compare with the default size, not exactly.)
    assert settings_store.window_geometry() is not None
    assert window.width() != 1536

    settings_store.set_open_last_project(False)
    _, third = build_app([], settings_store, check_tools_on_start=False)
    dispose(third.window)
    third.start()
    assert third.window.current_screen is Screen.WELCOME


def test_option_checkboxes_are_shared(dispose, settings_store) -> None:
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    dispose(controller.window)
    controller.start()
    welcome = controller.window.welcome.settings
    dialog = controller.window.settings_dialog.application
    assert welcome.options["save_before_build"].isChecked()
    assert not welcome.options["format_on_save"].isChecked()
    welcome.options["format_on_save"].setChecked(True)
    assert settings_store.format_on_save()
    assert dialog.options["format_on_save"].isChecked()  # the other copy follows
    dialog.options["open_last_project"].setChecked(False)
    assert not settings_store.open_last_project()
    assert not welcome.options["open_last_project"].isChecked()


def test_popups_are_round(qtbot, dispose, settings_store) -> None:
    """Menus and combo lists get a translucent window so the rounded frame is their shape."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox, QMenu, QStyle

    _, controller = build_app([], settings_store, check_tools_on_start=False)
    dispose(controller.window)
    combo = QComboBox()
    qtbot.addWidget(combo)
    combo.addItems(["Текст", "HEX"])
    combo.show()
    combo.showPopup()
    container = combo.view().window()
    assert container.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    # The open box and its list are one shape: flush, at least as wide, square where they meet.
    direction = combo.property("popupDirection")
    assert direction in ("down", "up") and combo.view().property("popupDirection") == direction
    top_left = combo.mapToGlobal(combo.rect().topLeft())
    # The list is exactly as wide as a box that fits its rows: same geometry, not a wider list.
    assert container.x() == top_left.x() and container.width() == combo.width()
    assert not combo.view().property("popupWider")
    if direction == "down":
        assert container.y() == top_left.y() + combo.height()
    else:
        assert container.geometry().bottom() == top_left.y() - 1
    # No system shadow (a second, darker outline); rows are never cut short to "…".
    assert container.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    assert combo.view().viewport().width() >= combo.view().sizeHintForColumn(0)
    # The chosen row is ticked by the delegate; a click leaves no focus outline on the box.
    assert isinstance(combo.view().itemDelegate(), palette.CurrentItemDelegate)
    assert combo.focusPolicy() == Qt.FocusPolicy.TabFocus
    combo.hidePopup()
    assert combo.property("popupDirection") is None  # the box gets its round corners back
    # A list below the box, not the menu-style popup laid over it.
    assert not combo.style().styleHint(QStyle.StyleHint.SH_ComboBox_Popup, None, combo)

    # A row longer than a narrow box: the list is wider and says so (its free corner is round).
    combo.setFixedWidth(combo.width())
    combo.addItem("usbserial-A50285BI — FT232R USB UART")
    combo.showPopup()
    assert combo.view().window().width() > combo.width()
    assert combo.view().property("popupWider") is True
    assert combo.view().viewport().width() >= combo.view().sizeHintForColumn(0)
    combo.hidePopup()

    menu = QMenu()
    qtbot.addWidget(menu)
    menu.addAction("x")
    menu.ensurePolished()
    assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_one_font_size_for_everything_or_one_for_each_area(dispose, settings_store) -> None:
    _, controller = build_app([], settings_store, check_tools_on_start=False)
    dispose(controller.window)
    controller.start()
    window = controller.window
    on_welcome = window.welcome.settings
    in_dialog = window.settings_dialog.application
    uart = window.uart_panel.output
    logs = window.output_panel

    def saved() -> list[int]:
        return [settings_store.font_size(area) for area in FontArea]

    def shown(panel) -> list[FontArea]:
        return [area for area, box in panel.font_sizes.items() if not box.isHidden()]

    # One size for everything: a single box, and every area follows it.
    assert on_welcome.single_font_size.isChecked() and shown(on_welcome) == [FontArea.EDITOR]
    on_welcome.font_sizes[FontArea.EDITOR].setValue(16)
    assert saved() == [16, 16, 16, 16]
    assert uart.font().pointSize() == logs.font().pointSize() == 15  # a point less, by eye
    window.font_size_step_requested.emit(FontArea.UART, 1)  # the keys change the common size
    assert saved() == [17, 17, 17, 17]

    # A size for each area: four boxes in both copies of the settings, nothing jumps yet.
    in_dialog.single_font_size.setChecked(False)
    assert not settings_store.single_font_size() and not on_welcome.single_font_size.isChecked()
    assert shown(on_welcome) == shown(in_dialog) == list(FontArea)
    assert saved() == [17, 17, 17, 17]
    in_dialog.font_sizes[FontArea.UART].setValue(11)
    assert saved() == [17, 17, 11, 17]
    assert on_welcome.font_sizes[FontArea.UART].value() == 11
    assert uart.font().pointSize() == 10 and logs.font().pointSize() == 16
    window.font_size_step_requested.emit(FontArea.LOGS, 1)  # the keys: the focused area only
    window.font_size_step_requested.emit(FontArea.TERMINAL, 0)  # Cmd+0: its default size
    assert saved() == [17, 14, 11, 18]

    # Back to one size: everything comes together at the editor's.
    on_welcome.single_font_size.setChecked(True)
    assert saved() == [17, 17, 17, 17] and shown(in_dialog) == [FontArea.EDITOR]
    assert uart.font().pointSize() == logs.font().pointSize() == 16

    # The choice survives a restart.
    in_dialog.single_font_size.setChecked(False)
    in_dialog.font_sizes[FontArea.TERMINAL].setValue(20)
    _, second = build_app([], settings_store, check_tools_on_start=False)
    dispose(second.window)
    second.start()
    again = second.window.welcome.settings
    assert not again.single_font_size.isChecked()
    assert [again.font_sizes[area].value() for area in FontArea] == [17, 20, 17, 17]


def test_qt_dialog_buttons_follow_the_language(qtbot):
    """The texts Qt draws itself (standard buttons) come from Qt's translation."""
    from PySide6.QtWidgets import QApplication, QDialogButtonBox

    from nightexpress.app import install_qt_translation

    def cancel_text() -> str:
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        qtbot.addWidget(box)
        return box.button(QDialogButtonBox.StandardButton.Cancel).text().replace("&", "")

    qapp = QApplication.instance()
    try:
        assert install_qt_translation(qapp, "uk")
        assert cancel_text() == "Скасувати"
        assert install_qt_translation(qapp, "de")  # the previous translation is replaced
        assert cancel_text() == "Abbrechen"
        assert not install_qt_translation(qapp, "xx")  # Qt does not know it: English
        assert cancel_text() == "Cancel"
    finally:
        install_qt_translation(qapp, "uk")  # what build_app sets in the other tests


def test_the_application_starts_in_english(qtbot, dispose, settings_store) -> None:
    import re

    from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel

    from nightexpress import i18n
    from nightexpress.app import install_qt_translation

    settings_store.set_language("en")
    try:
        _, controller = build_app([], settings_store, check_tools_on_start=False)
        window = controller.window
        dispose(window)
        controller.start()

        assert i18n.current() == "en"
        assert window.toolbar.verify_button.text() == "Build"
        assert window.toolbar.upload_button.text() == "Build and flash"
        settings = window.welcome.settings
        names = [
            settings.language_combo.itemText(i) for i in range(settings.language_combo.count())
        ]
        languages = ["Українська", "Deutsch", "English", "Español", "Français"]
        assert names == ["Same as system", *languages]
        assert settings.language_combo.currentText() == "English"
        assert settings.theme_combo.itemText(1) == "Graphite"
        texts = [
            widget.text()
            for widget in window.findChildren(QLabel) + window.findChildren(QAbstractButton)
        ]
        cyrillic = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")
        assert "Welcome to NightExpress" in texts
        assert [text for text in texts if cyrillic.search(text)] == []
    finally:
        install_qt_translation(QApplication.instance(), "uk")  # as in the other tests
