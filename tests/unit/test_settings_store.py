from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from nightexpress.models.entities.font import FontArea
from nightexpress.services.settings_store import DEFAULT_BAUD_RATE, SettingsStore

PL2303 = "serial:067b:2303@1-1"


def make_store(tmp_path: Path) -> tuple[SettingsStore, Path]:
    ini = tmp_path / "settings.ini"
    return SettingsStore(QSettings(str(ini), QSettings.Format.IniFormat)), ini


def reopen(ini: Path) -> SettingsStore:
    return SettingsStore(QSettings(str(ini), QSettings.Format.IniFormat))


def test_defaults(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    assert store.recent_projects() == []
    assert store.project_upload_target(tmp_path / "blink") is None
    assert store.adapter_label(PL2303) is None
    assert store.monitor_baud_rate() == DEFAULT_BAUD_RATE
    assert store.theme() == "graphite"
    assert store.language() == "system"
    assert store.window_geometry() is None
    assert store.window_state() is None


def test_values_survive_reopening(tmp_path: Path) -> None:
    store, ini = make_store(tmp_path)
    project = tmp_path / "my projects" / "blink"  # spaces and slashes in the path
    store.set_recent_projects([project, tmp_path / "другий"])
    store.set_project_upload_target(project, "probe:0483:3748:2C3B")
    store.set_adapter_label(PL2303, "  до STM32  ")
    store.set_monitor_baud_rate(9600)
    store.set_theme("light")
    store.set_language("en")
    store.set_window_geometry(b"\x01\x02\x00\xff")
    store.set_window_state(b"state")
    store.sync()

    again = reopen(ini)
    assert again.recent_projects() == [project, tmp_path / "другий"]
    assert again.project_upload_target(project) == "probe:0483:3748:2C3B"
    assert again.adapter_label(PL2303) == "до STM32"
    assert again.monitor_baud_rate() == 9600
    assert again.theme() == "light"
    assert again.language() == "en"
    assert again.window_geometry() == b"\x01\x02\x00\xff"
    assert again.window_state() == b"state"


def test_project_target_is_per_project_and_clearable(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    store.set_project_upload_target(a, PL2303)
    assert store.project_upload_target(b) is None
    store.set_project_upload_target(a, None)
    assert store.project_upload_target(a) is None


def test_empty_adapter_label_removes_it(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.set_adapter_label(PL2303, "до STM32")
    store.set_adapter_label(PL2303, "   ")
    assert store.adapter_label(PL2303) is None


def test_corrupted_values_fall_back_to_defaults(tmp_path: Path) -> None:
    ini = tmp_path / "settings.ini"
    raw = QSettings(str(ini), QSettings.Format.IniFormat)
    raw.setValue("recent_projects", "{not json")
    raw.setValue("serial_monitor/baud_rate", "fast")
    raw.setValue("appearance/theme", "neon")
    raw.sync()
    store = reopen(ini)
    assert store.recent_projects() == []
    assert store.monitor_baud_rate() == DEFAULT_BAUD_RATE
    assert store.theme() == "neon"  # kept as written: the theme catalog falls back


def test_validation(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.set_monitor_baud_rate(0)
    with pytest.raises(ValueError, match="theme id is empty"):
        store.set_theme(" ")
    with pytest.raises(ValueError, match="language code is empty"):
        store.set_language("")


def test_theme_choice_and_values_of_older_versions(tmp_path: Path) -> None:
    store, ini = make_store(tmp_path)
    assert store.theme() == "graphite"  # nothing chosen yet
    for chosen in ("ocean", "system", "a-theme-that-was-removed"):
        store.set_theme(chosen)  # whether it exists is the theme catalog's business
        assert store.theme() == chosen
    store.set_theme("dark")  # written before themes had names
    store.sync()
    assert reopen(ini).theme() == "graphite"


def test_project_opened_at_profile_and_font(tmp_path: Path) -> None:
    from datetime import datetime

    from nightexpress.models.entities import BuildProfile

    store, ini = make_store(tmp_path)
    project = tmp_path / "blink"
    assert store.project_opened_at(project) is None
    assert store.project_build_profile(project) is BuildProfile.RELEASE
    assert store.font_size() == 14
    moment = datetime(2026, 11, 15, 10, 24, 7)
    store.set_project_opened_at(project, moment)
    store.set_project_build_profile(project, BuildProfile.DEV)
    store.set_font_size(FontArea.EDITOR, 40)  # clamped
    store.sync()
    again = reopen(ini)
    assert again.project_opened_at(project) == moment
    assert again.project_build_profile(project) is BuildProfile.DEV
    assert again.font_size() == 28


def test_layout_session_and_options(tmp_path: Path) -> None:
    store, ini = make_store(tmp_path)
    assert store.window_layout() == {}
    assert store.project_session(tmp_path / "blink") == {}
    assert store.open_last_project() and store.save_before_build()
    assert not store.format_on_save()
    assert not store.bracket_pair_guides()
    assert store.minimap()

    store.set_window_layout({"top": [300, 800, 360], "bottom_tab": 3})
    session = {
        "open": ["src/main.rs"],
        "active": "src/main.rs",
        "cursors": {"src/main.rs": [17, 9]},
    }
    store.set_project_session(tmp_path / "blink", session)
    store.set_open_last_project(False)
    store.set_save_before_build(False)
    store.set_format_on_save(True)
    store.set_bracket_pair_guides(True)
    store.set_minimap(False)

    again = reopen(ini)
    assert again.window_layout() == {"top": [300, 800, 360], "bottom_tab": 3}
    assert again.project_session(tmp_path / "blink") == session
    assert again.project_session(tmp_path / "other") == {}  # per project
    assert not again.open_last_project() and not again.save_before_build()
    assert again.format_on_save()
    assert again.bracket_pair_guides()
    assert not again.minimap()


def test_broken_layout_and_session_are_ignored(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store._s.setValue("window/layout", "{not json")
    assert store.window_layout() == {}
    store._s.setValue("window/layout", "[1, 2]")  # valid JSON, wrong shape
    assert store.window_layout() == {}


def test_font_sizes_of_the_text_areas(tmp_path: Path) -> None:
    store, ini = make_store(tmp_path)
    assert store.single_font_size()  # one size for everything until the user says otherwise
    assert [store.font_size(area) for area in FontArea] == [14, 14, 14, 14]
    store.set_font_size(FontArea.EDITOR, 17)
    # Settings of an older version hold the editor's size only: the other areas have it too.
    assert [store.font_size(area) for area in FontArea] == [17, 17, 17, 17]
    store.set_font_size(FontArea.TERMINAL, 12)
    store.set_font_size(FontArea.UART, 3)  # clamped
    store.set_single_font_size(False)
    store.sync()
    again = reopen(ini)
    assert not again.single_font_size()
    assert {area: again.font_size(area) for area in FontArea} == {
        FontArea.EDITOR: 17,
        FontArea.TERMINAL: 12,
        FontArea.UART: 9,
        FontArea.LOGS: 17,
    }
    raw = QSettings(str(ini), QSettings.Format.IniFormat)
    raw.setValue("terminal/font_size", "big")
    raw.sync()
    assert reopen(ini).font_size(FontArea.TERMINAL) == 17  # not a number: the editor's size


def test_settings_of_the_old_name_are_carried_over_once(tmp_path) -> None:
    from PySide6.QtCore import QSettings

    from nightexpress.services.settings_store import carry_over

    old = QSettings(str(tmp_path / "old.ini"), QSettings.Format.IniFormat)
    old.setValue("appearance/language", "es")
    old.setValue("projects/abc/opened_at", "2026-11-17T10:00:00")
    new = QSettings(str(tmp_path / "new.ini"), QSettings.Format.IniFormat)
    assert carry_over(old, new)
    assert new.value("appearance/language") == "es"
    assert new.value("projects/abc/opened_at") == "2026-11-17T10:00:00"
    old.setValue("appearance/language", "uk")
    assert not carry_over(old, new)  # the new settings are the user's now
    assert new.value("appearance/language") == "es"
