from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nightexpress.models.entities import SupportStatus, UploadMethod
from nightexpress.services.board_catalog import (
    BOARDS_FILE,
    CHIPS_FILE,
    EXAMPLES_FILE,
    BoardCatalog,
    CatalogError,
)

Catalog = dict[str, Any]


@pytest.fixture(scope="module")
def default_files() -> Catalog:
    base = BoardCatalog.default_directory()
    return {name: json.loads((base / name).read_text("utf-8")) for name in _FILES}


_FILES = (CHIPS_FILE, BOARDS_FILE, EXAMPLES_FILE)


def write_catalog(tmp_path: Path, files: Catalog) -> Path:
    for name, data in files.items():
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def load_mutated(
    tmp_path: Path, default_files: Catalog, mutate: Callable[[Catalog], None]
) -> BoardCatalog:
    files = copy.deepcopy(default_files)
    mutate(files)
    return BoardCatalog.load(write_catalog(tmp_path, files))


def first_board(files: Catalog) -> dict[str, Any]:
    return files[BOARDS_FILE]["boards"][0]


def first_chip(files: Catalog) -> dict[str, Any]:
    return files[CHIPS_FILE]["chips"][0]


# --- the shipped catalog ----------------------------------------------------------------------


def test_default_catalog_loads() -> None:
    catalog = BoardCatalog.load()
    board = catalog.get_board("stm32f103c6-minimal")
    chip = catalog.chip_for(board)

    assert chip.id == "STM32F103C6"
    assert chip.target_triple == "thumbv7m-none-eabi"
    assert (chip.flash_kb, chip.ram_kb) == (32, 10)
    assert chip.dev_id == 0x412
    assert chip.idcode_address == 0xE0042000
    assert chip.bootloader is not None and chip.bootloader.family == "F1"

    assert board.led is not None and board.led.pin == "PC13" and board.led.active_low
    assert board.button is not None and board.button.pin == "PA0"
    assert board.button.active_low and not board.button.on_board
    assert board.default_upload_method is UploadMethod.PROBE_RS
    assert board.method_status(UploadMethod.PROBE_RS) is SupportStatus.VERIFIED
    examples = ["blink", "serial_hello", "button", "rtt_hello"]
    assert [e.id for e in catalog.examples_for(board.id)] == examples


def test_chip_for_accepts_board_id() -> None:
    catalog = BoardCatalog.load()
    assert catalog.chip_for("stm32f103c6-minimal").id == "STM32F103C6"


@pytest.mark.parametrize(
    "query",
    ["STM32F103C6T6A", "stm32f103c6t6a", "STM32F103C6", "stm32f103c6-minimal", "Blue Pill F103C6"],
)
def test_search_exact(query: str) -> None:
    assert [b.id for b in BoardCatalog.load().search(query)] == ["stm32f103c6-minimal"]


def test_search_marking_without_vendor_prefix() -> None:
    assert [b.id for b in BoardCatalog.load().search("F103C6T6A")] == ["stm32f103c6-minimal"]


def test_search_partial_fragment() -> None:
    assert [b.id for b in BoardCatalog.load().search("103c6")] == ["stm32f103c6-minimal"]


@pytest.mark.parametrize("query", ["STM32G431CBU6", "ATmega328P", "esp32"])
def test_search_unknown_chip_returns_nothing(query: str) -> None:
    assert BoardCatalog.load().search(query) == []


def test_search_empty_returns_all() -> None:
    catalog = BoardCatalog.load()
    assert catalog.search("  ") == list(catalog.boards)


def test_unknown_ids_raise_key_error() -> None:
    catalog = BoardCatalog.load()
    with pytest.raises(KeyError, match="unknown board"):
        catalog.get_board("nope")
    with pytest.raises(KeyError, match="unknown chip"):
        catalog.get_chip("STM32F999ZZ")
    with pytest.raises(KeyError, match="unknown example"):
        catalog.get_example("nope")


def test_search_ranks_exact_before_partial(tmp_path: Path, default_files: Catalog) -> None:
    def add_board(files: Catalog) -> None:
        other = copy.deepcopy(first_board(files))
        other.update(id="aaa-board", display_name="Board with F103C6T6A inside", aliases=[])
        files[BOARDS_FILE]["boards"].insert(0, other)
        # Make it point to a different (fake) chip so only the name matches.
        chip = copy.deepcopy(first_chip(files))
        chip.update(id="STM32F103CB", embassy_feature="stm32f103cb", probe_rs_name="STM32F103CB")
        files[CHIPS_FILE]["chips"].append(chip)
        other["chip"] = "STM32F103CB"

    catalog = load_mutated(tmp_path, default_files, add_board)
    assert [b.id for b in catalog.search("F103C6T6A")] == ["stm32f103c6-minimal", "aaa-board"]


def test_blue_pill_f103c8_is_listed_as_unverified() -> None:
    catalog = BoardCatalog.load()
    [board] = catalog.search("STM32F103C8T6")
    chip = catalog.chip_for(board)
    assert board.id == "stm32f103c8-bluepill" and board.status is SupportStatus.EXPERIMENTAL
    assert (chip.flash_kb, chip.ram_kb, chip.dev_id) == (64, 20, 0x410)
    assert chip.target_triple == "thumbv7m-none-eabi" and chip.embassy_feature == "stm32f103c8"
    examples = {"blink", "serial_hello", "button", "rtt_hello"}
    assert {e.id for e in catalog.examples_for(board.id)} == examples


# --- broken catalogs --------------------------------------------------------------------------


def test_missing_file(tmp_path: Path, default_files: Catalog) -> None:
    write_catalog(tmp_path, {CHIPS_FILE: default_files[CHIPS_FILE]})
    with pytest.raises(CatalogError, match=r"boards\.json: file not found"):
        BoardCatalog.load(tmp_path)


def test_invalid_json_reports_position(tmp_path: Path, default_files: Catalog) -> None:
    files = dict(default_files)
    files[BOARDS_FILE] = '{\n  "schema_version": 1,\n  "boards": [ }'
    with pytest.raises(CatalogError, match=r"boards\.json: invalid JSON at line 3"):
        BoardCatalog.load(write_catalog(tmp_path, files))


def _set(
    getter: Callable[[Catalog], dict[str, Any]], key: str, value: Any
) -> Callable[[Catalog], None]:
    def mutate(files: Catalog) -> None:
        getter(files)[key] = value

    return mutate


def _delete(getter: Callable[[Catalog], dict[str, Any]], key: str) -> Callable[[Catalog], None]:
    def mutate(files: Catalog) -> None:
        del getter(files)[key]

    return mutate


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_set(lambda f: f[CHIPS_FILE], "schema_version", 2), r"unsupported schema_version 2"),
        (_delete(first_board, "chip"), r"boards\[0\]: missing key 'chip'"),
        (_set(first_board, "colour", "blue"), r"boards\[0\]: unknown key\(s\) 'colour'"),
        (_set(first_chip, "flash_kb", "32"), r"chips\[0\]\.flash_kb: expected an integer"),
        (_set(first_chip, "flash_kb", True), r"chips\[0\]\.flash_kb: expected an integer"),
        (_set(first_chip, "dev_id", "412"), r"chips\[0\]\.dev_id: expected a hex string"),
        (_set(first_chip, "flash_kb", 0), r"chips\[0\]: STM32F103C6: flash_kb"),
        (_set(first_board, "status", "tested"), r"boards\[0\]\.status: 'tested' is not one of"),
        (_set(first_board, "aliases", ["ok", 5]), r"boards\[0\]\.aliases\[1\]: expected a string"),
        (_set(first_board, "chip", "STM32F999ZZ"), r"unknown chip 'STM32F999ZZ'"),
        (
            _set(first_board, "led", {"pin": "PC99", "active_low": True}),
            r"boards\[0\]\.led\.pin: led: invalid pin name 'PC99'",
        ),
        (
            _set(first_board, "button", {"pin": "PA0", "active_low": True}),
            r"boards\[0\]\.button: missing key 'on_board'",
        ),
        (
            _set(
                first_board,
                "upload_methods",
                [{"method": "serial-bootloader", "status": "verified"}],
            ),
            r"default upload method is not in upload_methods",
        ),
        (_set(first_chip, "bootloader", None), r"lists serial-bootloader, but chip"),
        (
            _set(lambda f: f[EXAMPLES_FILE]["examples"][0], "boards", ["ghost"]),
            r"example 'blink' refers to unknown board 'ghost'",
        ),
        (
            lambda f: f[BOARDS_FILE]["boards"].append(copy.deepcopy(first_board(f))),
            r"duplicate board id 'stm32f103c6-minimal'",
        ),
    ],
)
def test_broken_catalog(
    tmp_path: Path,
    default_files: Catalog,
    mutate: Callable[[Catalog], None],
    message: str,
) -> None:
    with pytest.raises(CatalogError, match=message):
        load_mutated(tmp_path, default_files, mutate)


def test_nicknames_are_searched_and_the_first_is_shown_in_brackets() -> None:
    catalog = BoardCatalog.load()
    found = [b.id for b in catalog.search("blue pill")]
    assert found == ["stm32f103c6-minimal", "stm32f103c8-bluepill"]
    assert [b.id for b in catalog.search("bluepill f103c8")] == ["stm32f103c8-bluepill"]
    names = [b.display_name for b in catalog.boards]
    assert names == [
        "STM32F103C6 (Blue Pill)",
        "STM32F103C8 (Blue Pill)",
        "STM32F401CC (Black Pill)",
        "STM32F411CE (Black Pill)",
        "STM32F103RB (Nucleo-F103RB)",
        "STM32F401RE (Nucleo-F401RE)",
        "STM32F411RE (Nucleo-F411RE)",
        "STM32F446RE (Nucleo-F446RE)",
        "STM32L476RG (Nucleo-L476RG)",
    ]
    black_pills = ["stm32f401cc-blackpill", "stm32f411ce-blackpill"]
    assert [b.id for b in catalog.search("black pill")] == black_pills
    assert [b.id for b in catalog.search("black pill f401")] == ["stm32f401cc-blackpill"]
    assert len(catalog.search("nucleo")) == 5
    assert [b.id for b in catalog.search("Nucleo-F401RE")] == ["nucleo-f401re"]
    assert [b.id for b in catalog.search("STM32F411CEU6")] == ["stm32f411ce-blackpill"]


def test_name_in_another_language_is_searched_too() -> None:
    import dataclasses

    from nightexpress import i18n

    loaded = BoardCatalog.load()
    board = dataclasses.replace(
        loaded.boards[0], display_names={"uk": "Плата розробника", "en": "Dev Board"}
    )
    catalog = BoardCatalog(loaded.chips, [board], [])
    i18n.install("en")  # the source_language fixture puts Ukrainian back
    assert [b.id for b in catalog.search("розробника")] == [board.id]
