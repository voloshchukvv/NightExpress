from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

import pytest

from nightexpress.models.entities import (
    BoardDefinition,
    ButtonInfo,
    LedInfo,
    UartInfo,
    UploadMethod,
)
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.project_generator import (
    ProjectGenerationError,
    ProjectGenerator,
    make_context,
)
from nightexpress.services.toolchain_probe import ToolchainProbe

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = ROOT / "reference"
# template -> its hand-checked reference project for the F103C6 board
TEMPLATE_REFERENCES = {
    "blink": "blinky_f103c6",
    "serial_hello": "serial_echo_f103c6",
    "button": "button_f103c6",
    "rtt_hello": "rtt_hello_f103c6",
}
CATALOG = BoardCatalog.load()
BOARD = CATALOG.get_board("stm32f103c6-minimal")
CHIP = CATALOG.chip_for(BOARD)


def context(name: str = "blinky_f103c6", **board_changes: object) -> dict:
    board = dataclasses.replace(BOARD, **board_changes) if board_changes else BOARD
    return make_context(name, board, CHIP)


def test_templates_cover_every_catalog_example() -> None:
    generator = ProjectGenerator()
    assert {e.template for e in CATALOG.examples} <= set(generator.templates())
    info = generator.template_info("blink")
    assert info.title == "Blink" and info.requires == ("led",)
    assert info.open_on_create == "src/main.rs"


def test_example_templates_have_reference_projects() -> None:
    assert {example.template for example in CATALOG.examples} == set(TEMPLATE_REFERENCES)
    assert set(ProjectGenerator().templates()) == {*TEMPLATE_REFERENCES, "blank"}
    infos = {name: ProjectGenerator().template_info(name) for name in TEMPLATE_REFERENCES}
    assert infos["serial_hello"].requires == ("led", "uart")
    assert infos["button"].requires == ("led", "button")


@pytest.mark.parametrize(("template", "reference"), TEMPLATE_REFERENCES.items())
def test_template_matches_reference_project_byte_for_byte(template: str, reference: str) -> None:
    files = ProjectGenerator().render(template, context(reference))
    paths = {f.path for f in files}
    assert paths == {
        PurePosixPath(p)
        for p in (
            "Cargo.toml",
            "rust-toolchain.toml",
            ".cargo/config.toml",
            ".gitignore",
            "README.md",
            "src/main.rs",
        )
    }
    for file in files:
        if file.path.name == "README.md":  # the reference README documents the hardware check
            continue
        expected = (REFERENCES / reference / file.path).read_text(encoding="utf-8")
        assert file.content == expected, file.path


@pytest.mark.parametrize(
    ("board", "template"),
    [
        pytest.param(board, template, id=f"{board.id}-{template}")
        for board in CATALOG.boards
        for template in ("blank", *(example.template for example in CATALOG.examples_for(board.id)))
    ],
)
def test_generated_readme_matches_board_and_template(
    tmp_path: Path, board: BoardDefinition, template: str
) -> None:
    generator = ProjectGenerator()
    chip = CATALOG.chip_for(board)
    project = tmp_path / "readme_example"
    generator.check_board(template, board)
    generator.generate(template, project, make_context(project.name, board, chip))
    readme = (project / "README.md").read_text(encoding="utf-8")

    assert readme.startswith(f"# {project.name}\n")
    assert f"**{generator.template_info(template).title}**" in readme
    assert board.display_name_en and f"**{board.display_name_en}**" in readme
    assert f"`{chip.id}` microcontroller" in readme
    assert f"{chip.flash_kb} KB of flash and {chip.ram_kb} KB of RAM" in readme
    assert "cargo build --release" in readme
    assert f"`target/{chip.target_triple}/release/{project.name}`" in readme
    assert not re.search(r"[\u0400-\u04ff]", readme)


def test_context_values() -> None:
    ctx = context()
    assert ctx["core_description"] == "Cortex-M3 without FPU (STM32F1)"
    assert ctx["sysclk_mhz"] == "8"
    assert ctx["half_second_cycles_rust"] == "4_000_000"
    assert ctx["slice_20us_cycles"] == 160
    assert (ctx["uart_peripheral"], ctx["uart_tx_pin"], ctx["uart_rx_pin"]) == (
        "USART1",
        "PA9",
        "PA10",
    )
    assert (ctx["button_pin"], ctx["button_active_low"], ctx["button_on_board"]) == (
        "PA0",
        True,
        False,
    )
    assert ctx["upload_method"] == "probe-rs"
    assert make_context("x", BOARD, CHIP, UploadMethod.SERIAL_BOOTLOADER)["upload_method"] == (
        "serial-bootloader"
    )
    fallback = make_context(
        "x", dataclasses.replace(BOARD, display_names={"uk": BOARD.display_name}), CHIP
    )
    assert fallback["board_name_en"] == BOARD.id


def test_active_high_led_variant() -> None:
    files = ProjectGenerator().render("blink", context(led=LedInfo("PA5", active_low=False)))
    main = next(f.content for f in files if f.path.name == "main.rs")
    assert "Output::new(p.PA5, Level::Low, Speed::Low)" in main
    assert "lights up when the pin is driven HIGH" in main
    assert "PC13" not in main
    readme = next(f.content for f in files if f.path.name == "README.md")
    assert "LED on `PA5`" in readme and "active high" in readme


@pytest.mark.parametrize(
    ("template", "missing", "message"),
    [
        ("blink", "led", "потребує світлодіода"),
        ("serial_hello", "uart", "потребує UART"),
        ("button", "button", "потребує кнопки"),
    ],
)
def test_board_without_required_part_is_rejected(template: str, missing: str, message: str) -> None:
    generator = ProjectGenerator()
    generator.check_board(template, BOARD)  # the F103C6 board has everything
    with pytest.raises(ProjectGenerationError, match=message):
        generator.check_board(template, dataclasses.replace(BOARD, **{missing: None}))


def test_unknown_requirement_is_reported(tmp_path: Path) -> None:
    (tmp_path / "odd").mkdir()
    (tmp_path / "odd" / "template.toml").write_text('title = "Odd"\nrequires = ["laser"]\n')
    with pytest.raises(ProjectGenerationError, match="невідома вимога «laser»"):
        ProjectGenerator(tmp_path).check_board("odd", BOARD)


def test_serial_hello_uses_board_uart_pins() -> None:
    uart = UartInfo("USART2", tx_pin="PA2", rx_pin="PA3")
    files = ProjectGenerator().render("serial_hello", context(uart=uart))
    main = next(f.content for f in files if f.path.name == "main.rs")
    assert "Uart::new_blocking(p.USART2, p.PA3, p.PA2, Config::default())" in main
    readme = next(f.content for f in files if f.path.name == "README.md")
    assert "`USART2`" in readme
    assert "| `PA2` (TX) | RX |" in readme and "| `PA3` (RX) | TX |" in readme


def test_button_variants() -> None:
    def render(button: ButtonInfo, led: LedInfo) -> tuple[str, str]:
        files = ProjectGenerator().render("button", context(button=button, led=led))
        by_name = {f.path.name: f.content for f in files}
        return by_name["main.rs"], by_name["README.md"]

    main, readme = render(ButtonInfo("PC13", active_low=True, on_board=True), LedInfo("PA5", False))
    assert "Input::new(p.PC13, Pull::Up)" in main and "button.is_low()" in main
    assert "if pressed { Level::High } else { Level::Low }" in main
    assert "no user button" not in main and "Connect an external button" not in readme

    main, readme = render(ButtonInfo("PB1", active_low=False, on_board=False), BOARD.led)
    assert "Input::new(p.PB1, Pull::Down)" in main and "button.is_high()" in main
    assert "connect one between PB1 and 3.3V" in main
    assert "between `PB1` and `3.3V`" in readme and "pull-down" in readme


def test_invalid_project_name_is_rejected() -> None:
    with pytest.raises(ProjectGenerationError, match="латинської"):
        ProjectGenerator().render("blink", context("мій проєкт"))


@pytest.mark.parametrize("name", ["nope", "../templates", ".hidden"])
def test_unknown_template(name: str) -> None:
    with pytest.raises(ProjectGenerationError, match="Невідомий шаблон"):
        ProjectGenerator().template_info(name)


def test_generate_writes_files(tmp_path: Path) -> None:
    destination = tmp_path / "projects" / "blink"
    written = ProjectGenerator().generate("blink", destination, context("blink"))
    assert destination / ".cargo" / "config.toml" in written
    assert 'name = "blink"' in (destination / "Cargo.toml").read_text()
    assert not [p for p in destination.parent.iterdir() if p.name.startswith(".blink.")]


def test_generate_into_existing_empty_directory(tmp_path: Path) -> None:
    ProjectGenerator().generate("blink", tmp_path, context("blink"))
    assert (tmp_path / "src" / "main.rs").is_file()


def test_generate_refuses_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("user data")
    with pytest.raises(ProjectGenerationError, match="не порожня"):
        ProjectGenerator().generate("blink", tmp_path, context("blink"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


def test_failed_render_leaves_nothing_behind(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    (templates / "broken").mkdir(parents=True)
    (templates / "broken" / "template.toml").write_text('title = "Broken"\n')
    (templates / "broken" / "ok.txt.j2").write_text("{{ project_name }}\n")
    (templates / "broken" / "bad.txt.j2").write_text("{{ missing_variable }}\n")
    destination = tmp_path / "out"
    with pytest.raises(ProjectGenerationError, match="missing_variable"):
        ProjectGenerator(templates).generate("broken", destination, {"project_name": "x"})
    assert not destination.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["templates"]


def test_path_traversal_in_template_is_rejected(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    evil = templates / "evil" / "dot_."  # "dot_." would become ".."
    evil.mkdir(parents=True)
    (templates / "evil" / "template.toml").write_text('title = "Evil"\n')
    (evil / "escape.txt").write_text("x")
    with pytest.raises(ProjectGenerationError, match="Небезпечний шлях"):
        ProjectGenerator(templates).render("evil", {"project_name": "x"})


@pytest.mark.parametrize("template", ["blink", "blank"])
def test_generated_project_builds_with_cargo(tmp_path: Path, template: str) -> None:
    """A project made from the template builds with cargo."""
    probe = ToolchainProbe()
    cargo = probe.find("cargo")
    if cargo is None or shutil.which("rustup", path=probe.search_path) is None:
        pytest.skip("Rust toolchain is not installed")
    project = tmp_path / f"my_{template}"
    board = (
        dataclasses.replace(BOARD, led=None, button=None, uart=None)
        if template == "blank"
        else BOARD
    )
    generator = ProjectGenerator()
    generator.check_board(template, board)
    generator.generate(template, project, make_context(project.name, board, CHIP))
    # Share compiled dependencies between test runs.
    target_dir = Path(tempfile.gettempdir()) / "nightexpress-test-target"
    env = {**os.environ, "PATH": probe.search_path, "CARGO_TARGET_DIR": str(target_dir)}
    result = subprocess.run(
        [str(cargo), "build", "--release", "--quiet"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (target_dir / CHIP.target_triple / "release" / project.name).is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_generated_project_folder_is_not_private(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()  # what the user's umask gives an ordinary new folder
    destination = tmp_path / "blink"
    ProjectGenerator().generate("blink", destination, context("blink"))
    assert destination.stat().st_mode & 0o777 == plain.stat().st_mode & 0o777


def test_gitignore_can_be_left_out(tmp_path: Path) -> None:
    generator = ProjectGenerator()
    rendered = generator.render("blank", context(), exclude=(".gitignore",))
    assert ".gitignore" not in {str(f.path) for f in rendered} and len(rendered) >= 4
    created = generator.generate("blank", tmp_path / "app", context(), exclude=(".gitignore",))
    assert not (tmp_path / "app" / ".gitignore").exists()
    assert (tmp_path / "app" / "Cargo.toml") in created


def test_generate_reports_a_parent_that_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("user data")
    with pytest.raises(ProjectGenerationError, match="Не вдалося створити проєкт"):
        ProjectGenerator().generate("blink", tmp_path / "notes.txt" / "blink", context("blink"))
    assert (tmp_path / "notes.txt").read_text() == "user data"
