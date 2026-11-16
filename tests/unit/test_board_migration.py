"""BoardMigration: the configuration files follow the board, the sources are only pointed at."""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

import pytest

from nightexpress.models.entities import LedInfo, UartInfo, UploadMethod
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.board_migration import BoardMigration, BoardMigrationError
from nightexpress.services.file_service import FileService, FileServiceError
from nightexpress.services.project_config_repository import ProjectConfigRepository
from nightexpress.services.project_generator import ProjectGenerator, make_context

CATALOG = BoardCatalog.load()
OLD_BOARD = CATALOG.get_board("stm32f103c6-minimal")
OLD_CHIP = CATALOG.chip_for(OLD_BOARD)
OLD = (OLD_BOARD, OLD_CHIP)
# A board that differs in everything a project depends on.
NEW_CHIP = dataclasses.replace(
    OLD_CHIP,
    id="STM32F411CE",
    family="STM32F4",
    core="cortex-m4",
    target_triple="thumbv7em-none-eabihf",
    flash_kb=512,
    ram_kb=128,
    embassy_feature="stm32f411ce",
    probe_rs_name="STM32F411CE",
    default_sysclk_hz=16_000_000,
)
NEW_BOARD = dataclasses.replace(
    OLD_BOARD,
    id="stm32f411ce-test",
    display_names={"uk": "STM32F411 — тестова плата"},
    chip_id=NEW_CHIP.id,
    aliases=("STM32F411CEU6",),
    led=LedInfo("PA5", active_low=False),
    uart=UartInfo("USART2", "PA2", "PA3"),
)
NEW = (NEW_BOARD, NEW_CHIP)


def make_project(tmp_path: Path, template: str = "blink") -> Path:
    project = tmp_path / "demo"
    ProjectGenerator().generate(template, project, make_context("demo", OLD_BOARD, OLD_CHIP))
    return project


def snapshot(project: Path) -> dict[str, str]:
    return {
        p.relative_to(project).as_posix(): p.read_text() for p in FileService().list_files(project)
    }


def test_generated_project_moves_to_another_board(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    before = snapshot(project)
    migration = BoardMigration()
    plan = migration.plan(project, "demo", OLD, NEW, UploadMethod.PROBE_RS)

    assert snapshot(project) == before  # planning writes nothing
    assert [str(c.relative) for c in plan.changes] == [
        ".cargo/config.toml",
        "rust-toolchain.toml",
        "Cargo.toml",
    ]
    assert plan.target_changed and not any(c.created for c in plan.changes)
    migration.apply(plan)

    config = (project / ".cargo" / "config.toml").read_text()
    assert 'target = "thumbv7em-none-eabihf"   # Cortex-M4 with FPU (STM32F4)' in config
    assert "[target.thumbv7em-none-eabihf]" in config and "thumbv7m-none-eabi" not in config
    toolchain = tomllib.loads((project / "rust-toolchain.toml").read_text())
    assert toolchain["toolchain"]["targets"] == ["thumbv7em-none-eabihf"]
    cargo = (project / "Cargo.toml").read_text()
    assert 'features = ["stm32f411ce", "memory-x"]' in cargo
    assert "(512K flash / 128K RAM)" in cargo and "fit into 512 KB of flash" in cargo
    assert ProjectConfigRepository().read(project).board_id == "stm32f411ce-test"
    # Everything else is byte for byte what it was.
    after = snapshot(project)
    assert after["src/main.rs"] == before["src/main.rs"]
    assert cargo.replace("stm32f411ce", "stm32f103c6").replace("512", "32").replace(
        "128K", "10K"
    ) == before["Cargo.toml"].replace("stm32f103c6-minimal", "stm32f103c6-test")


def test_pins_of_the_old_board_are_pointed_at(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    plan = BoardMigration().plan(project, "demo", OLD, NEW)
    main = project / "src" / "main.rs"
    lines = main.read_text().splitlines()
    assert plan.hints and {h.file for h in plan.hints} == {main}
    for hint in plan.hints:
        assert lines[hint.line - 1][hint.column - 1 :].startswith("PC13")
        assert "світлодіод" in hint.message and "це PA5" in hint.message
    assert len(plan.hints) == main.read_text().count("PC13")
    notes = " ".join(plan.notes)
    assert "STM32F1 → STM32F4" in notes and "8 → 16 МГц" in notes


def test_uart_names_are_pointed_at(tmp_path: Path) -> None:
    project = make_project(tmp_path, "serial_hello")
    plan = BoardMigration().plan(project, "demo", OLD, NEW)
    found = {h.message.split(" ")[0] for h in plan.hints}
    assert {"USART1", "PA9", "PA10"} <= found


def test_same_pins_need_no_hints(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    twin = dataclasses.replace(OLD_BOARD, id="stm32f103c6-twin", display_names={"uk": "Twin"})
    plan = BoardMigration().plan(project, "demo", OLD, (twin, OLD_CHIP))
    assert plan.hints == () and plan.notes == ()
    assert [str(c.relative) for c in plan.changes] == ["Cargo.toml"]  # only the metadata
    assert not plan.target_changed


def test_board_without_a_part_says_so(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    bare = dataclasses.replace(NEW_BOARD, led=None)
    plan = BoardMigration().plan(project, "demo", OLD, (bare, NEW_CHIP))
    assert all("світлодіода немає" in h.message for h in plan.hints) and plan.hints


def test_user_written_configuration_is_kept(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    config = project / ".cargo" / "config.toml"
    config.write_text('# mine\n[build]\njobs = 2\n\n[env]\nFOO = "1"\n')
    (project / "rust-toolchain.toml").unlink()
    migration = BoardMigration()
    plan = migration.plan(project, "demo", OLD, NEW)
    migration.apply(plan)

    text = config.read_text()
    parsed = tomllib.loads(text)
    assert text.startswith("# mine\n") and parsed["build"]["jobs"] == 2 and parsed["env"]
    assert parsed["build"]["target"] == "thumbv7em-none-eabihf"
    assert parsed["target"]["thumbv7em-none-eabihf"]["rustflags"] == ["-C", "link-arg=-Tlink.x"]
    assert not (project / "rust-toolchain.toml").exists()  # optional: not invented


def test_missing_cargo_config_is_created(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / ".cargo" / "config.toml").unlink()
    (project / ".cargo").rmdir()
    migration = BoardMigration()
    plan = migration.plan(project, "demo", OLD, NEW)
    assert plan.changes[0].created
    migration.apply(plan)
    parsed = tomllib.loads((project / ".cargo" / "config.toml").read_text())
    assert parsed["build"]["target"] == "thumbv7em-none-eabihf"


def test_project_without_the_hal_gets_a_note(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    cargo = project / "Cargo.toml"
    cargo.write_text(
        "\n".join(line for line in cargo.read_text().splitlines() if "embassy-stm32" not in line)
    )
    plan = BoardMigration().plan(project, "demo", OLD, NEW)
    assert any("embassy-stm32" in note and "stm32f411ce" in note for note in plan.notes)


def test_unknown_old_board(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    migration = BoardMigration()
    plan = migration.plan(project, "demo", None, NEW)
    assert plan.hints == () and "невідома" in plan.notes[-1]
    migration.apply(plan)
    assert tomllib.loads((project / ".cargo" / "config.toml").read_text())["build"]["target"] == (
        "thumbv7em-none-eabihf"
    )
    cargo = tomllib.loads((project / "Cargo.toml").read_text())
    assert cargo["dependencies"]["embassy-stm32"]["features"] == ["stm32f411ce", "memory-x"]


def test_upload_method_follows_what_the_board_supports(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    migration = BoardMigration()
    assert migration.plan(
        project, "demo", OLD, NEW, UploadMethod.SERIAL_BOOTLOADER
    ).upload_method is (UploadMethod.SERIAL_BOOTLOADER)
    probe_only = dataclasses.replace(
        NEW_BOARD,
        upload_methods=tuple(
            m for m in NEW_BOARD.upload_methods if m.method is UploadMethod.PROBE_RS
        ),
    )
    plan = migration.plan(
        project, "demo", OLD, (probe_only, NEW_CHIP), UploadMethod.SERIAL_BOOTLOADER
    )
    assert plan.upload_method is UploadMethod.PROBE_RS


def test_broken_toml_is_reported_before_anything_is_written(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "rust-toolchain.toml").write_text("[toolchain\n")
    before = snapshot(project)
    with pytest.raises(BoardMigrationError, match=r"rust-toolchain\.toml"):
        BoardMigration().plan(project, "demo", OLD, NEW)
    assert snapshot(project) == before


def test_failed_write_puts_the_files_back(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    before = snapshot(project)

    class FailingFiles(FileService):
        def write_text_atomic(self, path: Path, text: str) -> None:
            if path.name == "Cargo.toml":
                raise FileServiceError("disk full")
            super().write_text_atomic(path, text)

    migration = BoardMigration(FailingFiles())
    plan = migration.plan(project, "demo", OLD, NEW)
    with pytest.raises(BoardMigrationError, match="disk full"):
        migration.apply(plan)
    assert snapshot(project) == before
