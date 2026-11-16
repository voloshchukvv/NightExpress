"""CargoMessageParser against output recorded from real builds of reference/blinky_f103c6."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nightexpress.models.entities import DiagnosticLevel
from nightexpress.services.cargo_message_parser import (
    ArtifactEvent,
    CargoEvent,
    CargoMessageParser,
    DiagnosticEvent,
    FinishedEvent,
    strip_ansi,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cargo"
PROJECT = Path("/work/blinky_f103c6")
ELF = PROJECT / "target/thumbv7m-none-eabi/release/blinky_f103c6"


def parse_fixture(name: str) -> list[CargoEvent]:
    parser = CargoMessageParser(PROJECT)
    lines = (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return [e for e in map(parser.parse_line, lines) if e is not None]


def diagnostics(events: list[CargoEvent]) -> list:
    return [e.diagnostic for e in events if isinstance(e, DiagnosticEvent)]


def test_success() -> None:
    events = parse_fixture("success")
    assert events == [ArtifactEvent(ELF, "blinky_f103c6"), FinishedEvent(success=True)]


def test_warning() -> None:
    events = parse_fixture("warning")
    [warning] = diagnostics(events)
    assert warning.level is DiagnosticLevel.WARNING
    assert warning.message == "unused variable: `unused_value`"
    assert (warning.file, warning.line, warning.column) == (PROJECT / "src/main.rs", 20, 9)
    assert warning.code == "unused_variables"
    assert "unused_value" in strip_ansi(warning.rendered)
    assert events[-2:] == [ArtifactEvent(ELF, "blinky_f103c6"), FinishedEvent(success=True)]


def test_compile_error_skips_failure_note() -> None:
    events = parse_fixture("compile_error")
    [error] = diagnostics(events)  # the "rustc --explain" failure-note is dropped
    assert error.level is DiagnosticLevel.ERROR
    assert error.code == "E0425"
    assert error.message == "cannot find value `undefined_value` in this scope"
    assert (error.file, error.line, error.column) == (PROJECT / "src/main.rs", 24, 9)
    assert not any(isinstance(e, ArtifactEvent) for e in events)
    assert events[-1] == FinishedEvent(success=False)


def test_link_error_has_no_location_but_keeps_details() -> None:
    events = parse_fixture("link_error")
    [error] = diagnostics(events)
    assert error.level is DiagnosticLevel.ERROR
    assert error.message.startswith("linking with `rust-lld` failed")
    assert not error.has_location
    assert "stmr_missing_symbol" in strip_ansi(error.rendered)
    assert events[-1] == FinishedEvent(success=False)


def message_line(**message: object) -> str:
    base = {"level": "error", "message": "m", "spans": [], "code": None, "rendered": "r"}
    base.update(message)
    return json.dumps({"reason": "compiler-message", "message": base})


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   Compiling foo v0.1.0",
        "{not json",
        "[1, 2]",
        '{"reason": "build-script-executed"}',
        message_line(level="failure-note", message="For more information about this error"),
        message_line(message="aborting due to 2 previous errors"),
        message_line(level="warning", message="3 warnings emitted"),
        message_line(level="mystery-level"),
    ],
)
def test_ignored_lines(line: str) -> None:
    assert CargoMessageParser(PROJECT).parse_line(line) is None


def test_absolute_span_and_macro_expansion() -> None:
    span_in_macro = {
        "file_name": "/home/dev/.cargo/registry/src/x/defmt/src/lib.rs",
        "line_start": 10,
        "column_start": 1,
        "is_primary": True,
        "expansion": {
            "span": {"file_name": "src/main.rs", "line_start": 7, "column_start": 5},
        },
    }
    event = CargoMessageParser(PROJECT).parse_line(message_line(spans=[span_in_macro]))
    assert isinstance(event, DiagnosticEvent)
    assert (event.diagnostic.file, event.diagnostic.line) == (PROJECT / "src/main.rs", 7)


def test_pseudo_file_names_have_no_path() -> None:
    span = {"file_name": "<::core::macros::panic>", "line_start": 1, "column_start": 1}
    event = CargoMessageParser(PROJECT).parse_line(
        message_line(spans=[{**span, "is_primary": True}])
    )
    assert isinstance(event, DiagnosticEvent)
    assert event.diagnostic.file is None


def test_strip_ansi() -> None:
    assert strip_ansi("\x1b[1m\x1b[91merror\x1b[0m: x") == "error: x"


def test_nested_dependency_macros_lead_back_to_the_project() -> None:
    registry = "/home/dev/.cargo/registry/src/x"
    span = {
        "file_name": f"{registry}/inner/src/lib.rs",
        "line_start": 10,
        "column_start": 1,
        "is_primary": True,
        "expansion": {
            "span": {
                "file_name": f"{registry}/outer/src/lib.rs",
                "line_start": 20,
                "column_start": 1,
                "expansion": {
                    "span": {"file_name": "src/main.rs", "line_start": 7, "column_start": 5},
                },
            },
        },
    }
    event = CargoMessageParser(PROJECT).parse_line(message_line(spans=[span]))
    assert isinstance(event, DiagnosticEvent)
    assert (event.diagnostic.file, event.diagnostic.line) == (PROJECT / "src/main.rs", 7)

    # No call site in the project at all: the location of the dependency is kept.
    del span["expansion"]["span"]["expansion"]
    event = CargoMessageParser(PROJECT).parse_line(message_line(spans=[span]))
    assert isinstance(event, DiagnosticEvent)
    assert event.diagnostic.file == Path(f"{registry}/inner/src/lib.rs")
