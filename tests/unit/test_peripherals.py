"""Add peripheral: what adding a module changes in a project made from each template."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nightexpress.models.entities import I2cPinChoice
from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.peripherals import (
    PeripheralError,
    apply,
    default_pins,
    existing_i2c_buses,
    load_peripherals,
    plan_install,
    wiring_for,
)
from nightexpress.services.pin_catalog import load_chip_pins
from nightexpress.services.project_generator import ProjectGenerator, make_context

CATALOG = BoardCatalog.load()
PERIPHERALS = {p.id: p for p in load_peripherals()}
MPU, BMP = PERIPHERALS["mpu6050"], PERIPHERALS["bmp180"]
CHIP_PINS = load_chip_pins()


def project(tmp_path: Path, template: str, board_id: str = "stm32f103c6-minimal") -> Path:
    board = CATALOG.get_board(board_id)
    folder = tmp_path / template
    ProjectGenerator().generate(
        template, folder, make_context(template, board, CATALOG.chip_for(board))
    )
    return folder


def added(folder: Path, board_id: str = "stm32f103c6-minimal", peripheral=MPU, pins=None) -> str:
    plan = plan_install(folder, peripheral, CATALOG.get_board(board_id), pins=pins)
    apply(folder, plan)
    return (folder / "src/main.rs").read_text()


def test_the_mpu6050_joins_a_blink_project(tmp_path: Path) -> None:
    folder = project(tmp_path, "blink")
    plan = plan_install(folder, MPU, CATALOG.get_board("stm32f103c6-minimal"))
    assert plan.wiring == (("VCC", "3.3V"), ("GND", "GND"), ("SCL", "PB6"), ("SDA", "PB7"))
    assert {str(c.path): c.created for c in plan.changes} == {
        "src/mpu6050.rs": True,
        "src/main.rs": False,
        "Cargo.toml": False,
        ".cargo/config.toml": False,
    }
    assert plan.manual == ""
    assert not (folder / "src/mpu6050.rs").exists()  # planning writes nothing

    main = added(folder)
    assert "mod mpu6050;" in main and "use defmt_rtt as _;" in main
    init = main.index("p.I2C1, p.PB6, p.PB7, Default::default(),")
    assert (
        main.index("embassy_stm32::init(") < init < main.index("loop {") < main.index("mpu.read()")
    )
    assert "Mpu6050::new(embedded_hal_bus::i2c::RefCellDevice::new(&i2c1))" in main
    cargo = (folder / "Cargo.toml").read_text()
    assert 'embedded-hal = "1.0"' in cargo and 'defmt-rtt = "1.0"' in cargo
    assert 'embedded-hal-bus = "0.3"' in cargo
    assert 'panic-halt = "1.0.0"' in cargo  # the rest stays as it was
    config = (folder / ".cargo/config.toml").read_text()
    assert "link-arg=-Tdefmt.x" in config and 'DEFMT_LOG = "info"' in config
    assert (folder / "src/mpu6050.rs").read_text() == MPU.driver


def test_a_nucleo_uses_its_arduino_header_pins(tmp_path: Path) -> None:
    main = added(project(tmp_path, "button", "nucleo-f103rb"), "nucleo-f103rb")
    assert "p.I2C1, p.PB8, p.PB9, Default::default()," in main


def test_a_defmt_project_keeps_its_settings(tmp_path: Path) -> None:
    folder = project(tmp_path, "rtt_hello")
    plan = plan_install(folder, MPU, CATALOG.get_board("stm32f103c6-minimal"))
    assert ".cargo/config.toml" not in {str(c.path) for c in plan.changes}
    main = added(folder)
    assert main.count("defmt_rtt") == 1  # the program already sends to RTT


def test_without_a_place_for_it_the_code_is_left_to_paste(tmp_path: Path) -> None:
    folder = project(tmp_path, "blank")
    plan = plan_install(folder, MPU, CATALOG.get_board("stm32f103c6-minimal"))
    assert "Mpu6050::new" in plan.manual and "mpu.read()" in plan.manual
    apply(folder, plan)
    assert "mod mpu6050;" in (folder / "src/main.rs").read_text()


def test_it_is_added_once(tmp_path: Path) -> None:
    folder = project(tmp_path, "blink")
    added(folder)
    with pytest.raises(PeripheralError, match="MPU-6050"):
        plan_install(folder, MPU, CATALOG.get_board("stm32f103c6-minimal"))


def test_pins_already_in_use_are_refused(tmp_path: Path) -> None:
    folder = project(tmp_path, "blink")
    main = folder / "src/main.rs"
    main.write_text(main.read_text().replace("p.PC13", "p.PB6"))
    with pytest.raises(PeripheralError, match="PB6"):
        plan_install(folder, MPU, CATALOG.get_board("stm32f103c6-minimal"))


def test_a_board_without_i2c_is_refused(tmp_path: Path) -> None:
    folder = project(tmp_path, "blink")
    board = dataclasses.replace(CATALOG.get_board("stm32f103c6-minimal"), i2c=None)
    with pytest.raises(PeripheralError, match="I2C"):
        plan_install(folder, MPU, board)


def test_every_board_of_the_catalog_has_an_i2c_bus() -> None:
    assert all(board.i2c is not None for board in CATALOG.boards)


def test_modules_on_one_bus_share_it(tmp_path: Path) -> None:
    folder = project(tmp_path, "blink")
    added(folder)
    plan = plan_install(folder, BMP, CATALOG.get_board("stm32f103c6-minimal"))
    assert plan.shares_bus
    assert {str(c.path) for c in plan.changes} == {"src/bmp180.rs", "src/main.rs"}
    apply(folder, plan)
    main = (folder / "src/main.rs").read_text()
    assert main.count("I2c::new_blocking(") == 1
    assert main.count("RefCellDevice::new(&i2c1)") == 2
    bus = main.index("let i2c1 =")
    assert bus < main.index("Bmp180::new") < main.index("loop {") < main.index("bmp.read()")
    assert main.index("mod mpu6050;") < main.index("mod bmp180;") < main.index("use defmt_rtt")
    assert existing_i2c_buses(main) == [I2cPinChoice("I2C1", "PB6", "PB7")]


def test_the_same_bus_on_other_pins_is_refused(tmp_path: Path) -> None:
    folder = project(tmp_path, "blink")
    added(folder)
    with pytest.raises(PeripheralError, match="PB6/PB7"):
        plan_install(
            folder,
            BMP,
            CATALOG.get_board("stm32f103c6-minimal"),
            pins=I2cPinChoice("I2C1", "PB8", "PB9"),
        )


def test_chosen_pins_and_a_second_bus(tmp_path: Path) -> None:
    folder = project(tmp_path, "blink", "stm32f103c8-bluepill")
    main = added(folder, "stm32f103c8-bluepill", pins=I2cPinChoice("I2C1", "PB8", "PB9"))
    assert "p.I2C1, p.PB8, p.PB9" in main
    main = added(folder, "stm32f103c8-bluepill", BMP, I2cPinChoice("I2C2", "PB10", "PB11"))
    assert "let i2c2 =" in main and "p.I2C2, p.PB10, p.PB11" in main
    assert "Bmp180::new(embedded_hal_bus::i2c::RefCellDevice::new(&i2c2))" in main


def test_wiring_rows_mark_the_pins_to_choose() -> None:
    assert wiring_for(BMP, I2cPinChoice("I2C1", "PB8", "PB9")) == (
        ("VCC", "3.3V", None),
        ("GND", "GND", None),
        ("SCL", "PB8", "scl"),
        ("SDA", "PB9", "sda"),
    )


def test_pin_data_covers_the_catalog() -> None:
    """Every chip has embassy's pin data, and each board's usual bus is one it allows."""
    assert {chip.id for chip in CATALOG.chips} <= set(CHIP_PINS)
    for board in CATALOG.boards:
        pins = CHIP_PINS[CATALOG.chip_for(board).id]
        assert default_pins(board, MPU) in pins.i2c_choices(), board.id
    # STM32F1: SCL and SDA move together, so PB6 with PB9 is not offered.
    f103 = CHIP_PINS["STM32F103C6"].i2c_choices()
    assert I2cPinChoice("I2C1", "PB6", "PB9") not in f103
    assert I2cPinChoice("I2C1", "PB8", "PB9") in f103


def test_every_driver_is_tested_on_the_computer() -> None:
    """scripts/check.sh runs `cargo test` in tests/drivers: each driver must be listed there."""
    lib = (Path(__file__).resolve().parents[1] / "drivers" / "src" / "lib.rs").read_text()
    for peripheral in PERIPHERALS.values():
        assert f"peripherals/{peripheral.id}/{peripheral.module}.rs" in lib, peripheral.id
