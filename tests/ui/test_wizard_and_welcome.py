from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from nightexpress.models.entities import NewProjectRequest, RecentProject, ToolStatus
from nightexpress.models.entities.font import FontArea
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.views.dialogs.board_chooser import BoardChooserDialog
from nightexpress.views.dialogs.new_project_wizard import NewProjectWizard
from nightexpress.views.theme import palette
from nightexpress.views.welcome_view import HOME, PROJECTS, SETTINGS, WelcomeView

pytestmark = pytest.mark.ui

CATALOG = BoardCatalog.load()
CHIPS = {c.id: c for c in CATALOG.chips}


def make_wizard(qtbot, location: Path) -> NewProjectWizard:
    wizard = NewProjectWizard(
        None,
        CATALOG.boards,
        CHIPS,
        location,
        search=CATALOG.search,
        examples_for=lambda board_id: list(CATALOG.examples_for(board_id)),
        preview=lambda request: [f"{request.name}/Cargo.toml", "src/main.rs"],
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def test_name_page_validation(qtbot, tmp_path: Path) -> None:
    wizard = make_wizard(qtbot, tmp_path)
    page = wizard.name_page
    assert page.isComplete()  # the default name in an empty folder
    assert page.path_label.text().startswith("Буде створено: ")
    assert page.path_label.toolTip() == str(tmp_path / "my_project")

    page.name_edit.setText("мій проєкт")
    assert not page.isComplete() and "латинської" in page.error_label.text()

    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "file").write_text("x")
    page.name_edit.setText("taken")
    assert not page.isComplete() and "не порожня" in page.error_label.text()

    page.name_edit.setText("ok_name")
    page.location_edit.setText("")
    assert not page.isComplete()


def test_name_page_survives_unreadable_locations(qtbot, tmp_path: Path) -> None:
    wizard = make_wizard(qtbot, tmp_path)
    page = wizard.name_page
    page.name_edit.setText("locked")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        page.location_edit.setText(str(tmp_path) + " ")  # re-run the check
        assert not page.isComplete() and "недоступна" in page.error_label.text()
    finally:
        locked.chmod(0o700)

    page.location_edit.setText("~no_such_user_here/projects")  # typed character by character
    assert not page.isComplete() and "не знайдено" in page.error_label.text()
    assert page.path_label.text() == ""


def test_full_wizard_flow_emits_request(qtbot, tmp_path: Path) -> None:
    wizard = make_wizard(qtbot, tmp_path)
    wizard.name_page.name_edit.setText("my_blink")
    wizard.next()
    picker = wizard.board_page.picker
    picker.search_edit.setText("F103C6T6A")  # search by the marking on the chip
    assert picker.current_board_id == "stm32f103c6-minimal"
    assert "32 KB flash" in picker.details.text()
    picker.search_edit.setText("ATmega328")
    assert picker.current_board_id is None and not wizard.board_page.isComplete()
    picker.search_edit.setText("")
    wizard.next()

    page = wizard.example_page
    assert [page.examples.item(i).text() for i in range(page.examples.count())] == [
        "Blank Project",
        "Blink",
        "Serial Hello",
        "Button",
        "RTT Hello",
    ]
    page.examples.setCurrentRow(1)
    assert page.files.item(0).text() == "my_blink/Cargo.toml"

    with qtbot.waitSignal(wizard.create_requested) as blocker:
        wizard.accept()
    request: NewProjectRequest = blocker.args[0]
    assert request == NewProjectRequest("my_blink", tmp_path, "stm32f103c6-minimal", "blink")
    assert wizard.isVisible()  # stays open until the controller confirms

    wizard.show_error("Тека зайнята")
    assert page.error_label.text() == "Тека зайнята"
    wizard.accept_created()
    assert not wizard.isVisible()


def test_short_template_list_fits_in_compact_wizard(qtbot, tmp_path: Path) -> None:
    wizard = make_wizard(qtbot, tmp_path)
    wizard.setStyleSheet(palette.build_stylesheet(palette.current()))
    wizard.next()
    wizard.board_page.picker.select("stm32f103c6-minimal")
    wizard.next()
    wizard.resize(wizard.width(), 500)

    templates = wizard.example_page.examples
    assert templates.count() == 5
    assert templates.verticalScrollBar().maximum() == 0
    assert templates.viewport().rect().contains(templates.visualItemRect(templates.item(4)))


def test_board_chooser(qtbot) -> None:
    dialog = BoardChooserDialog("Виберіть плату для проєкту", CATALOG.boards, CHIPS, CATALOG.search)
    qtbot.addWidget(dialog)
    assert dialog.board_id == "stm32f103c6-minimal"
    dialog.picker.search_edit.setText("nucleo-f401re")
    assert dialog.board_id == "nucleo-f401re"  # found by its nickname
    dialog.picker.search_edit.setText("esp32")
    assert dialog.board_id is None
    assert not dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled()


def test_welcome_navigation_and_cards(qtbot) -> None:
    view = WelcomeView()
    qtbot.addWidget(view)
    view.show()
    assert view.current_page == HOME
    for key in (PROJECTS, SETTINGS, HOME):
        view.nav_buttons[key].click()
        assert view.current_page == key

    with qtbot.waitSignal(view.new_project_requested, timeout=1000):
        qtbot.mouseClick(view.home.new_card, Qt.MouseButton.LeftButton)
    with qtbot.waitSignal(view.open_project_requested, timeout=1000):
        qtbot.mouseClick(view.home.open_card, Qt.MouseButton.LeftButton)
    with qtbot.waitSignal(view.new_project_requested, timeout=1000):
        view.projects.new_button.click()


def test_recent_project_cards(qtbot, tmp_path: Path) -> None:
    view = WelcomeView()
    qtbot.addWidget(view)
    view.show()
    assert view.home.recent.empty.isVisible()
    now = datetime.now()
    projects = [
        RecentProject(tmp_path / f"p{i}", f"p{i}", "STM32F103C6T6A", now - timedelta(days=i))
        for i in range(5)
    ]
    view.set_recent_projects(projects)
    home_cards = view.home.recent.cards
    assert [c.title.text() for c in home_cards] == ["p0", "p1", "p2"]  # the start page shows 3
    assert len(view.projects.list.cards) == 5  # the projects page shows all
    assert home_cards[0].right.text() == "STM32F103C6T6A  ·  відкрито сьогодні"
    assert home_cards[1].right.text().endswith("учора")
    with qtbot.waitSignal(view.open_recent_requested) as blocker:
        qtbot.mouseClick(home_cards[1], Qt.MouseButton.LeftButton)
    assert blocker.args == [tmp_path / "p1"]


def test_environment_card(qtbot) -> None:
    view = WelcomeView()
    qtbot.addWidget(view)
    assert view.home.environment.header.text() == "Перевіряю середовище…"
    ready = view.set_tool_statuses(
        [
            ToolStatus("rustup", True, "1.28.2"),
            ToolStatus("rustc", True, "1.95.0"),
            ToolStatus("cargo", True, "1.95.0"),
            ToolStatus("probe-rs", True, "0.32.0", required=False),
            ToolStatus("rust-analyzer", False, install_hint="rustup component add rust-analyzer",
                       required=False),
        ]
    )  # fmt: skip
    assert ready
    assert view.home.environment.header.text() == "Середовище готове"
    _mark, text = view.home.environment.rows["rust-analyzer"]
    assert "необов'язково" in text.text()  # not a warning: nothing uses it yet
    assert "після MVP" in text.toolTip()

    assert not view.set_tool_statuses([ToolStatus("cargo", False, install_hint="rustup.rs")])
    assert view.home.environment.header.text() == "Потрібне налаштування"

    with qtbot.waitSignal(view.tools_dialog_requested, timeout=1000):
        view.home.environment.check_link.click()  # "Check the setup" opens the dialog


def test_settings_page(qtbot, tmp_path: Path) -> None:
    view = WelcomeView()
    qtbot.addWidget(view)
    view.set_projects_directory(tmp_path)
    assert view.settings.projects_dir.text() == str(tmp_path)
    view.settings.set_font_sizes({FontArea.EDITOR: 16}, single=True)
    with qtbot.waitSignal(view.settings.font_size_changed) as blocker:
        view.settings.font_sizes[FontArea.EDITOR].setValue(18)
    assert blocker.args == [FontArea.EDITOR, 18]
    with qtbot.waitSignal(view.change_projects_directory_requested, timeout=1000):
        view.settings.change_dir.click()


def test_wizard_shows_steps_and_destination(qtbot, tmp_path: Path) -> None:
    wizard = make_wizard(qtbot, tmp_path)
    steps = wizard.steps.labels
    assert [label.text() for label in steps] == [
        "Назва й розташування",
        "Плата",
        "Шаблон",
    ]
    assert [label.property("state") for label in steps] == ["current", "", ""]
    wizard.next()
    wizard.next()
    assert wizard.current_step == 2
    assert [label.property("state") for label in steps] == ["done", "done", "current"]
    page = wizard.example_page
    assert page.destination.text().startswith("Буде створено в ")
    assert page.destination.toolTip() == str(tmp_path / "my_project")


def test_start_screen_examples(qtbot) -> None:
    view = WelcomeView()
    qtbot.addWidget(view)
    view.show()
    assert not view.home.examples_title.isVisible()  # nothing to show yet
    view.set_examples(CATALOG.examples)
    cards = view.home.example_cards
    assert [card.title.text() for card in cards] == ["Blink", "Serial Hello", "Button", "RTT Hello"]
    assert view.home.examples_title.isVisible()
    with qtbot.waitSignal(view.example_requested, timeout=1000) as blocker:
        qtbot.mouseClick(cards[1], Qt.MouseButton.LeftButton)
    assert blocker.args == ["serial_hello"]
    view.set_examples(CATALOG.examples)  # rebuilt, not appended
    assert len(view.home.example_cards) == 4


def test_recent_projects_menu_and_missing_folders(qtbot, tmp_path: Path) -> None:
    view = WelcomeView()
    qtbot.addWidget(view)
    present = RecentProject(tmp_path / "blink", "blink", "STM32F103C6T6A")
    gone = RecentProject(tmp_path / "gone", "gone", missing=True)
    view.set_recent_projects([present, gone])
    ok_card, missing_card = view.home.recent.cards
    assert missing_card.property("missing") and missing_card.right.text() == "не знайдено"
    assert not ok_card.property("missing")

    menu = view.home.recent.menu_for(gone)
    open_action, reveal_action, _sep, remove_action = menu.actions()
    assert not open_action.isEnabled() and not reveal_action.isEnabled()
    with qtbot.waitSignal(view.remove_recent_requested, timeout=1000) as removed:
        remove_action.trigger()
    assert removed.args == [gone.path]

    menu = view.home.recent.menu_for(present)
    with qtbot.waitSignal(view.reveal_recent_requested, timeout=1000) as revealed:
        menu.actions()[1].trigger()
    assert revealed.args == [present.path]

    view.set_recent_projects([])
    assert "Новий проєкт" in view.home.recent.empty.text()


def test_wizard_gitignore_option_and_board_details(qtbot, tmp_path: Path) -> None:
    wizard = make_wizard(qtbot, tmp_path)
    seen: list[bool] = []
    wizard.preview = lambda request: seen.append(request.create_gitignore) or ["Cargo.toml"]
    wizard.next()
    # The board step describes the board itself, not what is wired to it or how it is flashed.
    details = wizard.board_page.picker.details.text()
    assert "KB flash" in details
    for word in ("Світлодіод", "Кнопка", "Прошивання"):
        assert word not in details
    wizard.next()
    page = wizard.example_page
    assert page.gitignore.isChecked() and wizard.request().create_gitignore
    page.gitignore.click()
    assert not wizard.request().create_gitignore and seen[-1] is False  # the preview follows
    with qtbot.waitSignal(wizard.create_requested) as blocker:
        wizard.accept()
    assert blocker.args[0].create_gitignore is False
