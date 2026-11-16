from __future__ import annotations

from nightexpress.views.fuzzy import fuzzy_filter, fuzzy_score
from nightexpress.views.icons import file_icon_name

FILES = ["src/main.rs", "src/drivers/led.rs", "Cargo.toml", ".cargo/config.toml", "memory.x"]


def test_query_letters_must_come_in_order() -> None:
    assert fuzzy_score("mrs", "src/main.rs") is not None
    assert fuzzy_score("srm", "main.rs") is None
    assert fuzzy_score("", "anything") == 0


def test_case_and_spaces_are_ignored() -> None:
    assert fuzzy_score("CARGO", "Cargo.toml") == fuzzy_score("car go", "Cargo.toml")


def test_file_name_beats_folder_name() -> None:
    assert fuzzy_filter("led", FILES)[0] == "src/drivers/led.rs"
    assert fuzzy_filter("cargo", FILES)[0] == "Cargo.toml"
    assert fuzzy_filter("conf", FILES) == [".cargo/config.toml"]
    assert fuzzy_filter("main", FILES)[0] == "src/main.rs"


def test_empty_query_keeps_the_given_order_and_limit() -> None:
    assert fuzzy_filter("", FILES) == FILES
    assert fuzzy_filter("  ", FILES, limit=2) == FILES[:2]
    assert fuzzy_filter("zzz", FILES) == []


def test_file_icons_by_type() -> None:
    assert file_icon_name("main.rs") == "rust"
    assert file_icon_name("Cargo.toml") == "settings"
    assert file_icon_name("Cargo.lock") == "lock"
    assert file_icon_name("memory.x") == "memory-stick"
    assert file_icon_name("README.md") == "file-text"
    assert file_icon_name("Makefile") == "file"
    assert file_icon_name(".gitignore") == "file"
