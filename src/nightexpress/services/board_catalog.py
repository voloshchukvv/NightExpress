"""Board/chip/example catalog loaded from ``resources/catalog/*.json``.

The JSON is parsed by a strict hand-written reader instead of JSON Schema: unknown keys, missing
keys, wrong types, dangling references and duplicate ids are all reported with the exact path
(e.g. ``boards.json: boards[0].led.pin``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from enum import Enum
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, TypeVar

from nightexpress.models.entities import (
    BoardDefinition,
    BootloaderInfo,
    ButtonInfo,
    ChipDefinition,
    ExampleDefinition,
    I2cInfo,
    LedInfo,
    SupportStatus,
    UartInfo,
    UploadMethod,
    UploadMethodSupport,
    chip_model_from_marking,
)
from nightexpress.models.entities.language import localized_texts

SCHEMA_VERSION = 1
CHIPS_FILE = "chips.json"
BOARDS_FILE = "boards.json"
EXAMPLES_FILE = "examples.json"

T = TypeVar("T")
E = TypeVar("E", bound=Enum)


class CatalogError(Exception):
    """The catalog files are missing, malformed or inconsistent."""


class _Reader:
    """Typed access to one JSON object; every key must be consumed exactly once."""

    def __init__(self, data: Any, path: str) -> None:
        if not isinstance(data, dict):
            raise CatalogError(f"{path}: expected an object, got {type(data).__name__}")
        self._data: dict[str, Any] = data
        self._path = path
        self._used: set[str] = set()

    def _take(self, key: str, optional: bool = False) -> Any:
        self._used.add(key)
        if key not in self._data:
            if optional:
                return None
            raise CatalogError(f"{self._path}: missing key {key!r}")
        return self._data[key]

    def _typed(self, key: str, kind: type | tuple[type, ...], name: str, optional: bool) -> Any:
        value = self._take(key, optional)
        if value is None and optional:
            return None
        # bool is a subclass of int; never accept it where a number is expected.
        if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
            raise CatalogError(f"{self.at(key)}: expected {name}, got {type(value).__name__}")
        return value

    @property
    def path(self) -> str:
        return self._path

    def at(self, key: str) -> str:
        return f"{self._path}.{key}"

    def str_(self, key: str) -> str:
        return self._typed(key, str, "a string", optional=False)

    def texts(self, key: str) -> dict[str, str]:
        """A text shown to the user: one string or ``{language: text}``."""
        try:
            return localized_texts(self._take(key))
        except ValueError as exc:
            raise CatalogError(f"{self.at(key)}: {exc}") from exc

    def opt_str(self, key: str) -> str | None:
        return self._typed(key, str, "a string or null", optional=True)

    def int_(self, key: str) -> int:
        return self._typed(key, int, "an integer", optional=False)

    def bool_(self, key: str) -> bool:
        return self._typed(key, bool, "a boolean", optional=False)

    def hex_(self, key: str) -> int:
        raw = self.str_(key)
        if not re.fullmatch(r"0x[0-9A-Fa-f]+", raw):
            raise CatalogError(f"{self.at(key)}: expected a hex string like '0x412', got {raw!r}")
        return int(raw, 16)

    def enum_(self, key: str, enum: type[E]) -> E:
        raw = self.str_(key)
        try:
            return enum(raw)
        except ValueError:
            allowed = ", ".join(repr(m.value) for m in enum)
            raise CatalogError(f"{self.at(key)}: {raw!r} is not one of {allowed}") from None

    def str_list(self, key: str) -> tuple[str, ...]:
        items = self.list_(key)
        for i, item in enumerate(items):
            if not isinstance(item, str):
                raise CatalogError(f"{self.at(key)}[{i}]: expected a string")
        return tuple(items)

    def opt_str_list(self, key: str) -> tuple[str, ...]:
        """A list of strings that may be left out: then it is empty."""
        if self._take(key, optional=True) is None:
            return ()
        return self.str_list(key)

    def list_(self, key: str) -> list[Any]:
        return self._typed(key, list, "a list", optional=False)

    def opt_obj(self, key: str) -> _Reader | None:
        value = self._take(key, optional=True)
        return None if value is None else _Reader(value, self.at(key))

    def objects(self, key: str) -> list[_Reader]:
        return [_Reader(item, f"{self.at(key)}[{i}]") for i, item in enumerate(self.list_(key))]

    def finish(self) -> None:
        unknown = sorted(set(self._data) - self._used)
        if unknown:
            raise CatalogError(f"{self._path}: unknown key(s) {', '.join(map(repr, unknown))}")


def _build(path: str, factory: Callable[[], T]) -> T:
    """Run an entity constructor and attach the JSON path to its validation error."""
    try:
        return factory()
    except ValueError as exc:
        raise CatalogError(f"{path}: {exc}") from exc


def _parse_chip(r: _Reader, path: str) -> ChipDefinition:
    boot = r.opt_obj("bootloader")
    bootloader = None
    if boot is not None:
        b_family, b_usart, b_tx, b_rx = (
            boot.str_("family"),
            boot.str_("usart"),
            boot.str_("tx_pin"),
            boot.str_("rx_pin"),
        )
        boot.finish()
        bootloader = _build(path, lambda: BootloaderInfo(b_family, b_usart, b_tx, b_rx))
    fields = {
        "id": r.str_("id"),
        "family": r.str_("family"),
        "core": r.str_("core"),
        "target_triple": r.str_("target_triple"),
        "flash_kb": r.int_("flash_kb"),
        "ram_kb": r.int_("ram_kb"),
        "embassy_feature": r.str_("embassy_feature"),
        "probe_rs_name": r.str_("probe_rs_name"),
        "dev_id": r.hex_("dev_id"),
        "idcode_address": r.hex_("idcode_address"),
        "flash_size_address": r.hex_("flash_size_address"),
        "default_sysclk_hz": r.int_("default_sysclk_hz"),
    }
    r.finish()
    return _build(path, lambda: ChipDefinition(**fields, bootloader=bootloader))


def _parse_board(r: _Reader, path: str) -> BoardDefinition:
    led = None
    if (led_r := r.opt_obj("led")) is not None:
        pin, active_low = led_r.str_("pin"), led_r.bool_("active_low")
        led_r.finish()
        led = _build(led_r.at("pin"), lambda: LedInfo(pin, active_low))

    button = None
    if (button_r := r.opt_obj("button")) is not None:
        b_pin, b_low = button_r.str_("pin"), button_r.bool_("active_low")
        b_on_board = button_r.bool_("on_board")
        button_r.finish()
        button = _build(button_r.at("pin"), lambda: ButtonInfo(b_pin, b_low, b_on_board))

    i2c = None
    if (i2c_r := r.opt_obj("i2c")) is not None:
        bus, scl, sda = i2c_r.str_("peripheral"), i2c_r.str_("scl_pin"), i2c_r.str_("sda_pin")
        i2c_r.finish()
        i2c = _build(i2c_r.at("peripheral"), lambda: I2cInfo(bus, scl, sda))
    uart = None
    if (uart_r := r.opt_obj("uart")) is not None:
        periph, tx, rx = uart_r.str_("peripheral"), uart_r.str_("tx_pin"), uart_r.str_("rx_pin")
        uart_r.finish()
        uart = _build(uart_r.at("peripheral"), lambda: UartInfo(periph, tx, rx))

    methods = []
    for m in r.objects("upload_methods"):
        methods.append(
            UploadMethodSupport(m.enum_("method", UploadMethod), m.enum_("status", SupportStatus))
        )
        m.finish()

    names = r.texts("display_name")
    fields = {
        "id": r.str_("id"),
        "display_names": names,
        "chip_id": r.str_("chip"),
        "aliases": r.str_list("aliases"),
        "nicknames": r.opt_str_list("nicknames"),
        "default_upload_method": r.enum_("default_upload_method", UploadMethod),
        "status": r.enum_("status", SupportStatus),
        "notes": r.opt_str("notes") or "",
    }
    r.finish()
    return _build(
        path,
        lambda: BoardDefinition(
            **fields, led=led, button=button, uart=uart, i2c=i2c, upload_methods=tuple(methods)
        ),
    )


def _parse_example(r: _Reader, path: str) -> ExampleDefinition:
    fields = {
        "id": r.str_("id"),
        "title": r.str_("title"),
        "descriptions": r.texts("description"),
        "template": r.str_("template"),
        "board_ids": r.str_list("boards"),
    }
    r.finish()
    return _build(path, lambda: ExampleDefinition(**fields))


def _parse_file(name: str, text: str, list_key: str, parse: Callable[[_Reader, str], T]) -> list[T]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(
            f"{name}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    root = _Reader(data, name)
    version = root.int_("schema_version")
    if version != SCHEMA_VERSION:
        raise CatalogError(
            f"{name}: unsupported schema_version {version} (expected {SCHEMA_VERSION})"
        )
    items = [parse(item, item.path) for item in root.objects(list_key)]
    root.finish()
    return items


def _index(items: Iterable[T], key: Callable[[T], str], name: str, what: str) -> dict[str, T]:
    result: dict[str, T] = {}
    for item in items:
        k = key(item)
        if k in result:
            raise CatalogError(f"{name}: duplicate {what} id {k!r}")
        result[k] = item
    return result


def _normalize(text: str) -> str:
    return re.sub(r"[\s\-_]", "", text).upper()


class BoardCatalog:
    def __init__(
        self,
        chips: Iterable[ChipDefinition],
        boards: Iterable[BoardDefinition],
        examples: Iterable[ExampleDefinition],
    ) -> None:
        self._chips = _index(chips, lambda c: c.id, CHIPS_FILE, "chip")
        self._boards = _index(boards, lambda b: b.id, BOARDS_FILE, "board")
        self._examples = _index(examples, lambda e: e.id, EXAMPLES_FILE, "example")
        self._check_references()

    # --- loading ---------------------------------------------------------------------------

    @classmethod
    def default_directory(cls) -> Traversable:
        return resources.files("nightexpress") / "resources" / "catalog"

    @classmethod
    def load(cls, directory: Path | Traversable | None = None) -> BoardCatalog:
        base = directory if directory is not None else cls.default_directory()

        def read(name: str) -> str:
            file = base / name
            if not file.is_file():
                raise CatalogError(f"{name}: file not found in {base}")
            return file.read_text(encoding="utf-8")

        return cls(
            _parse_file(CHIPS_FILE, read(CHIPS_FILE), "chips", _parse_chip),
            _parse_file(BOARDS_FILE, read(BOARDS_FILE), "boards", _parse_board),
            _parse_file(EXAMPLES_FILE, read(EXAMPLES_FILE), "examples", _parse_example),
        )

    def _check_references(self) -> None:
        for board in self._boards.values():
            if board.chip_id not in self._chips:
                raise CatalogError(
                    f"{BOARDS_FILE}: board {board.id!r} refers to unknown chip {board.chip_id!r}"
                )
            chip = self._chips[board.chip_id]
            if board.supports(UploadMethod.SERIAL_BOOTLOADER) and chip.bootloader is None:
                raise CatalogError(
                    f"{BOARDS_FILE}: board {board.id!r} lists serial-bootloader, "
                    f"but chip {chip.id!r} has no bootloader info"
                )
        for example in self._examples.values():
            for board_id in example.board_ids:
                if board_id not in self._boards:
                    raise CatalogError(
                        f"{EXAMPLES_FILE}: example {example.id!r} "
                        f"refers to unknown board {board_id!r}"
                    )

    # --- queries ---------------------------------------------------------------------------

    @property
    def chips(self) -> tuple[ChipDefinition, ...]:
        return tuple(self._chips.values())

    @property
    def boards(self) -> tuple[BoardDefinition, ...]:
        return tuple(self._boards.values())

    @property
    def examples(self) -> tuple[ExampleDefinition, ...]:
        return tuple(self._examples.values())

    def get_chip(self, chip_id: str) -> ChipDefinition:
        try:
            return self._chips[chip_id]
        except KeyError:
            raise KeyError(f"unknown chip {chip_id!r}") from None

    def get_board(self, board_id: str) -> BoardDefinition:
        try:
            return self._boards[board_id]
        except KeyError:
            raise KeyError(f"unknown board {board_id!r}") from None

    def get_example(self, example_id: str) -> ExampleDefinition:
        try:
            return self._examples[example_id]
        except KeyError:
            raise KeyError(f"unknown example {example_id!r}") from None

    def chip_for(self, board: BoardDefinition | str) -> ChipDefinition:
        board_def = self.get_board(board) if isinstance(board, str) else board
        return self.get_chip(board_def.chip_id)

    def examples_for(self, board_id: str) -> tuple[ExampleDefinition, ...]:
        return tuple(e for e in self._examples.values() if board_id in e.board_ids)

    def search(self, query: str) -> list[BoardDefinition]:
        """Find boards by part marking (``STM32F103C6T6A``), id, alias, nickname ("Blue
        Pill") or a fragment of the name in any language.

        Exact matches (same chip model, id or alias) come first, then substring matches.
        An empty query returns all boards.
        """
        needle = _normalize(query)
        if not needle:
            return list(self._boards.values())
        model = chip_model_from_marking(needle)
        if model is None and not needle.startswith("STM32"):
            # People often type the marking without the vendor prefix: "F103C6T6A".
            model = chip_model_from_marking("STM32" + needle)

        exact: list[BoardDefinition] = []
        partial: list[BoardDefinition] = []
        for board in self._boards.values():
            keys = [board.id, board.chip_id, *board.aliases, *board.nicknames]
            normalized = [_normalize(k) for k in keys]
            names = [_normalize(name) for name in board.display_names.values()]
            if board.chip_id == model or needle in normalized:
                exact.append(board)
            elif any(needle in k for k in [*normalized, *names]):
                partial.append(board)
        return exact + partial
