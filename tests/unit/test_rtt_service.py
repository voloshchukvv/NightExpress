"""What the RTT reader asks probe-rs for, and how it tells a defmt program from another."""

from __future__ import annotations

from pathlib import Path

import pytest

from nightexpress.services.rtt_service import attach_args, has_rtt

ROOT = Path(__file__).resolve().parents[2]
RELEASE = "target/thumbv7m-none-eabi/release"
RTT_ELF = ROOT / "reference/rtt_hello_f103c6" / RELEASE / "rtt_hello_f103c6"
PLAIN_ELF = ROOT / "reference/blinky_f103c6" / RELEASE / "blinky_f103c6"


def test_attach_keeps_the_program_running_after_we_detach() -> None:
    args = attach_args(Path("/p/app.elf"), "STM32F103C6", "0483:3748:ABC")
    assert args[:2] == ["attach", "--chip"] and "--non-interactive" in args
    assert args[args.index("--probe") + 1] == "0483:3748:ABC"
    # probe-rs would make the channels block when full: the board would hang without us.
    assert args[args.index("--rtt-channel-mode") + 1] == "no-block-skip"
    assert args[-1] == "/p/app.elf"
    assert "--probe" not in attach_args(Path("/p/app.elf"), "STM32F103C6", None)


def test_something_that_is_not_an_elf_has_no_rtt(tmp_path: Path) -> None:
    (tmp_path / "x.elf").write_bytes(b"not an elf")
    assert not has_rtt(tmp_path / "x.elf")
    assert not has_rtt(tmp_path / "missing.elf")


@pytest.mark.skipif(
    not (RTT_ELF.is_file() and PLAIN_ELF.is_file()),
    reason="build the reference firmware first (scripts/check.sh)",
)
def test_rtt_is_found_in_a_defmt_program_only() -> None:
    assert has_rtt(RTT_ELF)
    assert not has_rtt(PLAIN_ELF)
