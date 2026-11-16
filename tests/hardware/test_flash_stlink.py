"""Real hardware: flash reference/blinky_f103c6 through an ST-Link and read it back.

Run explicitly with the board connected (it overwrites the board's firmware with blinky):

    .venv/bin/python -m pytest -m hardware tests/hardware
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nightexpress.services.board_catalog import BoardCatalog
from nightexpress.services.elf_tools import elf_to_bin
from nightexpress.services.probe_rs import ProbeRsService, parse_probe_list
from nightexpress.services.toolchain_probe import ToolchainProbe
from nightexpress.views.main_window import MainWindow

pytestmark = [pytest.mark.hardware, pytest.mark.ui]

REFERENCE = Path(__file__).resolve().parents[2] / "reference" / "blinky_f103c6"
ELF = REFERENCE / "target" / "thumbv7m-none-eabi" / "release" / "blinky_f103c6"


@pytest.fixture(scope="module")
def probe_rs() -> Path:
    probe = ToolchainProbe()
    tool = probe.find("probe-rs")
    if tool is None:
        pytest.skip("probe-rs is not installed")
    listing = subprocess.run([tool, "list"], capture_output=True, text=True, check=False).stdout
    if not parse_probe_list(listing):
        pytest.skip("no ST-Link connected")
    if not ELF.is_file():
        pytest.skip("build reference/blinky_f103c6 first (scripts/check.sh)")
    return tool


def test_check_chip_and_flash_blinky(qtbot, dispose, probe_rs: Path) -> None:
    chip = BoardCatalog.load().get_chip("STM32F103C6")
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    service = ProbeRsService((str(probe_rs),), parent=window)

    with qtbot.waitSignal(service.chip_checked, timeout=30_000) as blocker:
        service.check_chip(chip.probe_rs_name, None, chip.idcode_address, chip.dev_id)
    check = blocker.args[0]
    assert check.ok, check

    with qtbot.waitSignal(service.download_finished, timeout=120_000) as blocker:
        service.download(ELF, chip.probe_rs_name, None)
    assert blocker.args[0].ok, blocker.args[0]

    expected = elf_to_bin(ELF).data[:16]
    read = [probe_rs, "read", "--chip", chip.probe_rs_name, "--non-interactive"]
    output = subprocess.run(
        [*read, "b32", "0x08000000", "4"], capture_output=True, text=True, check=True
    ).stdout
    words = output.split(":", 1)[1].split()
    assert b"".join(int(w, 16).to_bytes(4, "little") for w in words) == expected
    subprocess.run(
        [probe_rs, "reset", "--chip", chip.probe_rs_name, "--non-interactive"], check=True
    )
