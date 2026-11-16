"""Modules a project can be given in one step ("Add peripheral"): sensors, displays…

A peripheral lives in ``resources/peripherals/<id>/``: ``peripheral.toml`` (name, wiring, the
code to insert) and the driver, a Rust source file copied into the project. Adding one:

- copies the driver to ``src/<module>.rs`` and adds ``mod <module>;`` to ``main.rs``;
- adds what the driver and the inserted code need to ``Cargo.toml`` (embedded-hal, defmt,
  defmt-rtt) and makes defmt work (``.cargo/config.toml``);
- inserts the set-up right after ``embassy_stm32::init`` and a reading at the top of the main
  ``loop``, with the pins chosen in the dialog (the board's usual ones by default). Where
  ``main.rs`` does not look like that, the code is left for the user to paste
  (``InstallPlan.manual``).

Modules on the same I2C bus share it: the first one creates the bus (``let i2c1 =
RefCell::new(I2c::new_blocking(...))``), each module talks through its own
``embedded_hal_bus::i2c::RefCellDevice`` on it, so a GY-87 (MPU-6050 + BMP180) needs two wires.

Planning only reads; nothing is written until ``apply``.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath

import jinja2
import tomlkit
from tomlkit.exceptions import TOMLKitError

from nightexpress.i18n import _, localized
from nightexpress.models.entities import BoardDefinition, I2cPinChoice
from nightexpress.models.entities.language import localized_texts
from nightexpress.services.file_service import FileService

DESCRIPTION_FILE = "peripheral.toml"
MAIN_RS = PurePosixPath("src/main.rs")
CARGO_TOML = PurePosixPath("Cargo.toml")
CARGO_CONFIG = PurePosixPath(".cargo/config.toml")
# What the drivers and the inserted code use, with the versions the templates use.
DEPENDENCIES = {
    "embedded-hal": "1.0",
    "embedded-hal-bus": "0.3",  # one I2C bus shared by several modules
    "defmt": "1.0",
    "defmt-rtt": "1.0",
}
DEFMT_LINK_ARG = "link-arg=-Tdefmt.x"

_INIT_RE = re.compile(r"^(?P<indent>[ \t]*)let\s+(?:mut\s+)?(?P<p>\w+)\s*=\s*embassy_stm32::init\(")
_LOOP_RE = re.compile(r"^(?P<indent>[ \t]*)loop\s*\{\s*(//.*)?$")
_INNER_ATTRIBUTE_RE = re.compile(r"^#!\[")
# let i2c1 = core::cell::RefCell::new(embassy_stm32::i2c::I2c::new_blocking(p.I2C1, p.PB6, p.PB7,
_BUS_RE = re.compile(
    r"^(?P<indent>[ \t]*)let\s+(?P<var>\w+)\s*=\s*core::cell::RefCell::new\(\s*"
    r"embassy_stm32::i2c::I2c::new_blocking\(\s*\w+\.(?P<bus>I2C\d+),\s*\w+\.(?P<scl>P[A-K]\d+),"
    r"\s*\w+\.(?P<sda>P[A-K]\d+)",
    re.MULTILINE,
)


class PeripheralError(Exception):
    """Why the peripheral cannot be added to this project (shown to the user)."""


@dataclass(frozen=True)
class PeripheralDefinition:
    id: str
    name: str
    module: str  # the driver's file name and `mod` name
    bus: str  # "i2c"
    descriptions: Mapping[str, str]
    wiring: tuple[tuple[str, str], ...]  # module pin -> board pin, "{scl}" / "{sda}" placeholders
    init: str  # Jinja2 snippets
    loop: str
    driver: str  # the Rust source of the driver

    @property
    def description(self) -> str:
        return localized(self.descriptions)


@dataclass(frozen=True)
class FileChange:
    path: PurePosixPath  # relative to the project folder
    text: str
    created: bool  # a new file, not a change of one


@dataclass(frozen=True)
class InstallPlan:
    peripheral: PeripheralDefinition
    wiring: tuple[tuple[str, str], ...]  # with the chosen pins
    pins: I2cPinChoice
    shares_bus: bool  # the bus is already there (another module): only the module is added
    changes: tuple[FileChange, ...]
    manual: str  # code the user has to paste because main.rs has no place for it; or ""


def load_peripherals(directory: Traversable | None = None) -> list[PeripheralDefinition]:
    root = directory or resources.files("nightexpress") / "resources" / "peripherals"
    found = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        description = entry / DESCRIPTION_FILE
        if not entry.is_dir() or not description.is_file():
            continue
        data = tomllib.loads(description.read_text(encoding="utf-8"))
        module = str(data["module"])
        found.append(
            PeripheralDefinition(
                id=str(data["id"]),
                name=str(data["name"]),
                module=module,
                bus=str(data["bus"]),
                descriptions=localized_texts(data["description"]),
                wiring=tuple((str(a), str(b)) for a, b in data["wiring"]),
                init=str(data["init"]).strip("\n"),
                loop=str(data["loop"]).strip("\n"),
                driver=(entry / f"{module}.rs").read_text(encoding="utf-8"),
            )
        )
    return found


def default_pins(board: BoardDefinition, peripheral: PeripheralDefinition) -> I2cPinChoice:
    """The board's usual bus for modules (PB6/PB7 on a Blue Pill)."""
    if peripheral.bus != "i2c" or board.i2c is None:
        raise PeripheralError(
            _("На платі «{board}» не описано шину I2C.").format(board=board.display_name)
        )
    return I2cPinChoice(board.i2c.peripheral, board.i2c.scl_pin, board.i2c.sda_pin)


def wiring_for(
    peripheral: PeripheralDefinition, pins: I2cPinChoice
) -> tuple[tuple[str, str, str | None], ...]:
    """(module pin, board pin, role) rows; role "scl"/"sda" marks a pin the user can choose."""
    rows = []
    for module_pin, board_pin in peripheral.wiring:
        role = board_pin.strip("{}") if board_pin in ("{scl}", "{sda}") else None
        rows.append((module_pin, board_pin.format(scl=pins.scl_pin, sda=pins.sda_pin), role))
    return tuple(rows)


def read_main(project_dir: Path, files: FileService | None = None) -> str:
    return _read(files or FileService(), project_dir, MAIN_RS)


def existing_i2c_buses(main: str) -> list[I2cPinChoice]:
    """Buses created by "Add peripheral" in ``main.rs``: a new module can join one of them."""
    return [I2cPinChoice(m["bus"], m["scl"], m["sda"]) for m in _BUS_RE.finditer(main)]


def plan_install(
    project_dir: Path,
    peripheral: PeripheralDefinition,
    board: BoardDefinition,
    files: FileService | None = None,
    pins: I2cPinChoice | None = None,
) -> InstallPlan:
    """What adding ``peripheral`` on ``pins`` (default: the board's usual bus) changes in the
    project; raises PeripheralError if it can't."""
    files = files or FileService()
    choice = pins or default_pins(board, peripheral)
    wiring = tuple(
        (module_pin, board_pin.format(scl=choice.scl_pin, sda=choice.sda_pin))
        for module_pin, board_pin in peripheral.wiring
    )

    driver_path = PurePosixPath("src") / f"{peripheral.module}.rs"
    if (project_dir / driver_path).exists():
        raise PeripheralError(
            _("{name} уже є в проєкті: {path}.").format(name=peripheral.name, path=driver_path)
        )
    main = _read(files, project_dir, MAIN_RS)
    if re.search(rf"^\s*mod\s+{peripheral.module}\b", main, re.MULTILINE):
        raise PeripheralError(_("{name} уже додано до проєкту.").format(name=peripheral.name))

    main_text, manual, shares_bus = _edit_main(main, peripheral, choice)
    changes = [FileChange(driver_path, peripheral.driver, created=True)]
    if main_text != main:
        changes.append(FileChange(MAIN_RS, main_text, created=False))
    cargo = _read(files, project_dir, CARGO_TOML)
    if (cargo_text := _with_dependencies(cargo)) != cargo:
        changes.append(FileChange(CARGO_TOML, cargo_text, created=False))
    config = _read(files, project_dir, CARGO_CONFIG)
    if (config_text := _with_defmt(config)) != config:
        changes.append(FileChange(CARGO_CONFIG, config_text, created=False))
    return InstallPlan(peripheral, wiring, choice, shares_bus, tuple(changes), manual)


def apply(project_dir: Path, plan: InstallPlan, files: FileService | None = None) -> None:
    files = files or FileService()
    for change in plan.changes:
        files.write_text_atomic(project_dir / change.path, change.text)


# --- main.rs ---------------------------------------------------------------------------------


def _render(snippet: str, values: Mapping[str, str]) -> str:
    return jinja2.Template(snippet, undefined=jinja2.StrictUndefined).render(**values)


def _indented(text: str, indent: str) -> list[str]:
    return [indent + line if line.strip() else "" for line in text.splitlines()]


def _edit_main(
    main: str, peripheral: PeripheralDefinition, pins: I2cPinChoice
) -> tuple[str, str, bool]:
    """``main.rs`` with the module, the set-up and the reading; the code left to paste; whether
    the module joins a bus that is already there."""
    lines = main.splitlines()
    init_at = next((i for i, line in enumerate(lines) if _INIT_RE.match(line)), None)
    name = _INIT_RE.match(lines[init_at]).group("p") if init_at is not None else "p"  # type: ignore[union-attr]
    bus_var = pins.peripheral.lower()
    shared = next((m for m in _BUS_RE.finditer(main) if m["bus"] == pins.peripheral), None)
    if shared is not None:
        if (shared["scl"], shared["sda"]) != (pins.scl_pin, pins.sda_pin):
            raise PeripheralError(
                _(
                    "{bus} уже підключено до {scl}/{sda}. Виберіть ці виводи, щоб модуль став "
                    "на ту саму шину, або іншу шину I2C."
                ).format(bus=pins.peripheral, scl=shared["scl"], sda=shared["sda"])
            )
        bus_var = shared["var"]
    else:
        used = re.compile(
            rf"\b{re.escape(name)}\.({pins.peripheral}|{pins.scl_pin}|{pins.sda_pin})\b"
        )
        if used.search(main):
            raise PeripheralError(
                _(
                    "У main.rs уже використано {bus} або виводи {scl}/{sda}: модуль не можна "
                    "підключити до них удруге."
                ).format(bus=pins.peripheral, scl=pins.scl_pin, sda=pins.sda_pin)
            )
    values = {
        "p": name,
        "bus": pins.peripheral,
        "scl": pins.scl_pin,
        "sda": pins.sda_pin,
        "i2c": f"embedded_hal_bus::i2c::RefCellDevice::new(&{bus_var})",
    }
    init, loop = _render(peripheral.init, values), _render(peripheral.loop, values)
    bus = (
        []
        if shared is not None
        else [
            f"// {pins.peripheral}: SCL = {pins.scl_pin}, SDA = {pins.sda_pin}, shared by the "
            'modules on it (added by "Add peripheral").',
            f"let {bus_var} = core::cell::RefCell::new(embassy_stm32::i2c::I2c::new_blocking(",
            f"    {name}.{pins.peripheral}, {name}.{pins.scl_pin}, {name}.{pins.sda_pin}, "
            "Default::default(),",
            "));",
        ]
    )

    manual: list[str] = []
    loop_at = None
    if init_at is not None:
        loop_at = next(
            (i for i in range(init_at + 1, len(lines)) if _LOOP_RE.match(lines[i])), None
        )
    if init_at is None or loop_at is None:
        manual = [*bus, *([""] if bus else []), init, "", "// " + _("на початку циклу loop:"), loop]
    else:
        loop_indent = _LOOP_RE.match(lines[loop_at]).group("indent") + "    "  # type: ignore[union-attr]
        lines[loop_at + 1 : loop_at + 1] = [*_indented(loop, loop_indent), ""]
        if shared is not None:
            # The bus is above the loop, so its lines have not moved: after its closing "));".
            at = main[: shared.start()].count("\n")
            at = next(i for i in range(at, len(lines)) if lines[i].rstrip().endswith(");"))
            indent = shared["indent"]
            lines[at + 1 : at + 1] = ["", *_indented(init, indent)]
        else:
            indent = _INIT_RE.match(lines[init_at]).group("indent")  # type: ignore[union-attr]
            block = [*_indented("\n".join(bus), indent), "", *_indented(init, indent)]
            lines[init_at + 1 : init_at + 1] = ["", *block]

    # `mod` and the RTT transport go right after the inner attributes (#![no_std], #![no_main]).
    head = [f'mod {peripheral.module}; // the driver, added by "Add peripheral"']
    if not re.search(r"\bdefmt_rtt\b", main):
        head.append("use defmt_rtt as _; // defmt messages go to the debug probe (the RTT tab)")
    attributes = [i for i, line in enumerate(lines) if _INNER_ATTRIBUTE_RE.match(line)]
    modules = [i for i, line in enumerate(lines) if "added by \"Add peripheral\"" in line
               and line.startswith("mod ")]  # fmt: skip
    if modules:  # next to the modules added before
        lines[modules[-1] + 1 : modules[-1] + 1] = head
    else:
        at = attributes[-1] + 1 if attributes else 0
        lines[at:at] = ["", *head] if attributes else [*head, ""]
    text = "\n".join(lines) + ("\n" if main.endswith("\n") else "")
    return text, "\n".join(manual), shared is not None


# --- Cargo.toml and .cargo/config.toml -------------------------------------------------------


def _read(files: FileService, project_dir: Path, relative: PurePosixPath) -> str:
    path = project_dir / relative
    if not path.is_file():
        raise PeripheralError(_("У проєкті немає файлу {path}.").format(path=relative))
    return files.read_text(path)


def _parse(text: str, relative: PurePosixPath) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(text)
    except TOMLKitError as exc:
        raise PeripheralError(
            _("{path} містить помилку: {error}").format(path=relative, error=exc)
        ) from exc


def _with_dependencies(cargo: str) -> str:
    doc = _parse(cargo, CARGO_TOML)
    dependencies = doc.get("dependencies")
    if dependencies is None:
        dependencies = tomlkit.table()
        doc["dependencies"] = dependencies
    for crate, version in DEPENDENCIES.items():
        if crate not in dependencies:
            dependencies[crate] = version
    return tomlkit.dumps(doc)


def _with_defmt(config: str) -> str:
    doc = _parse(config, CARGO_CONFIG)
    target = doc.get("build", {}).get("target")
    if not isinstance(target, str):
        raise PeripheralError(_("У .cargo/config.toml не вказано build.target."))
    table = doc.setdefault("target", tomlkit.table(is_super_table=True)).setdefault(
        target, tomlkit.table()
    )
    flags = table.get("rustflags")
    if flags is None:
        table["rustflags"] = ["-C", "link-arg=-Tlink.x", "-C", DEFMT_LINK_ARG]
    elif DEFMT_LINK_ARG not in list(flags):
        flags.extend(["-C", DEFMT_LINK_ARG])
    env = doc.setdefault("env", tomlkit.table())
    if "DEFMT_LOG" not in env:
        env["DEFMT_LOG"] = "info"
    return tomlkit.dumps(doc)
