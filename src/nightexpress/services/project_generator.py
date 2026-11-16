"""Create firmware projects from the Jinja2 templates in ``resources/templates``.

Template layout conventions:

- ``*.j2`` files are rendered, other files are copied verbatim;
- a path component ``dot_x`` becomes ``.x``
  (``dot_cargo/config.toml.j2`` -> ``.cargo/config.toml``), so dot-directories survive packaging;
- ``template.toml`` holds metadata and is not copied.
"""

from __future__ import annotations

import shutil
import tomllib
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError

from nightexpress.i18n import N_, _
from nightexpress.models.entities import (
    BoardDefinition,
    ChipDefinition,
    UploadMethod,
    project_name_error,
)

TEMPLATE_META = "template.toml"


class ProjectGenerationError(Exception):
    """The project cannot be created (user-facing message in Ukrainian)."""


@dataclass(frozen=True)
class TemplateInfo:
    name: str
    title: str
    requires: tuple[str, ...]
    open_on_create: str | None


@dataclass(frozen=True)
class GeneratedFile:
    path: PurePosixPath  # relative to the project root
    content: str


def _format_hz(hz: int) -> str:
    mhz = hz / 1_000_000
    return f"{mhz:g}"


def core_description(chip: ChipDefinition) -> str:
    core = "-".join(part.capitalize() for part in chip.core.split("-"))  # cortex-m3 -> Cortex-M3
    fpu = "with" if chip.target_triple.endswith("eabihf") else "without"
    return f"{core} {fpu} FPU ({chip.family})"


def make_context(
    project_name: str,
    board: BoardDefinition,
    chip: ChipDefinition,
    upload_method: UploadMethod | None = None,
) -> dict[str, Any]:
    """Template variables for a board; every template receives the same set."""
    cycles = chip.default_sysclk_hz // 2
    led, button, uart = board.led, board.button, board.uart
    return {
        "project_name": project_name,
        "board_id": board.id,
        "board_name": board.display_name,
        "board_name_en": (board.display_name_en or "").strip() or board.id,
        "chip_id": chip.id,
        "probe_rs_name": chip.probe_rs_name,
        "flash_kb": chip.flash_kb,
        "ram_kb": chip.ram_kb,
        "embassy_feature": chip.embassy_feature,
        "target_triple": chip.target_triple,
        "core_description": core_description(chip),
        "upload_method": (upload_method or board.default_upload_method).value,
        "led_pin": led.pin if led else None,
        "led_active_low": led.active_low if led else None,
        "button_pin": button.pin if button else None,
        "button_active_low": button.active_low if button else None,
        "button_on_board": button.on_board if button else None,
        "uart_peripheral": uart.peripheral if uart else None,
        "uart_tx_pin": uart.tx_pin if uart else None,
        "uart_rx_pin": uart.rx_pin if uart else None,
        "sysclk_mhz": _format_hz(chip.default_sysclk_hz),
        "half_second_cycles_text": f"{cycles:,}",
        "half_second_cycles_rust": f"{cycles:_}",
        "slice_20us_cycles": chip.default_sysclk_hz // 50_000,
    }


def _output_path(relative: PurePosixPath) -> PurePosixPath:
    parts = ["." + p[4:] if p.startswith("dot_") else p for p in relative.parts]
    name = parts[-1]
    if name.endswith(".j2"):
        parts[-1] = name[:-3]
    path = PurePosixPath(*parts)
    if path.is_absolute() or ".." in path.parts:
        raise ProjectGenerationError(
            _("Небезпечний шлях у шаблоні: {relative}").format(relative=relative)
        )
    return path


_ROOT = PurePosixPath()

# template.toml ``requires`` -> (the message when the board lacks it; check on the board)
_REQUIREMENTS: dict[str, tuple[str, Callable[[BoardDefinition], bool]]] = {
    "led": (
        N_("Приклад «{title}» потребує світлодіода, а для плати «{board}» він не описаний."),
        lambda board: board.led is not None,
    ),
    "button": (
        N_("Приклад «{title}» потребує кнопки, а для плати «{board}» він не описаний."),
        lambda board: board.button is not None,
    ),
    "uart": (
        N_("Приклад «{title}» потребує UART, а для плати «{board}» він не описаний."),
        lambda board: board.uart is not None,
    ),
}


def _walk(
    root: Traversable, prefix: PurePosixPath = _ROOT
) -> list[tuple[PurePosixPath, Traversable]]:
    items: list[tuple[PurePosixPath, Traversable]] = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        relative = prefix / entry.name
        if entry.is_dir():
            items.extend(_walk(entry, relative))
        elif entry.is_file():
            items.append((relative, entry))
    return items


class ProjectGenerator:
    def __init__(self, templates_dir: Path | Traversable | None = None) -> None:
        self._root = (
            templates_dir
            if templates_dir is not None
            else resources.files("nightexpress") / "resources" / "templates"
        )
        self._env = Environment(
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,
        )

    def templates(self) -> list[str]:
        return sorted(e.name for e in self._root.iterdir() if (e / TEMPLATE_META).is_file())

    def template_info(self, name: str) -> TemplateInfo:
        directory = self._template_dir(name)
        meta = tomllib.loads((directory / TEMPLATE_META).read_text(encoding="utf-8"))
        return TemplateInfo(
            name=name,
            title=str(meta.get("title", name)),
            requires=tuple(meta.get("requires", ())),
            open_on_create=meta.get("open_on_create"),
        )

    def check_board(self, name: str, board: BoardDefinition) -> None:
        info = self.template_info(name)
        for requirement in info.requires:
            if requirement not in _REQUIREMENTS:
                raise ProjectGenerationError(
                    _("Шаблон {name}: невідома вимога «{requirement}» у {file}.").format(
                        name=name, requirement=requirement, file=TEMPLATE_META
                    )
                )
            message, present = _REQUIREMENTS[requirement]
            if not present(board):
                raise ProjectGenerationError(
                    _(message).format(title=info.title, board=board.display_name)
                )

    def render(
        self, name: str, context: dict[str, Any], exclude: Collection[str] = ()
    ) -> list[GeneratedFile]:
        """The files of the project; ``exclude`` lists output paths to leave out
        (".gitignore" for a project that is not going to be a git repository)."""
        error = project_name_error(str(context.get("project_name", "")))
        if error is not None:
            raise ProjectGenerationError(error)
        files: list[GeneratedFile] = []
        for relative, entry in _walk(self._template_dir(name)):
            if relative == PurePosixPath(TEMPLATE_META):
                continue
            text = entry.read_text(encoding="utf-8")
            if relative.name.endswith(".j2"):
                try:
                    text = self._env.from_string(text).render(**context)
                except TemplateError as exc:
                    raise ProjectGenerationError(
                        _("Помилка в шаблоні {name}/{relative}: {error}").format(
                            name=name, relative=relative, error=exc
                        )
                    ) from exc
            path = _output_path(relative)
            if str(path) not in exclude:
                files.append(GeneratedFile(path, text))
        return files

    def generate(
        self,
        name: str,
        destination: Path,
        context: dict[str, Any],
        exclude: Collection[str] = (),
    ) -> list[Path]:
        """Render into ``destination`` (must not exist or be empty); all-or-nothing."""
        try:
            occupied = destination.exists() and (
                not destination.is_dir() or any(destination.iterdir())
            )
        except OSError as exc:
            raise ProjectGenerationError(
                _("Не вдалося створити проєкт: {error}").format(error=exc)
            ) from exc
        if occupied:
            raise ProjectGenerationError(
                _("Тека {destination} уже існує й не порожня.").format(destination=destination)
            )
        files = self.render(name, context, exclude)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # e.g. the parent is a file or is not writable
            raise ProjectGenerationError(
                _("Не вдалося створити проєкт: {error}").format(error=exc)
            ) from exc
        # Not mkdtemp: that makes the folder private (0700), and the project would stay so.
        staging = destination.parent / f".{destination.name}.{uuid.uuid4().hex[:8]}"
        try:
            staging.mkdir()
            for file in files:
                target = staging.joinpath(*file.path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(file.content, encoding="utf-8", newline="\n")
            if destination.exists():
                destination.rmdir()
            staging.rename(destination)
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ProjectGenerationError(
                _("Не вдалося створити проєкт: {error}").format(error=exc)
            ) from exc
        return [destination.joinpath(*f.path.parts) for f in files]

    def _template_dir(self, name: str) -> Traversable:
        directory = self._root / name
        if "/" in name or name.startswith(".") or not (directory / TEMPLATE_META).is_file():
            raise ProjectGenerationError(_("Невідомий шаблон «{name}».").format(name=name))
        return directory
