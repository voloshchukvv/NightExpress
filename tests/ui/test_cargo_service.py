from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from nightexpress.models.entities import BuildProfile, DiagnosticLevel
from nightexpress.services.cargo_message_parser import strip_ansi
from nightexpress.services.cargo_service import MESSAGE_FORMAT, BuildResult, CargoService
from nightexpress.services.process_runner import ProcessOutcome
from nightexpress.services.toolchain_probe import ToolchainProbe

pytestmark = pytest.mark.ui

ROOT = Path(__file__).resolve().parents[2]
FAKE_CARGO = ROOT / "tests" / "fixtures" / "fake_cargo.py"
REFERENCE = ROOT / "reference" / "blinky_f103c6"
PROJECT = Path("/work/blinky_f103c6")


def run_build(
    qtbot, service: CargoService, project: Path, profile: BuildProfile = BuildProfile.RELEASE
) -> tuple[BuildResult, list[str], list]:
    logs: list[str] = []
    diags: list = []
    service.log_line.connect(logs.append)
    service.diagnostic.connect(diags.append)
    with qtbot.waitSignal(service.finished, timeout=120_000) as blocker:
        service.build(project, profile)
    return blocker.args[0], logs, diags


def fake(scenario: str) -> CargoService:
    return CargoService((sys.executable, str(FAKE_CARGO), scenario))


def test_build_spec() -> None:
    service = CargoService(("cargo",), search_path="/usr/bin:/Users/dev/.cargo/bin")
    spec = service.build_spec(PROJECT, BuildProfile.RELEASE)
    assert spec.program == "cargo"
    assert spec.args == ("build", MESSAGE_FORMAT, "--color=always", "--release")
    assert spec.cwd == PROJECT
    assert spec.env == {"PATH": "/usr/bin:/Users/dev/.cargo/bin"}
    dev = service.build_spec(PROJECT, BuildProfile.DEV)
    assert "--release" not in dev.args


def test_success(qtbot, tmp_path: Path) -> None:
    result, logs, diags = run_build(qtbot, fake("success"), tmp_path)
    assert result.success and result.outcome is ProcessOutcome.FINISHED
    assert result.elf_path == PROJECT / "target/thumbv7m-none-eabi/release/blinky_f103c6"
    assert diags == [] and result.diagnostics == ()
    assert logs[0].startswith("ARGS build --message-format=")
    assert any("Compiling blinky_f103c6" in line for line in logs)


def test_warning_is_logged_and_reported(qtbot, tmp_path: Path) -> None:
    result, logs, diags = run_build(qtbot, fake("warning"), tmp_path)
    assert result.success
    assert [d.level for d in diags] == [DiagnosticLevel.WARNING]
    assert result.diagnostics == tuple(diags)
    assert any("unused variable" in strip_ansi(line) for line in logs)


@pytest.mark.parametrize("scenario", ["compile_error", "link_error"])
def test_failures(qtbot, tmp_path: Path, scenario: str) -> None:
    result, _, diags = run_build(qtbot, fake(scenario), tmp_path)
    assert not result.success
    assert result.exit_code == 101
    assert result.elf_path is None
    assert [d.level for d in diags] == [DiagnosticLevel.ERROR]


def test_cargo_not_found(qtbot, tmp_path: Path) -> None:
    result, _, _ = run_build(qtbot, CargoService(("/nonexistent/cargo",)), tmp_path)
    assert result.outcome is ProcessOutcome.FAILED_TO_START
    assert not result.success and result.error


def test_only_one_build_at_a_time(qtbot, tmp_path: Path) -> None:
    service = fake("success")
    with qtbot.waitSignal(service.finished, timeout=30_000):
        service.build(tmp_path)
        with pytest.raises(RuntimeError, match="already running"):
            service.build(tmp_path)


def test_real_build_of_reference_firmware(qtbot) -> None:
    """End to end with the real toolchain (incremental, so usually ~1 s)."""
    probe = ToolchainProbe()
    cargo = probe.find("cargo")
    if cargo is None or shutil.which("rustup", path=probe.search_path) is None:
        pytest.skip("Rust toolchain is not installed")
    service = CargoService((str(cargo),), search_path=probe.search_path)
    result, _, _ = run_build(qtbot, service, REFERENCE)
    assert result.success, result
    assert result.elf_path == REFERENCE / "target/thumbv7m-none-eabi/release/blinky_f103c6"
    assert result.elf_path.is_file()
