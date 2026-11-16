"""ELF helpers: flat binary image for the serial bootloader and memory usage after a build.

Both follow the behaviour of the LLVM tools so results can be checked against them:

- :func:`elf_to_bin` == ``llvm-objcopy -O binary`` (allocated sections with contents, placed at
  their load addresses, gaps filled with zeros);
- :func:`memory_usage` uses the Berkeley rules of ``llvm-size``
  (text = read-only allocated, data = writable with contents, bss = writable without contents).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import Section
from elftools.elf.segments import Segment

from nightexpress.models.entities import MemoryUsage

MAX_IMAGE_BYTES = 16 * 1024 * 1024  # sanity limit: no STM32 has more flash than this


class ElfToolsError(Exception):
    """The file is not a usable firmware ELF."""


@dataclass(frozen=True)
class BinaryImage:
    address: int  # load address of the first byte, e.g. 0x08000000
    data: bytes

    @property
    def end_address(self) -> int:
        return self.address + len(self.data)


@dataclass(frozen=True)
class SectionSizes:
    text: int
    data: int
    bss: int


def _open(path: Path) -> tuple[ELFFile, object]:
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise ElfToolsError(f"cannot open {path}: {exc}") from exc
    try:
        return ELFFile(stream), stream
    except ELFError as exc:
        stream.close()
        raise ElfToolsError(f"{path} is not an ELF file: {exc}") from exc


def _is_alloc(section: Section) -> bool:
    return bool(section["sh_flags"] & SH_FLAGS.SHF_ALLOC) and section["sh_size"] > 0


def _load_address(section: Section, segments: list[Segment]) -> int:
    """Physical (load) address: e.g. .data lives in RAM but is stored in flash."""
    for segment in segments:
        if segment["p_type"] != "PT_LOAD":
            continue
        start = segment["p_offset"]
        if start <= section["sh_offset"] < start + segment["p_filesz"]:
            return segment["p_paddr"] + (section["sh_offset"] - start)
    return section["sh_addr"]


def elf_to_bin(path: Path) -> BinaryImage:
    elf, stream = _open(path)
    try:
        segments = list(elf.iter_segments())
        chunks: list[tuple[int, bytes]] = []
        for section in elf.iter_sections():
            if _is_alloc(section) and section["sh_type"] != "SHT_NOBITS":
                chunks.append((_load_address(section, segments), section.data()))
    finally:
        stream.close()  # type: ignore[attr-defined]

    if not chunks:
        raise ElfToolsError(f"{path} has no loadable sections")
    start = min(address for address, _ in chunks)
    end = max(address + len(data) for address, data in chunks)
    if end - start > MAX_IMAGE_BYTES:
        raise ElfToolsError(
            f"{path}: image spans {end - start} bytes (0x{start:08x}..0x{end:08x}); "
            "sections are probably linked to different memories"
        )
    image = bytearray(end - start)
    for address, data in chunks:
        offset = address - start
        image[offset : offset + len(data)] = data
    return BinaryImage(start, bytes(image))


def section_sizes(path: Path) -> SectionSizes:
    elf, stream = _open(path)
    text = data = bss = 0
    try:
        for section in elf.iter_sections():
            if not _is_alloc(section):
                continue
            size = section["sh_size"]
            if section["sh_type"] == "SHT_NOBITS":
                bss += size
            elif section["sh_flags"] & SH_FLAGS.SHF_WRITE:
                data += size
            else:
                text += size
    finally:
        stream.close()  # type: ignore[attr-defined]
    return SectionSizes(text, data, bss)


def memory_usage(path: Path, flash_bytes: int, ram_bytes: int) -> MemoryUsage:
    """Flash holds code, constants and the initial values of .data; RAM holds .data and .bss."""
    sizes = section_sizes(path)
    return MemoryUsage(
        flash_used=sizes.text + sizes.data,
        flash_total=flash_bytes,
        ram_used=sizes.data + sizes.bss,
        ram_total=ram_bytes,
    )
