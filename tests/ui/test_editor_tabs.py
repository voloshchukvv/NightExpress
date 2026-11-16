from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWebEngineCore import QWebEngineScript

from nightexpress.views.editor.editor_tabs import EditorTabsView
from nightexpress.views.editor.markdown_preview import MarkdownPreview
from nightexpress.views.editor.monaco_editor import EditorAction, monaco_installed

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not monaco_installed(), reason="run scripts/fetch_monaco.py first"),
]

MAIN = Path("/work/blink/src/main.rs")
CARGO = Path("/work/blink/Cargo.toml")
README = Path("/work/blink/README.md")


@pytest.fixture
def tabs(qtbot, dispose) -> EditorTabsView:
    view = EditorTabsView()
    dispose(view)
    view.show()
    with qtbot.waitSignal(view.editor.ready, timeout=20_000):
        pass
    return view


def preview_query(qtbot, preview: MarkdownPreview, expression: str):
    with qtbot.waitCallback(timeout=5000) as result:
        world = int(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        preview.page().runJavaScript(expression, world, result)
    return result.args[0]


def test_open_files_create_tabs_once(tabs: EditorTabsView) -> None:
    tabs.open_file(MAIN, "fn main() {}")
    tabs.open_file(CARGO, "[package]")
    tabs.open_file(MAIN, "ignored: already open")
    assert tabs.paths == (MAIN, CARGO)
    assert tabs.current_path == MAIN
    assert tabs.text(MAIN) == "fn main() {}"
    assert tabs.tab_bar.tabText(0) == "main.rs"


def test_user_tab_switch_emits(qtbot, tabs: EditorTabsView) -> None:
    tabs.open_file(MAIN, "a")
    tabs.open_file(CARGO, "b")
    with qtbot.waitSignal(tabs.tab_selected) as blocker:
        tabs.tab_bar.setCurrentIndex(0)
    assert blocker.args == [MAIN]
    assert tabs.editor.active_document == MAIN


def test_close_request_is_an_intent_only(qtbot, tabs: EditorTabsView) -> None:
    tabs.open_file(MAIN, "a")
    with qtbot.waitSignal(tabs.tab_close_requested) as blocker:
        tabs.close_button(MAIN).click()
    assert blocker.args == [MAIN]
    assert tabs.paths == (MAIN,)  # the controller decides (e.g. ask to save first)
    tabs.close_file(MAIN)
    assert tabs.paths == () and tabs.current_path is None


def test_dirty_marker(tabs: EditorTabsView) -> None:
    tabs.open_file(MAIN, "a")
    tabs.set_dirty(MAIN, True)
    assert tabs.is_marked_dirty(MAIN)
    assert tabs.tab_bar.tabText(0) == "main.rs"  # the tab keeps its width
    assert tabs.close_button(MAIN).shows_dot
    tabs.set_dirty(MAIN, False)
    assert not tabs.is_marked_dirty(MAIN)


def test_dirty_dot_turns_into_close_cross_under_the_mouse(qtbot, tabs: EditorTabsView) -> None:
    tabs.open_file(MAIN, "a")
    tabs.set_dirty(MAIN, True)
    button = tabs.close_button(MAIN)
    button.enterEvent(None)
    assert not button.shows_dot and button.dirty
    with qtbot.waitSignal(tabs.tab_close_requested) as blocker:
        button.click()
    assert blocker.args == [MAIN]
    button.leaveEvent(None)
    assert button.shows_dot


def test_middle_click_asks_to_close(qtbot, tabs: EditorTabsView) -> None:
    tabs.open_file(MAIN, "a")
    tabs.open_file(CARGO, "b")
    with qtbot.waitSignal(tabs.tab_close_requested) as blocker:
        tabs.tab_bar.middle_clicked.emit(0)
    assert blocker.args == [MAIN]


def test_tab_menu(qtbot, tabs: EditorTabsView) -> None:
    tabs.open_file(MAIN, "a")
    menu = tabs.tab_menu(MAIN)
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert list(actions) == [
        "Закрити",
        "Закрити інші",
        "Закрити всі",
        "Показати в дереві",
        "Скопіювати шлях",
    ]
    assert not actions["Закрити інші"].isEnabled()  # it is the only tab

    tabs.open_file(CARGO, "b")
    actions = {a.text(): a for a in tabs.tab_menu(MAIN).actions() if a.text()}
    for text, signal, args in (
        ("Закрити", tabs.tab_close_requested, [MAIN]),
        ("Закрити інші", tabs.close_others_requested, [MAIN]),
        ("Закрити всі", tabs.close_all_requested, []),
        ("Показати в дереві", tabs.reveal_in_tree_requested, [MAIN]),
        ("Скопіювати шлях", tabs.path_copied, [str(MAIN)]),
    ):
        with qtbot.waitSignal(signal) as blocker:
            actions[text].trigger()
        assert blocker.args == args


def test_next_and_previous_tab_wrap_around(qtbot, tabs: EditorTabsView) -> None:
    tabs.open_file(MAIN, "a")
    tabs.open_file(CARGO, "b")
    with qtbot.waitSignal(tabs.tab_selected) as blocker:
        tabs.select_next()
    assert blocker.args == [MAIN]
    tabs.select_next(-1)
    assert tabs.current_path == CARGO


def test_empty_state_replaces_the_editor_until_a_file_is_open(tabs: EditorTabsView) -> None:
    assert tabs.is_empty and not tabs.tab_bar.isVisible()
    assert [text for text, _keys in tabs.empty.hints][:2] == ["Відкрити файл", "Зібрати"]
    assert all(keys for _text, keys in tabs.empty.hints)
    tabs.open_file(MAIN, "a")
    assert not tabs.is_empty and tabs.tab_bar.isVisible()
    tabs.close_file(MAIN)
    assert tabs.is_empty


def test_markdown_toggle_is_icon_only_and_remembers_each_open_tab(
    qtbot, tabs: EditorTabsView
) -> None:
    tabs.open_file(MAIN, "fn main() {}")
    assert not tabs.markdown_toggle.isVisible()

    markdown = "# Board\n\n| Flash |\n| --- |\n| 32 KB |\n\n```sh\ncargo build\n```\n"
    tabs.open_file(README, markdown)
    assert tabs.is_editing and tabs.markdown_toggle.isVisible()
    assert tabs.markdown_toggle.text() == "" and not tabs.markdown_toggle.icon().isNull()
    with qtbot.waitSignal(tabs.markdown_preview.loadFinished, timeout=10_000):
        tabs.markdown_toggle.click()
    assert tabs.markdown_toggle.isChecked() and tabs.markdown_preview.isVisible()
    assert preview_query(qtbot, tabs.markdown_preview, "!!document.querySelector('main h1#board')")

    notes = README.with_name("notes.MD")
    tabs.open_file(notes, "# Notes")
    assert tabs.is_editing and tabs.markdown_toggle.isVisible()
    assert not tabs.markdown_toggle.isChecked()
    tabs.set_current(MAIN)
    assert tabs.is_editing and not tabs.markdown_toggle.isVisible()
    tabs.set_current(README)
    assert tabs.markdown_toggle.isChecked() and tabs.markdown_preview.isVisible()

    tabs.close_file(README)
    tabs.open_file(README, markdown)
    assert tabs.is_editing and not tabs.markdown_toggle.isChecked()


def test_markdown_preview_tracks_editor_buffer(qtbot, tabs: EditorTabsView) -> None:
    tabs.open_file(README, "# Saved\n")
    with qtbot.waitSignal(tabs.content_changed, timeout=5000):
        tabs.editor._bridge.contentChanged(str(README), "# Unsaved\n", 2)
    preview = tabs.markdown_preview
    with qtbot.waitSignal(preview.loadFinished, timeout=10_000):
        tabs.markdown_toggle.click()
    assert preview_query(qtbot, preview, "document.querySelector('h1')?.textContent") == "Unsaved"

    with qtbot.assertNotEmitted(tabs.content_changed):
        tabs.replace_text(README, "# Reloaded\n")
    assert preview_query(qtbot, preview, "document.querySelector('h1')?.textContent") == "Reloaded"
    tabs.trigger_action(EditorAction.SELECT_ALL)
    qtbot.waitUntil(lambda: preview.selectedText().strip() == "Reloaded")

    tabs.markdown_toggle.click()
    assert tabs.is_editing and tabs.editor.active_document == README
    assert tabs.text(README) == "# Reloaded\n"


def test_markdown_layout_contains_wide_content_and_escapes_raw_html(qtbot, tabs: EditorTabsView):
    wide = "wide_" * 100
    source = (
        "# Heading\n\n"
        "- [x] Done\n\n"
        f"| Pin | Notes |\n| --- | ---: |\n| `PA9` | {wide} |\n\n"
        f'```rust\nlet value = "{wide}";\n```\n\n'
        "<script>document.body.contentEditable = 'true'</script>\n"
    )
    tabs.resize(390, 480)
    tabs.open_file(README, source)
    preview = tabs.markdown_preview
    with qtbot.waitSignal(preview.loadFinished, timeout=10_000):
        tabs.markdown_toggle.click()
    assert (
        preview_query(
            qtbot,
            preview,
            """
        [document.documentElement.scrollWidth === innerWidth,
         ...[...document.querySelectorAll('.table-scroll, pre')]
            .map(el => el.scrollWidth > el.clientWidth),
         !!document.querySelector('thead th'),
         document.querySelector('input').checked && document.querySelector('input').disabled,
         !!document.querySelector('pre .kd'),
         !document.querySelector('script') && !document.body.isContentEditable]
        .join(',')
    """,
        )
        == "true,true,true,true,true,true,true"
    )
