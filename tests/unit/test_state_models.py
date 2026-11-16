"""State models must emit a change signal exactly when the value really changes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import SignalInstance

from nightexpress.models.entities import (
    Diagnostic,
    DiagnosticLevel,
    MemoryUsage,
    OperationState,
    ProbeInfo,
    ProjectConfig,
    SerialPortInfo,
    ToolStatus,
    UploadMethod,
    UploadProgress,
    UploadStage,
)
from nightexpress.models.state import (
    AppModel,
    BuildModel,
    DisplayMode,
    LineEnding,
    PortsModel,
    ProjectModel,
    Screen,
    SerialMonitorModel,
    ToolchainModel,
    UploadModel,
)
from nightexpress.models.state.app_model import MAX_RECENT_PROJECTS
from nightexpress.models.state.build_model import MAX_LOG_LINES
from nightexpress.models.state.serial_monitor_model import MAX_BUFFER_BYTES


class Spy:
    def __init__(self, signal: SignalInstance) -> None:
        self.calls: list[tuple[Any, ...]] = []
        signal.connect(lambda *args: self.calls.append(args))

    @property
    def count(self) -> int:
        return len(self.calls)

    @property
    def last(self) -> Any:
        args = self.calls[-1]
        return args[0] if len(args) == 1 else args


# --- AppModel -------------------------------------------------------------------------------


def test_app_screen_emits_only_on_change() -> None:
    model = AppModel()
    spy = Spy(model.screen_changed)
    model.set_screen(Screen.WELCOME)
    assert spy.count == 0
    model.set_screen(Screen.EDITOR)
    model.set_screen(Screen.EDITOR)
    assert spy.count == 1 and spy.last is Screen.EDITOR


def test_recent_projects_move_to_top_dedupe_and_limit() -> None:
    model = AppModel()
    spy = Spy(model.recent_projects_changed)
    a, b = Path("/a"), Path("/b")
    model.add_recent_project(a)
    model.add_recent_project(b)
    model.add_recent_project(a)
    assert model.recent_projects == (a, b)
    model.add_recent_project(a)  # already on top -> no change
    assert spy.count == 3

    model.set_recent_projects(Path(f"/p{i}") for i in range(MAX_RECENT_PROJECTS + 5))
    assert len(model.recent_projects) == MAX_RECENT_PROJECTS
    model.remove_recent_project(Path("/p0"))
    assert Path("/p0") not in model.recent_projects


# --- ProjectModel ---------------------------------------------------------------------------


def make_config(name: str = "blink") -> ProjectConfig:
    return ProjectConfig(Path(f"/p/{name}"), name, "stm32f103c6-minimal", UploadMethod.PROBE_RS)


def test_project_open_and_close() -> None:
    model = ProjectModel()
    config_spy = Spy(model.config_changed)
    model.open_project(make_config())
    model.set_config(make_config())  # equal value -> no signal
    assert model.is_open and config_spy.count == 1

    model.open_file(Path("src/main.rs"))
    model.set_dirty(Path("src/main.rs"), True)
    model.close_project()
    assert not model.is_open
    assert model.open_files == () and model.active_file is None
    assert not model.has_unsaved_changes
    assert config_spy.last is None


def test_open_file_activates_and_does_not_duplicate() -> None:
    model = ProjectModel()
    files_spy, active_spy = Spy(model.open_files_changed), Spy(model.active_file_changed)
    main, cargo = Path("main.rs"), Path("Cargo.toml")
    model.open_file(main)
    model.open_file(cargo)
    model.open_file(main)
    assert model.open_files == (main, cargo)
    assert files_spy.count == 2
    assert active_spy.count == 3 and model.active_file == main


def test_close_active_file_selects_neighbour() -> None:
    model = ProjectModel()
    a, b, c = Path("a"), Path("b"), Path("c")
    for p in (a, b, c):
        model.open_file(p)
    model.set_active_file(b)
    model.close_file(b)
    assert model.active_file == c
    model.close_file(c)
    assert model.active_file == a
    model.close_file(a)
    assert model.active_file is None
    model.close_file(Path("never-opened"))  # no-op


def test_set_active_file_requires_open_file() -> None:
    with pytest.raises(ValueError, match="not open"):
        ProjectModel().set_active_file(Path("x"))


def test_dirty_flag_emits_only_on_change() -> None:
    model = ProjectModel()
    spy = Spy(model.dirty_changed)
    path = Path("main.rs")
    model.open_file(path)
    model.set_dirty(path, False)
    model.set_dirty(path, True)
    model.set_dirty(path, True)
    assert spy.calls == [(path, True)]
    model.close_file(path)  # closing clears the flag
    assert spy.calls[-1] == (path, False)
    assert not model.is_dirty(path)


# --- BuildModel -----------------------------------------------------------------------------


def test_build_state_transitions() -> None:
    model = BuildModel()
    spy = Spy(model.state_changed)
    model.set_state(OperationState.RUNNING)
    model.set_state(OperationState.RUNNING)
    model.set_state(OperationState.SUCCEEDED)
    assert [c[0] for c in spy.calls] == [OperationState.RUNNING, OperationState.SUCCEEDED]
    with pytest.raises(ValueError, match="succeeded -> cancelled"):
        model.set_state(OperationState.CANCELLED)


def test_build_log_is_bounded() -> None:
    model = BuildModel()
    appended, cleared = Spy(model.log_appended), Spy(model.log_cleared)
    for i in range(MAX_LOG_LINES + 10):
        model.append_log(f"line {i}")
    assert len(model.log) == MAX_LOG_LINES
    assert model.log[0] == "line 10"
    assert appended.count == MAX_LOG_LINES + 10
    model.clear_log()
    model.clear_log()
    assert cleared.count == 1


def test_build_diagnostics_and_memory() -> None:
    model = BuildModel()
    diag_spy, mem_spy = Spy(model.diagnostics_changed), Spy(model.memory_usage_changed)
    err = Diagnostic(DiagnosticLevel.ERROR, "e", Path("src/main.rs"), 1, 1)
    warn = Diagnostic(DiagnosticLevel.WARNING, "w")
    model.add_diagnostic(err)
    model.add_diagnostic(warn)
    model.set_diagnostics([err, warn])  # same content -> no signal
    assert diag_spy.count == 2
    assert model.count(DiagnosticLevel.ERROR) == 1

    usage = MemoryUsage(1064, 32768, 4, 10240)
    model.set_memory_usage(usage)
    model.set_memory_usage(MemoryUsage(1064, 32768, 4, 10240))
    assert mem_spy.count == 1

    model.append_log("x")
    model.reset_results()
    assert model.log == () and model.diagnostics == () and model.memory_usage is None


# --- UploadModel ----------------------------------------------------------------------------


def test_upload_progress_and_message() -> None:
    model = UploadModel()
    progress_spy, msg_spy = Spy(model.progress_changed), Spy(model.message_changed)
    model.set_state(OperationState.PREPARING)
    model.set_progress(UploadProgress(UploadStage.PROGRAMMING, 40))
    model.set_progress(UploadProgress(UploadStage.PROGRAMMING, 40))
    model.set_progress(UploadProgress(UploadStage.PROGRAMMING, 80))
    assert progress_spy.count == 2
    model.set_message("done")
    model.set_message("done")
    assert msg_spy.count == 1
    with pytest.raises(ValueError, match="preparing -> succeeded"):
        model.set_state(OperationState.SUCCEEDED)


def test_upload_progress_percent_validated() -> None:
    with pytest.raises(ValueError):
        UploadProgress(UploadStage.ERASING, 101)


# --- SerialMonitorModel ---------------------------------------------------------------------


def test_serial_settings_emit_only_on_change() -> None:
    model = SerialMonitorModel()
    baud, mode, ending = (
        Spy(model.baud_rate_changed),
        Spy(model.display_mode_changed),
        Spy(model.line_ending_changed),
    )
    model.set_baud_rate(115200)
    model.set_baud_rate(9600)
    model.set_display_mode(DisplayMode.TEXT)
    model.set_display_mode(DisplayMode.HEX)
    model.set_line_ending(LineEnding.LF)
    model.set_line_ending(LineEnding.CRLF)
    assert (baud.count, mode.count, ending.count) == (1, 1, 1)
    assert LineEnding.CRLF.bytes == b"\r\n"
    with pytest.raises(ValueError):
        model.set_baud_rate(0)


def test_serial_port_and_open_state() -> None:
    model = SerialMonitorModel()
    port_spy, open_spy = Spy(model.port_changed), Spy(model.open_changed)
    port = SerialPortInfo("/dev/cu.usbserial-120", vid=0x067B, pid=0x2303)
    model.set_port(port)
    model.set_port(SerialPortInfo("/dev/cu.usbserial-120", vid=0x067B, pid=0x2303))
    model.set_open(True)
    model.set_open(True)
    assert (port_spy.count, open_spy.count) == (1, 1)


def test_serial_buffer_is_bounded() -> None:
    model = SerialMonitorModel()
    data_spy, cleared_spy = Spy(model.data_received), Spy(model.cleared)
    model.append_data(b"")
    model.append_data(b"a" * MAX_BUFFER_BYTES)
    model.append_data(b"tail")
    assert len(model.buffer) == MAX_BUFFER_BYTES
    assert model.buffer.endswith(b"tail")
    assert data_spy.count == 2 and data_spy.last == b"tail"
    model.clear()
    model.clear()
    assert cleared_spy.count == 1


# --- ToolchainModel -------------------------------------------------------------------------


def test_toolchain_status_and_readiness() -> None:
    model = ToolchainModel()
    spy = Spy(model.status_changed)
    assert not model.is_ready  # nothing checked yet

    cargo = ToolStatus("cargo", found=True, version="1.95.0")
    probe = ToolStatus("probe-rs", found=False, required=False)
    model.set_statuses([cargo, probe])
    model.set_status(ToolStatus("cargo", found=True, version="1.95.0"))
    assert spy.count == 2
    assert model.is_ready

    model.set_status(ToolStatus("rustup", found=False))
    assert not model.is_ready
    assert model.status("rustup") is not None


# --- PortsModel -----------------------------------------------------------------------------

STLINK = ProbeInfo("STLink V2", 0x0483, 0x3748, "2C3B15157416303030303032")
PL2303 = SerialPortInfo("/dev/cu.usbserial-120", vid=0x067B, pid=0x2303, location="1-1")
FT232 = SerialPortInfo(
    "/dev/cu.usbserial-A50285BI", vid=0x0403, pid=0x6001, serial_number="A50285BI"
)


def test_ports_never_auto_select() -> None:
    model = PortsModel()
    model.set_targets([STLINK, PL2303, FT232])
    assert model.selected is None
    assert model.probes == (STLINK,)
    assert model.serial_ports == (PL2303, FT232)


def test_ports_targets_emit_only_on_change() -> None:
    model = PortsModel()
    spy = Spy(model.targets_changed)
    model.set_targets([STLINK])
    model.set_targets([STLINK])
    assert spy.count == 1


def test_ports_selection() -> None:
    model = PortsModel()
    spy = Spy(model.selection_changed)
    model.set_targets([STLINK, PL2303])
    model.select(PL2303.stable_id)
    model.select(PL2303.stable_id)
    assert spy.count == 1 and model.selected == PL2303
    with pytest.raises(ValueError, match="unknown upload target"):
        model.select("serial:dead:beef")


def test_ports_selection_cleared_when_target_disappears() -> None:
    model = PortsModel()
    spy = Spy(model.selection_changed)
    model.set_targets([STLINK, PL2303])
    model.select(STLINK.stable_id)
    model.set_targets([PL2303, FT232])  # ST-Link unplugged
    assert model.selected is None
    assert spy.last is None
    model.set_targets([STLINK, PL2303])  # plugged back: no silent re-selection
    assert model.selected is None


def test_ports_selection_kept_when_other_targets_change() -> None:
    model = PortsModel()
    model.set_targets([STLINK])
    model.select(STLINK.stable_id)
    spy = Spy(model.selection_changed)
    model.set_targets([STLINK, PL2303])
    assert model.selected == STLINK and spy.count == 0


def test_ports_reject_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        PortsModel().set_targets([PL2303, PL2303])
