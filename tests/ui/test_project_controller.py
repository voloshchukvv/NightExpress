"""ProjectController end to end with the real main window; dialogs are replaced by stubs."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from PySide6.QtWebEngineCore import QWebEngineScript

from nightexpress.controllers.examples_controller import ExamplesController
from nightexpress.controllers.project_controller import ProjectController, free_project_name
from nightexpress.models.entities import NewProjectRequest, UploadMethod
from nightexpress.models.state import AppModel, ProjectModel, Screen
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.file_service import FileService
from nightexpress.views.editor.monaco_editor import monaco_installed
from nightexpress.views.main_window import MainWindow, SaveChoice

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not monaco_installed(), reason="run scripts/fetch_monaco.py first"),
]

REFERENCE = Path(__file__).resolve().parents[2] / "reference" / "blinky_f103c6"


class Env:
    def __init__(self, qtbot, dispose, settings_store) -> None:
        self.window = MainWindow("NightExpress", "0.1.0")
        dispose(self.window)
        self.app = AppModel()
        self.app.screen_changed.connect(self.window.set_screen)
        self.project = ProjectModel()
        self.settings = settings_store
        self.errors: list[tuple[str, str]] = []
        self.save_choice = SaveChoice.CANCEL
        self.board_answer: str | None = "stm32f103c6-minimal"
        self.questions: list[list[str]] = []
        self.window.show_error = lambda title, text: self.errors.append((title, text))
        self.window.ask_save_changes = self._ask_save
        self.window.ask_board = lambda *args, **kwargs: self.board_answer
        self.confirm_answer = True
        self.confirmations: list[tuple[str, str]] = []
        self.window.ask_confirm = self._ask_confirm
        self.toasts: list[tuple[str, str]] = []
        self.controller = ProjectController(
            self.window, self.app, self.project, BoardCatalog.load(), self.settings
        )

    def _ask_confirm(self, title: str, text: str, confirm_text: str, details: str = "") -> bool:
        self.confirmations.append((text, details))
        return self.confirm_answer

    def _ask_save(self, names) -> SaveChoice:
        self.questions.append(list(names))
        return self.save_choice

    def type_text(self, path: Path, text: str) -> None:
        """Simulate the Monaco bridge reporting a user edit."""
        self.window.editor_tabs.editor._on_content_changed(str(path), text, 1)


@pytest.fixture
def env(qtbot, dispose, settings_store) -> Env:
    return Env(qtbot, dispose, settings_store)


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    target = tmp_path / "blinky"
    shutil.copytree(REFERENCE, target, ignore=shutil.ignore_patterns("target", "*.bin"))
    return target


@pytest.mark.parametrize("example_id", [None, "blink"], ids=["blank", "blink"])
def test_new_project_through_wizard(env: Env, tmp_path: Path, example_id: str | None) -> None:
    env.settings.set_projects_directory(tmp_path)
    occupied = tmp_path / "my_project"
    occupied.mkdir()
    (occupied / "Cargo.toml").touch()
    wizard = env.controller.new_project()
    assert wizard.name_page.location_edit.text() == str(tmp_path)
    assert wizard.name_page.name_edit.text() == "my_project_2"

    name = f"my_{example_id or 'blank'}"
    wizard.name_page.name_edit.setText(name)
    wizard.next()
    wizard.next()
    page = wizard.example_page
    if example_id is not None:
        page.examples.setCurrentRow(1)
    assert wizard.request() == NewProjectRequest(name, tmp_path, "stm32f103c6-minimal", example_id)
    preview = {page.files.item(i).text() for i in range(page.files.count())}
    assert {
        "Cargo.toml",
        ".cargo/config.toml",
        "rust-toolchain.toml",
        "src/main.rs",
        "README.md",
    } <= preview
    wizard.button(wizard.WizardButton.FinishButton).click()

    project = tmp_path / name
    assert {
        path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()
    } == preview
    source = (project / "src" / "main.rs").read_text(encoding="utf-8")
    assert ("led.toggle()" in source) == (example_id == "blink")
    assert not wizard.isVisible()
    assert env.project.config is not None and env.project.config.name == name
    assert env.project.active_file == project / "src" / "main.rs"
    assert env.window.editor_tabs.paths == (project / "src" / "main.rs",)
    assert env.app.recent_projects == (project,)
    assert env.settings.recent_projects() == [project]
    assert env.window.current_screen is Screen.EDITOR
    assert env.window.windowTitle() == f"{name} — NightExpress"
    assert env.window.target_view.chip_title.text() == "STM32F103C6T6A"
    assert env.window.toolbar.target_combo.currentText() == "thumbv7m-none-eabi"


def test_free_project_name(tmp_path: Path) -> None:
    assert free_project_name("blink", tmp_path) == "blink"
    (tmp_path / "blink").mkdir()  # an empty folder can still be used
    assert free_project_name("blink", tmp_path) == "blink"
    (tmp_path / "blink" / "Cargo.toml").write_text("")
    (tmp_path / "blink_2").write_text("a file, not a folder")
    assert free_project_name("blink", tmp_path) == "blink_3"


def test_examples_menu_creates_project_from_example(qtbot, env: Env, tmp_path: Path) -> None:
    env.settings.set_projects_directory(tmp_path)
    ExamplesController(env.window, env.project, BoardCatalog.load(), env.controller)
    menu = env.window.examples_menu
    # No project: every example, not filtered by a board.
    examples = ["Blink…", "Serial Hello…", "Button…", "RTT Hello…"]
    assert [a.text() for a in menu.actions()] == examples

    (tmp_path / "serial_hello").mkdir()
    (tmp_path / "serial_hello" / "keep.txt").write_text("x")
    menu.actions()[1].trigger()
    wizard = env.controller.wizard
    assert wizard is not None and wizard.isVisible()
    assert wizard.name_page.name_edit.text() == "serial_hello_2"  # the folder is taken
    wizard.next()
    wizard.next()
    assert wizard.example_page.current_example.id == "serial_hello"
    request = wizard.request()
    assert request is not None and request.example_id == "serial_hello"

    env.controller._create(request)
    project = tmp_path / "serial_hello_2"
    assert "Uart::new_blocking" in (project / "src" / "main.rs").read_text()
    assert env.project.config is not None
    # A project is open: the menu shows the examples for its board.
    header = menu.actions()[0]
    assert header.text().startswith("Для плати: STM32F103C6") and not header.isEnabled()


def test_wizard_shows_generation_errors(env: Env, tmp_path: Path) -> None:
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "data.txt").write_text("x")
    wizard = env.controller.new_project()
    env.controller._create(NewProjectRequest("busy", tmp_path, "stm32f103c6-minimal", "blink"))
    assert "не порожня" in wizard.example_page.error_label.text()
    assert env.project.config is None


def test_wizard_preview_lists_template_files(env: Env, tmp_path: Path) -> None:
    preview = env.controller._preview(
        NewProjectRequest("p", tmp_path, "stm32f103c6-minimal", "blink")
    )
    assert "src/main.rs" in preview and ".cargo/config.toml" in preview


def test_open_existing_project(env: Env, project_copy: Path) -> None:
    assert env.controller.open_project(project_copy)
    assert env.project.config is not None
    assert env.project.config.upload_method is UploadMethod.PROBE_RS
    assert env.window.editor_tabs.current_path == project_copy / "src" / "main.rs"
    assert env.window.file_tree.root == project_copy
    assert env.errors == []
    assert env.controller.open_project(project_copy)  # same project again: no reopen
    assert env.window.editor_tabs.paths == (project_copy / "src" / "main.rs",)


def test_open_plain_cargo_project_asks_for_board(env: Env, tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n")
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "plain"\nversion = "0.1.0"\n')

    env.board_answer = None  # user cancels the board question
    assert not env.controller.open_project(tmp_path)
    assert "nightexpress" not in (tmp_path / "Cargo.toml").read_text()

    env.board_answer = "stm32f103c6-minimal"
    assert env.controller.open_project(tmp_path)
    assert 'board = "stm32f103c6-minimal"' in (tmp_path / "Cargo.toml").read_text()
    assert env.project.config is not None and env.project.config.board_id == "stm32f103c6-minimal"


def test_open_non_cargo_folder_shows_error(env: Env, tmp_path: Path) -> None:
    assert not env.controller.open_project(tmp_path)
    assert env.errors and "Cargo.toml" in env.errors[0][1]
    assert env.project.config is None


def test_missing_recent_project_is_forgotten(env: Env, tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    env.app.set_recent_projects([gone])
    assert not env.controller.open_project(gone)
    assert env.app.recent_projects == ()
    assert env.errors[0][0] == "Проєкт не знайдено"


def test_edit_save_and_dirty_markers(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    original = main.read_text()

    env.type_text(main, original + "// edited\n")
    assert env.project.is_dirty(main)
    assert env.window.editor_tabs.is_marked_dirty(main)

    env.type_text(main, original)  # typed back to the saved text
    assert not env.project.is_dirty(main)

    env.type_text(main, original + "// edited\n")
    env.window.save_requested.emit()
    assert main.read_text() == original + "// edited\n"
    assert not env.project.is_dirty(main)
    assert not env.window.editor_tabs.is_marked_dirty(main)


def test_save_markdown_from_preview(qtbot, env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    readme = project_copy / "README.md"
    assert env.controller.open_file(readme)
    markdown = "# Unsaved Markdown\n"
    env.type_text(readme, markdown)

    tabs = env.window.editor_tabs
    with qtbot.waitSignal(tabs.markdown_preview.loadFinished, timeout=10_000):
        tabs.markdown_toggle.click()
    with qtbot.waitCallback(timeout=5000) as result:
        tabs.markdown_preview.page().toPlainText(result)
    assert result.args[0].strip() == "Unsaved Markdown"
    assert tabs.is_marked_dirty(readme) and readme.read_text() != markdown

    env.window.save_requested.emit()
    assert readme.read_text() == markdown and not tabs.is_marked_dirty(readme)
    assert tabs.markdown_toggle.isChecked()


def test_markdown_project_link_opens_the_linked_heading(qtbot, env: Env, project_copy: Path):
    readme = project_copy / "README.md"
    guide = project_copy / "docs" / "UART Guide.md"
    guide.parent.mkdir()
    guide.write_text("# Guide\n\n" + "Paragraph.\n\n" * 100 + "## UART pins\n", encoding="utf-8")
    readme.write_text("[Wiring](docs/UART%20Guide.md#uart-pins)\n", encoding="utf-8")
    assert env.controller.open_project(project_copy)
    assert env.controller.open_file(readme)
    tabs = env.window.editor_tabs
    preview = tabs.markdown_preview
    env.window.show()
    with qtbot.waitSignal(preview.loadFinished, timeout=10_000):
        tabs.markdown_toggle.click()

    world = int(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    preview.page().runJavaScript("document.querySelector('a').click()", world)
    qtbot.waitUntil(lambda: tabs.current_path == guide.resolve(), timeout=5000)
    assert tabs.markdown_toggle.isChecked()
    with qtbot.waitCallback(timeout=5000) as result:
        preview.page().runJavaScript(
            "!!document.getElementById('uart-pins') && scrollY > 0", world, result
        )
    assert result.args == [True]


def test_open_file_from_tree_and_reject_binary(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    env.window.file_tree.file_activated.emit(project_copy / "Cargo.toml")
    assert env.window.editor_tabs.current_path == project_copy / "Cargo.toml"
    assert env.project.active_file == project_copy / "Cargo.toml"

    blob = project_copy / "firmware.bin"
    blob.write_bytes(b"\x00\xff\xfe\x80")
    env.window.file_tree.file_activated.emit(blob)
    assert env.errors and "UTF-8" in env.errors[-1][1]
    assert blob not in env.project.open_files


def test_close_dirty_tab_asks(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.type_text(main, "changed")

    env.save_choice = SaveChoice.CANCEL
    env.window.editor_tabs.tab_close_requested.emit(main)
    assert main in env.project.open_files
    assert env.questions == [["main.rs"]]

    env.save_choice = SaveChoice.DISCARD
    env.window.editor_tabs.tab_close_requested.emit(main)
    assert main not in env.project.open_files
    assert env.window.editor_tabs.paths == ()
    assert main.read_text() != "changed"


def test_close_project_with_unsaved_changes(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.type_text(main, "changed")

    env.save_choice = SaveChoice.CANCEL
    assert not env.controller.close_project()
    assert not env.controller.confirm_quit()
    assert env.project.is_open

    env.save_choice = SaveChoice.SAVE
    assert env.controller.close_project()
    assert main.read_text() == "changed"
    assert not env.project.is_open
    assert env.window.current_screen is Screen.WELCOME
    assert env.window.windowTitle() == "NightExpress"
    assert env.controller.confirm_quit()


def test_window_close_is_guarded(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    env.type_text(project_copy / "src" / "main.rs", "changed")
    env.window.show()
    env.save_choice = SaveChoice.CANCEL
    env.window.close()
    assert env.window.isVisible()


def test_new_file_inside_project(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    env.window.ask_text = lambda *args, **kwargs: "src/led.rs"
    env.window.new_file_requested.emit()
    assert (project_copy / "src" / "led.rs").is_file()
    assert env.window.editor_tabs.current_path == project_copy / "src" / "led.rs"

    env.window.ask_text = lambda *args, **kwargs: "../outside.rs"
    assert not env.controller.new_file()
    assert not (project_copy.parent / "outside.rs").exists()
    assert "всередині теки проєкту" in env.errors[-1][1]

    env.window.ask_text = lambda *args, **kwargs: "src/led.rs"
    assert not env.controller.new_file()
    assert "уже існує" in env.errors[-1][1]


def test_build_profile_is_remembered_per_project(env: Env, project_copy: Path) -> None:
    from nightexpress.models.entities import BuildProfile

    env.controller.open_project(project_copy)
    assert env.project.build_profile is BuildProfile.RELEASE
    env.window.toolbar.profile_selected.emit(BuildProfile.DEV)
    assert env.project.build_profile is BuildProfile.DEV
    assert env.settings.project_build_profile(project_copy) is BuildProfile.DEV

    env.controller.close_project()
    env.controller.open_project(project_copy)
    assert env.window.toolbar.current_profile is BuildProfile.DEV


def test_recent_projects_have_chip_and_date(env: Env, project_copy: Path, tmp_path: Path) -> None:
    env.controller.open_project(project_copy)
    env.app.add_recent_project(tmp_path / "gone")
    recent = {p.name: p for p in env.controller.recent_projects()}
    assert recent["blinky"].chip_label == "STM32F103C6T6A"
    assert recent["blinky"].opened_at is not None
    assert recent["gone"].chip_label is None  # unreadable projects are still listed


# --- project tree operations ---------------------------------------------------------------


@pytest.fixture
def trash(monkeypatch) -> list[Path]:
    """Deleting never touches the real trash in tests."""
    moved: list[Path] = []

    def move(self, path: Path) -> None:
        moved.append(path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    monkeypatch.setattr(FileService, "move_to_trash", move)
    return moved


def test_new_file_and_folder_from_the_tree(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    asked: list[str] = []

    def answer(text: str):
        def ask(title: str, label: str, prefill: str = "") -> str:
            asked.append(prefill)
            return prefill + text

        return ask

    env.window.ask_text = answer("drivers")
    env.window.file_tree.new_folder_requested.emit(project_copy / "src")
    assert (project_copy / "src" / "drivers").is_dir()

    env.window.ask_text = answer("led.rs")
    env.window.file_tree.new_file_requested.emit(project_copy / "src" / "drivers")
    assert env.window.editor_tabs.current_path == project_copy / "src" / "drivers" / "led.rs"

    env.window.ask_text = answer("notes.md")
    env.window.file_tree.new_file_requested.emit(project_copy)
    assert (project_copy / "notes.md").is_file()
    assert asked == ["src/", "src/drivers/", ""]

    env.window.ask_text = answer("bad?name")
    assert not env.controller.new_folder(project_copy)
    assert "не може містити" in env.errors[-1][1]


def test_rename_keeps_the_tab_and_its_unsaved_text(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    cargo = project_copy / "Cargo.toml"
    env.controller.open_file(cargo)
    env.controller.open_file(main)
    env.type_text(main, "// unsaved\n")
    on_disk = main.read_text()

    env.window.file_tree.rename_requested.emit(main, "app.rs")
    app = project_copy / "src" / "app.rs"
    assert not main.exists() and app.read_text() == on_disk
    assert set(env.project.open_files) == {cargo, app}
    assert env.project.active_file == app
    assert env.window.editor_tabs.text(app) == "// unsaved\n"
    assert env.project.dirty_files == {app}
    assert env.controller.save(app) and app.read_text() == "// unsaved\n"

    assert not env.controller.rename(app, "bad/name.rs")
    assert not env.controller.rename(cargo, "src")  # the name is taken
    assert "уже існує" in env.errors[-1][1]
    assert cargo.exists()


def test_rename_keeps_the_cursor_position(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.controller.open_file(main)
    env.window.editor_tabs.cursor_moved.emit(17, 9)  # Monaco reports the cursor of main.rs
    revealed: list[tuple[Path, int, int]] = []
    env.window.editor_tabs.reveal = lambda p, line, col=1: revealed.append((p, line, col))  # type: ignore[method-assign]
    assert env.controller.rename(main, "app.rs")
    app = project_copy / "src" / "app.rs"
    assert revealed == [(app, 17, 9)]
    assert env.controller.close_project()
    assert env.settings.project_session(project_copy)["cursors"] == {"src/app.rs": [17, 9]}


def test_rename_folder_moves_open_files(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    assert env.controller.rename(project_copy / "src", "source")
    moved = project_copy / "source" / "main.rs"
    assert moved.is_file() and not main.exists()
    assert env.project.open_files == (moved,)
    assert not env.controller.rename(project_copy, "other")  # never the project itself


def test_delete_asks_and_closes_tabs(env: Env, project_copy: Path, trash: list[Path]) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.type_text(main, "// unsaved\n")

    env.confirm_answer = False
    env.window.file_tree.delete_requested.emit(main)
    assert main.exists() and trash == []
    text, details = env.confirmations[-1]
    assert "src/main.rs" in text and "Незбережені зміни буде втрачено" in details

    env.confirm_answer = True
    assert env.controller.delete(project_copy / "src")  # a folder with an open file inside
    assert trash == [project_copy / "src"]
    assert env.project.open_files == () and not env.project.has_unsaved_changes
    assert env.window.editor_tabs.is_empty
    assert env.questions == []  # no second "save changes?" question

    assert not env.controller.delete(project_copy)


def test_close_other_and_all_tabs(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    cargo = project_copy / "Cargo.toml"
    readme = project_copy / "README.md"
    for path in (cargo, readme):
        env.controller.open_file(path)
    env.type_text(cargo, "changed")

    env.save_choice = SaveChoice.CANCEL
    env.window.editor_tabs.close_others_requested.emit(main)
    assert env.questions == [["Cargo.toml"]]  # one question for everything unsaved
    assert len(env.project.open_files) == 3

    env.save_choice = SaveChoice.DISCARD
    env.window.editor_tabs.close_others_requested.emit(main)
    assert env.project.open_files == (main,)
    assert env.window.editor_tabs.paths == (main,) and env.project.active_file == main

    env.window.editor_tabs.close_all_requested.emit()
    assert env.project.open_files == () and env.window.editor_tabs.is_empty


def test_close_tab_shortcut_closes_the_current_file(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    env.controller.open_file(project_copy / "Cargo.toml")
    env.window.actions_by_name["close_tab"].trigger()
    assert env.project.open_files == (project_copy / "src" / "main.rs",)


def test_quick_open_lists_project_files(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    (project_copy / "target" / "debug").mkdir(parents=True)
    (project_copy / "target" / "debug" / "junk.rs").write_text("")
    (project_copy / "firmware.bin").write_bytes(b"\0")
    (project_copy / "src" / "led.rs").write_text("")

    files = [p.relative_to(project_copy).as_posix() for p in env.controller.quick_open_files()]
    assert files[0] == "src/main.rs"  # open files first
    assert files[1] == "src/led.rs"  # then sources
    assert "Cargo.toml" in files
    assert not {"target/debug/junk.rs", "firmware.bin", "Cargo.lock"} & set(files)

    env.window.quick_open_requested.emit()
    dialog = env.window.quick_open
    assert dialog.isVisible() and dialog.shown_files[0] == "src/main.rs"
    dialog.query.setText("led")
    assert dialog.shown_files == ["src/led.rs"]
    dialog.accept_current()
    assert not dialog.isVisible()
    assert env.project.active_file == project_copy / "src" / "led.rs"


def _touch_later(path: Path) -> None:
    """Make the change visible even on file systems with coarse timestamps."""
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))


def test_external_change_is_reread_quietly(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.controller.check_external_changes()  # nothing changed: nothing happens
    assert env.window.activity_status.text() == ""

    main.write_text("// from git\n")
    _touch_later(main)
    env.controller.check_external_changes()
    assert env.window.editor_tabs.text(main) == "// from git\n"
    assert not env.project.has_unsaved_changes
    assert "main.rs оновлено з диска" in env.window.activity_status.text()
    assert env.window.toasts.toasts == ()


def test_own_save_is_not_an_external_change(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.type_text(main, "// mine\n")
    assert env.controller.save()
    env.controller.check_external_changes()
    assert env.window.activity_status.text() == "" and env.window.toasts.toasts == ()


def test_external_change_with_unsaved_edits_offers_to_reload(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.type_text(main, "// mine\n")
    main.write_text("// theirs\n")
    _touch_later(main)

    env.controller.check_external_changes()
    assert env.window.editor_tabs.text(main) == "// mine\n"  # nothing is lost silently
    (toast,) = env.window.toasts.toasts
    assert toast.title_text == "Файл змінено на диску" and "src/main.rs" in toast.body_text
    env.controller.check_external_changes()
    assert len(env.window.toasts.toasts) == 1  # asked once per change

    assert toast.action_button is not None
    toast.action_button.click()
    assert env.window.editor_tabs.text(main) == "// theirs\n"
    assert not env.project.has_unsaved_changes


def test_service_files_choice_is_remembered(env: Env) -> None:
    assert env.window.file_tree.shows_service_files
    env.window.show_service_action.setChecked(False)
    assert not env.window.file_tree.shows_service_files
    assert env.settings.show_service_files() is False


def test_build_folder_choice_is_remembered(env: Env) -> None:
    assert not env.window.file_tree.shows_build_dir  # hidden unless asked for
    assert not env.window.show_build_dir_action.isChecked()
    env.window.show_build_dir_action.setChecked(True)
    assert env.window.file_tree.shows_build_dir
    assert env.settings.show_build_dir() is True


def test_tree_shows_unsaved_and_error_files(env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.type_text(main, "// unsaved\n")
    assert env.window.file_tree.dirty_paths == {main}
    env.controller.save()
    assert env.window.file_tree.dirty_paths == frozenset()


def test_recent_projects_remove_and_missing(
    qtbot, env: Env, project_copy: Path, tmp_path: Path
) -> None:
    gone = tmp_path / "gone"
    env.app.set_recent_projects([project_copy, gone])
    recents = {r.path: r for r in env.controller.recent_projects()}
    assert recents[gone].missing and not recents[project_copy].missing

    env.window.welcome.remove_recent_requested.emit(gone)
    assert env.app.recent_projects == (project_copy,)
    assert env.settings.recent_projects() == [project_copy]
    assert project_copy.is_dir()  # only the list changes, never the folder


def test_first_project_shows_the_tips_once(qtbot, env: Env, tmp_path: Path) -> None:
    started: list[int] = []
    env.window.start_first_run_tour = lambda: started.append(1)  # type: ignore[method-assign]
    env.controller.new_project()
    env.controller._create(NewProjectRequest("first", tmp_path, "stm32f103c6-minimal", "blink"))
    qtbot.waitUntil(lambda: started == [1], timeout=2000)
    assert env.settings.first_run_tips_shown()

    env.controller.new_project()
    env.controller._create(NewProjectRequest("second", tmp_path, "stm32f103c6-minimal", "blink"))
    qtbot.wait(600)
    assert started == [1]  # not again


def fake_rustfmt(tmp_path: Path, script: str) -> tuple[str, ...]:
    """A stand-in for rustfmt: reads stdin, ignores the arguments."""
    tool = tmp_path / "fake_rustfmt"
    tool.write_text("#!/bin/sh\n" + script)
    tool.chmod(0o755)
    return (str(tool),)


@pytest.mark.skipif(os.name == "nt", reason="shell script stand-in")
def test_format_file_through_rustfmt(qtbot, env: Env, project_copy: Path, tmp_path: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    original = env.window.editor_tabs.text(main)
    command = fake_rustfmt(tmp_path, 'cat; echo "// formatted"\n')
    env.controller._rustfmt_command = lambda: command

    assert env.controller.format_file()
    assert env.window.editor_tabs.text(main) == original + "// formatted\n"
    assert main in env.project.dirty_files  # an edit like any other (and undoable in Monaco)
    assert main.read_text() == original  # nothing written yet

    notes: list[tuple[str, str]] = []
    env.window.notify = lambda title, text, *a, **k: notes.append((title, text))  # type: ignore[method-assign]
    env.controller._rustfmt_command = lambda: fake_rustfmt(
        tmp_path, 'echo "error: expected expression" >&2; echo " --> <stdin>:7:3" >&2; exit 1\n'
    )
    assert not env.controller.format_file()
    assert notes == [("Форматування", "Не вдалося відформатувати (рядок 7): expected expression")]

    env.controller.open_file(project_copy / "Cargo.toml")
    messages: list[str] = []
    env.window.show_message = lambda text, *a: messages.append(text)  # type: ignore[method-assign]
    assert not env.controller.format_file()
    assert messages == ["Форматування — лише для файлів .rs"]


@pytest.mark.skipif(os.name == "nt", reason="shell script stand-in")
def test_format_on_save(qtbot, env: Env, project_copy: Path, tmp_path: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    command = fake_rustfmt(tmp_path, 'cat; echo "// formatted"\n')
    env.controller._rustfmt_command = lambda: command
    env.type_text(main, "fn main() {}\n")
    assert env.controller.save(main)
    assert main.read_text() == "fn main() {}\n"  # off by default

    env.settings.set_format_on_save(True)
    env.type_text(main, "fn main() {}\n\n")
    assert env.controller.save(main)
    assert main.read_text() == "fn main() {}\n\n// formatted\n"

    # A file rustfmt cannot parse is still saved as typed.
    env.controller._rustfmt_command = lambda: fake_rustfmt(tmp_path, "exit 1\n")
    env.type_text(main, "fn main( {\n")
    assert env.controller.save(main)
    assert main.read_text() == "fn main( {\n"


def test_prepare_build_saves_unless_turned_off(qtbot, env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    env.type_text(main, "// one\n")
    assert env.controller.prepare_build()
    assert main.read_text() == "// one\n"

    env.settings.set_save_before_build(False)
    messages: list[str] = []
    env.window.show_message = lambda text, *a: messages.append(text)  # type: ignore[method-assign]
    env.type_text(main, "// two\n")
    assert env.controller.prepare_build()
    assert main.read_text() == "// one\n"  # the build uses what is on disk
    assert messages == ["Є незбережені зміни — збирається версія з диска"]


def test_tabs_and_cursor_come_back_with_the_project(qtbot, env: Env, project_copy: Path) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    cargo = project_copy / "Cargo.toml"
    env.controller.open_file(cargo)
    env.controller.open_file(main)
    env.window.editor_tabs.cursor_moved.emit(17, 9)  # Monaco reports the cursor of main.rs
    assert env.controller.close_project()
    assert env.settings.project_session(project_copy) == {
        "open": ["src/main.rs", "Cargo.toml"],
        "active": "src/main.rs",
        "cursors": {"src/main.rs": [17, 9]},
    }

    revealed: list[tuple[Path, int, int]] = []
    env.window.editor_tabs.reveal = lambda p, line, col=1: revealed.append((p, line, col))  # type: ignore[method-assign]
    env.controller.open_project(project_copy)
    assert env.window.editor_tabs.paths == (main, cargo)
    assert env.project.active_file == main
    assert revealed == [(main, 17, 9)]


def test_session_ignores_files_that_are_gone(qtbot, env: Env, project_copy: Path) -> None:
    env.settings.set_project_session(
        project_copy, {"open": ["deleted.rs", "../outside.rs"], "active": "deleted.rs"}
    )
    env.controller.open_project(project_copy)
    assert env.window.editor_tabs.paths == (project_copy / "src" / "main.rs",)  # the default


def test_closing_a_tab_after_reordering_keeps_model_and_screen_together(
    env: Env, project_copy: Path
) -> None:
    env.controller.open_project(project_copy)
    main = project_copy / "src" / "main.rs"
    cargo = project_copy / "Cargo.toml"
    readme = project_copy / "README.md"
    for path in (cargo, readme):
        env.controller.open_file(path)
    tabs = env.window.editor_tabs
    tabs.tab_bar.moveTab(2, 0)  # the user drags README.md to the front
    assert tabs.paths == (readme, main, cargo)

    env.controller.open_file(cargo)
    env.controller.close_file(cargo)
    assert tabs.current_path == env.project.active_file


def test_cancelled_switch_leaves_the_other_project_untouched(
    env: Env, project_copy: Path, tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "Cargo.toml").write_text('[package]\nname = "plain"\nversion = "0.1.0"\n')
    env.controller.open_project(project_copy)
    env.type_text(project_copy / "src" / "main.rs", "changed")

    env.save_choice = SaveChoice.CANCEL
    assert not env.controller.open_project(plain)
    assert "nightexpress" not in (plain / "Cargo.toml").read_text()
    assert env.project.config is not None and env.project.config.path == project_copy

    env.save_choice = SaveChoice.DISCARD
    assert env.controller.open_project(plain)
    assert 'board = "stm32f103c6-minimal"' in (plain / "Cargo.toml").read_text()


def test_only_the_build_folder_of_the_project_is_hidden(env: Env, project_copy: Path) -> None:
    (project_copy / "target" / "debug").mkdir(parents=True)
    (project_copy / "target" / "debug" / "junk.rs").write_text("")
    (project_copy / "src" / "target").mkdir()
    (project_copy / "src" / "target" / "mod.rs").write_text("")
    env.controller.open_project(project_copy)

    files = {p.relative_to(project_copy).as_posix() for p in env.controller.quick_open_files()}
    assert "src/target/mod.rs" in files and "target/debug/junk.rs" not in files
    tree = env.window.file_tree
    assert tree.is_service(project_copy / "target" / "debug")
    assert not tree.is_service(project_copy / "src" / "target" / "mod.rs")
