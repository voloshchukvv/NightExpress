"""Move an existing project to another board.

The board is written into a project in several places when it is created: the target triple
(``.cargo/config.toml``, ``rust-toolchain.toml``), the chip feature of ``embassy-stm32`` and
our metadata (``Cargo.toml``). Those are rewritten here, keeping the user's text around them.

The sources are never changed: the pins in them are the user's code by now. Instead the
places that still name pins of the old board are reported as :class:`PinHint` items.

A migration is planned first (:meth:`BoardMigration.plan`, nothing is written) and applied
after the user confirmed it (:meth:`BoardMigration.apply`, all files or none).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import tomlkit
from tomlkit.exceptions import TOMLKitError

from nightexpress.i18n import _
from nightexpress.models.entities import BoardDefinition, ChipDefinition, UploadMethod
from nightexpress.services.file_service import FileService, FileServiceError
from nightexpress.services.project_config_repository import (
    ProjectConfigError,
    ProjectConfigRepository,
)
from nightexpress.services.project_generator import (
    ProjectGenerationError,
    ProjectGenerator,
    core_description,
    make_context,
)

CARGO_CONFIG = PurePosixPath(".cargo/config.toml")
RUST_TOOLCHAIN = PurePosixPath("rust-toolchain.toml")
CARGO_TOML = PurePosixPath("Cargo.toml")
SOURCE_DIR = "src"
HAL_CRATE = "embassy-stm32"
LINKER_FLAGS = ("-C", "link-arg=-Tlink.x")
MAX_HINTS = 200

_CHIP_FEATURE_RE = re.compile(r"^stm32[a-z0-9]+$")


class BoardMigrationError(Exception):
    """User-facing message in Ukrainian."""


@dataclass(frozen=True)
class FileChange:
    path: Path
    relative: PurePosixPath
    text: str
    created: bool  # the file did not exist


@dataclass(frozen=True)
class PinHint:
    """A place in the sources that names something of the old board."""

    file: Path
    line: int
    column: int
    message: str


@dataclass(frozen=True)
class MigrationPlan:
    project_dir: Path
    board: BoardDefinition
    chip: ChipDefinition
    upload_method: UploadMethod
    changes: tuple[FileChange, ...]
    hints: tuple[PinHint, ...]
    notes: tuple[str, ...]  # things no file change covers; shown to the user as they are

    @property
    def target_changed(self) -> bool:
        return any(change.relative == CARGO_CONFIG for change in self.changes)


class BoardMigration:
    def __init__(
        self,
        files: FileService | None = None,
        config_repository: ProjectConfigRepository | None = None,
        generator: ProjectGenerator | None = None,
    ) -> None:
        self._files = files or FileService()
        self._config = config_repository or ProjectConfigRepository(self._files)
        self._generator = generator or ProjectGenerator()

    # --- planning ----------------------------------------------------------------------------

    def plan(
        self,
        project_dir: Path,
        project_name: str,
        old: tuple[BoardDefinition, ChipDefinition] | None,
        new: tuple[BoardDefinition, ChipDefinition],
        upload_method: UploadMethod | None = None,
    ) -> MigrationPlan:
        """What moving the project to ``new`` takes. ``old`` is None for an unknown board."""
        board, chip = new
        old_chip = old[1] if old else None
        method = (
            upload_method
            if upload_method is not None and board.supports(upload_method)
            else board.default_upload_method
        )
        notes: list[str] = []
        try:
            texts = {
                CARGO_CONFIG: self._cargo_config(project_dir, project_name, old_chip, new),
                RUST_TOOLCHAIN: self._rust_toolchain(project_dir, old_chip, chip),
                CARGO_TOML: self._cargo_toml(project_dir, old_chip, new, method, notes),
            }
        except FileServiceError as exc:
            raise BoardMigrationError(str(exc)) from exc
        changes = []
        for relative, text in texts.items():
            path = project_dir.joinpath(*relative.parts)
            current = self._read(path)
            if text is not None and text != current:
                changes.append(FileChange(path, relative, text, created=current is None))

        hints: tuple[PinHint, ...] = ()
        if old is None:
            notes.append(_("Попередня плата невідома: піни в коді перевірте самостійно."))
        else:
            hints = self._pin_hints(project_dir, old[0], board)
            notes.extend(_general_notes(old, new))
        return MigrationPlan(project_dir, board, chip, method, tuple(changes), hints, tuple(notes))

    def _read(self, path: Path) -> str | None:
        return self._files.read_text(path) if path.is_file() else None

    def _cargo_config(
        self,
        project_dir: Path,
        project_name: str,
        old_chip: ChipDefinition | None,
        new: tuple[BoardDefinition, ChipDefinition],
    ) -> str:
        board, chip = new
        triple = chip.target_triple
        text = self._read(project_dir.joinpath(*CARGO_CONFIG.parts))
        if text is None:
            # The same file a new project gets.
            try:
                rendered = self._generator.render("blank", make_context(project_name, board, chip))
            except ProjectGenerationError as exc:
                raise BoardMigrationError(str(exc)) from exc
            return next(f.content for f in rendered if f.path == CARGO_CONFIG)
        if old_chip is not None and old_chip.target_triple != triple:
            text = text.replace(old_chip.target_triple, triple)
            text = text.replace(core_description(old_chip), core_description(chip))
        doc = _parse(text, CARGO_CONFIG)
        build = doc.get("build")
        targets = doc.get("target")
        has_flags = isinstance(targets, dict) and triple in targets
        if isinstance(build, dict) and build.get("target") == triple and has_flags:
            return text
        # The file is not the one we generated: set the values and leave the rest alone.
        if not isinstance(build, dict):
            build = tomlkit.table()
            doc["build"] = build
        build["target"] = triple
        if not has_flags:
            if not isinstance(targets, dict):
                targets = tomlkit.table(is_super_table=True)
                doc["target"] = targets
            flags = tomlkit.table()
            flags["rustflags"] = list(LINKER_FLAGS)
            targets[triple] = flags
        return tomlkit.dumps(doc)

    def _rust_toolchain(
        self, project_dir: Path, old_chip: ChipDefinition | None, chip: ChipDefinition
    ) -> str | None:
        text = self._read(project_dir.joinpath(*RUST_TOOLCHAIN.parts))
        if text is None:
            return None  # optional: without it rustup's default toolchain is used
        triple = chip.target_triple
        if old_chip is not None and old_chip.target_triple != triple:
            text = text.replace(old_chip.target_triple, triple)
        doc = _parse(text, RUST_TOOLCHAIN)
        toolchain = doc.get("toolchain")
        if not isinstance(toolchain, dict):
            return text  # the legacy one-line format or something of the user's own
        targets = toolchain.get("targets")
        if isinstance(targets, list) and triple in targets:
            return text
        if isinstance(targets, list):
            targets.append(triple)
        else:
            toolchain["targets"] = [triple]
        return tomlkit.dumps(doc)

    def _cargo_toml(
        self,
        project_dir: Path,
        old_chip: ChipDefinition | None,
        new: tuple[BoardDefinition, ChipDefinition],
        method: UploadMethod,
        notes: list[str],
    ) -> str:
        board, chip = new
        feature = chip.embassy_feature
        text = self._files.read_text(project_dir.joinpath(*CARGO_TOML.parts))
        if old_chip is not None:
            text = text.replace(f'"{old_chip.embassy_feature}"', f'"{feature}"')
            # The comments of our templates that quote the memory of the chip.
            text = text.replace(
                f"({old_chip.flash_kb}K flash / {old_chip.ram_kb}K RAM)",
                f"({chip.flash_kb}K flash / {chip.ram_kb}K RAM)",
            )
            text = text.replace(
                f"fit into {old_chip.flash_kb} KB of flash", f"fit into {chip.flash_kb} KB of flash"
            )
        doc = _parse(text, CARGO_TOML)
        dependencies = doc.get("dependencies")
        hal = dependencies.get(HAL_CRATE) if isinstance(dependencies, dict) else None
        features = hal.get("features") if isinstance(hal, dict) else None
        if not isinstance(features, list):
            notes.append(
                _(
                    "У Cargo.toml немає залежності {crate} зі списком features: чип ({feature}) у "
                    "залежностях вкажіть самостійно."
                ).format(crate=HAL_CRATE, feature=feature)
            )
        elif feature not in features:
            index = next(
                (i for i, f in enumerate(features) if _CHIP_FEATURE_RE.match(str(f))), None
            )
            if index is None:
                features.insert(0, feature)
            else:
                features[index] = feature
            text = tomlkit.dumps(doc)
        try:
            return self._config.with_metadata(text, board.id, method)
        except ProjectConfigError as exc:
            raise BoardMigrationError(str(exc)) from exc

    def _pin_hints(
        self, project_dir: Path, old: BoardDefinition, new: BoardDefinition
    ) -> tuple[PinHint, ...]:
        searched = _changed_names(old, new)
        if not searched:
            return ()
        pattern = re.compile(r"\b(" + "|".join(re.escape(name) for name in searched) + r")\b")
        hints: list[PinHint] = []
        source_dir = project_dir / SOURCE_DIR
        if not source_dir.is_dir():
            return ()
        for path in self._files.list_files(source_dir):
            if path.suffix != ".rs":
                continue
            try:
                lines = self._files.read_text(path).splitlines()
            except FileServiceError:
                continue  # binary or huge: nothing to point at
            for number, line in enumerate(lines, start=1):
                for match in pattern.finditer(line):
                    hints.append(PinHint(path, number, match.start() + 1, searched[match.group(1)]))
                    if len(hints) >= MAX_HINTS:
                        return tuple(hints)
        return tuple(hints)

    # --- applying ----------------------------------------------------------------------------

    def apply(self, plan: MigrationPlan) -> None:
        """Write the planned files; on a failure the ones already written are put back."""
        originals: list[tuple[FileChange, str | None]] = []
        try:
            for change in plan.changes:
                original = self._read(change.path)
                change.path.parent.mkdir(parents=True, exist_ok=True)
                self._files.write_text_atomic(change.path, change.text)
                originals.append((change, original))
        except (FileServiceError, OSError) as exc:
            for change, original in reversed(originals):
                try:
                    if original is None:
                        change.path.unlink(missing_ok=True)
                    else:
                        self._files.write_text_atomic(change.path, original)
                except (FileServiceError, OSError):
                    pass  # nothing more can be done; the first error is the one reported
            raise BoardMigrationError(
                _("Не вдалося змінити плату: {error}").format(error=exc)
            ) from exc


def _parse(text: str, name: PurePosixPath) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(text)
    except TOMLKitError as exc:
        raise BoardMigrationError(
            _("{name} містить помилку: {error}").format(name=name, error=exc)
        ) from exc


def _changed_names(old: BoardDefinition, new: BoardDefinition) -> dict[str, str]:
    """Pin and peripheral names of the old board that mean something else on the new one,
    with the message to show at each of their uses."""
    roles: list[tuple[str, str, str | None, str | None]] = [
        # (what it is, the same in the genitive case, old name, new name)
        (_("світлодіод"), _("світлодіода"), old.led and old.led.pin, new.led and new.led.pin),
        (_("кнопка"), _("кнопки"), old.button and old.button.pin, new.button and new.button.pin),
        ("UART TX", "UART", old.uart and old.uart.tx_pin, new.uart and new.uart.tx_pin),
        ("UART RX", "UART", old.uart and old.uart.rx_pin, new.uart and new.uart.rx_pin),
        ("UART", "UART", old.uart and old.uart.peripheral, new.uart and new.uart.peripheral),
    ]
    names: dict[str, str] = {}
    for role, genitive, old_name, new_name in roles:
        if not old_name or old_name == new_name or old_name in names:
            continue
        if new_name:
            advice = _("на платі «{board}» це {new_name}").format(
                board=new.display_name, new_name=new_name
            )
        else:
            advice = _("на платі «{board}» {genitive} немає").format(
                board=new.display_name, genitive=genitive
            )
        names[old_name] = _("{old_name} — {role} попередньої плати; {advice}").format(
            old_name=old_name, role=role, advice=advice
        )
    return names


def _general_notes(
    old: tuple[BoardDefinition, ChipDefinition], new: tuple[BoardDefinition, ChipDefinition]
) -> list[str]:
    (old_board, old_chip), (board, chip) = old, new
    notes = []
    if old_chip.family != chip.family:
        notes.append(
            _(
                "Інше сімейство чипа ({old_family} → {new_family}): назви периферії та її "
                "налаштування в {crate} можуть відрізнятися."
            ).format(old_family=old_chip.family, new_family=chip.family, crate=HAL_CRATE)
        )
    if old_chip.default_sysclk_hz != chip.default_sysclk_hz:
        notes.append(
            _(
                "Інша частота ядра після старту ({old_mhz} → {new_mhz} МГц): затримки, пораховані "
                "в циклах, триватимуть інакше."
            ).format(
                old_mhz=old_chip.default_sysclk_hz // 1_000_000,
                new_mhz=chip.default_sysclk_hz // 1_000_000,
            )
        )
    if (
        old_board.led
        and board.led
        and old_board.led.pin == board.led.pin
        and old_board.led.active_low != board.led.active_low
    ):
        level = "LOW" if board.led.active_low else "HIGH"
        notes.append(
            _("Світлодіод на {pin} тепер вмикається рівнем {level}.").format(
                pin=board.led.pin, level=level
            )
        )
    if chip.flash_kb < old_chip.flash_kb or chip.ram_kb < old_chip.ram_kb:
        notes.append(
            _(
                "У нового чипа менше пам'яті ({flash_kb} KB flash, {ram_kb} KB RAM): програма може "
                "не вміститися."
            ).format(flash_kb=chip.flash_kb, ram_kb=chip.ram_kb)
        )
    return notes
