"""ElfTools must match llvm-objcopy / llvm-size byte for byte."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from nightexpress.services.elf_tools import (
    ElfToolsError,
    SectionSizes,
    elf_to_bin,
    memory_usage,
    section_sizes,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "elf"
REFERENCE_ELF = (
    Path(__file__).resolve().parents[2]
    / "reference/blinky_f103c6/target/thumbv7m-none-eabi/release/blinky_f103c6"
)

# Recorded with `llvm-size` (Berkeley format) when the fixtures were created.
EXPECTED_SIZES = {
    "blinky": SectionSizes(text=1064, data=0, bss=4),
    "blinky_data": SectionSizes(text=1080, data=4, bss=4),
}


@pytest.mark.parametrize("name", ["blinky", "blinky_data"])
def test_elf_to_bin_matches_llvm_objcopy(name: str) -> None:
    image = elf_to_bin(FIXTURES / f"{name}.elf")
    assert image.address == 0x08000000
    assert image.data == (FIXTURES / f"{name}.bin").read_bytes()


def test_data_section_is_placed_at_its_flash_load_address() -> None:
    # .data runs at 0x20000000 (RAM) but its initial value 0x12345678 is stored in flash.
    image = elf_to_bin(FIXTURES / "blinky_data.elf")
    assert image.end_address == 0x08000000 + 1088
    assert image.data[-4:] == (0x1234_5678).to_bytes(4, "little")


def test_vector_table_starts_with_stack_pointer() -> None:
    data = elf_to_bin(FIXTURES / "blinky.elf").data
    assert int.from_bytes(data[:4], "little") == 0x2000_2800  # end of 10 KB RAM


@pytest.mark.parametrize("name", ["blinky", "blinky_data"])
def test_section_sizes_match_llvm_size(name: str) -> None:
    assert section_sizes(FIXTURES / f"{name}.elf") == EXPECTED_SIZES[name]


def test_memory_usage() -> None:
    usage = memory_usage(FIXTURES / "blinky_data.elf", 32 * 1024, 10 * 1024)
    assert (usage.flash_used, usage.ram_used) == (1084, 8)
    assert usage.fits


def test_not_an_elf(tmp_path: Path) -> None:
    bogus = tmp_path / "firmware.elf"
    bogus.write_text("hello")
    with pytest.raises(ElfToolsError, match="not an ELF"):
        elf_to_bin(bogus)
    with pytest.raises(ElfToolsError, match="cannot open"):
        section_sizes(tmp_path / "missing.elf")


def _llvm_tool(name: str) -> Path | None:
    rustc = shutil.which("rustc")
    if rustc is None:
        return None
    sysroot = subprocess.run(
        [rustc, "+stable", "--print", "sysroot"], capture_output=True, text=True, check=False
    ).stdout.strip()
    found = sorted(Path(sysroot).glob(f"lib/rustlib/*/bin/{name}")) if sysroot else []
    return found[0] if found else None


def test_live_reference_build_matches_llvm_objcopy(tmp_path: Path) -> None:
    objcopy = _llvm_tool("llvm-objcopy")
    if objcopy is None or not REFERENCE_ELF.is_file():
        pytest.skip("needs llvm-tools and a built reference/blinky_f103c6 (scripts/check.sh)")
    expected = tmp_path / "expected.bin"
    subprocess.run([objcopy, "-O", "binary", REFERENCE_ELF, expected], check=True)
    assert elf_to_bin(REFERENCE_ELF).data == expected.read_bytes()
