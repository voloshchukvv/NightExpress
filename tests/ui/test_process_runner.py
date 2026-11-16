"""ProcessRunner runs real child processes (the current Python interpreter)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nightexpress.services.process_runner import (
    MAX_LINE_BYTES,
    ProcessOutcome,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
    run_sync,
)

pytestmark = pytest.mark.ui

PY = sys.executable


def py(code: str, **kwargs: object) -> ProcessSpec:
    return ProcessSpec(PY, ("-c", code), **kwargs)  # type: ignore[arg-type]


def run(
    qtbot, spec: ProcessSpec, timeout: int = 10_000
) -> tuple[ProcessResult, list[str], list[str]]:
    runner = ProcessRunner()
    out: list[str] = []
    err: list[str] = []
    runner.stdout_line.connect(out.append)
    runner.stderr_line.connect(err.append)
    with qtbot.waitSignal(runner.finished, timeout=timeout) as blocker:
        runner.start(spec)
    assert not runner.is_running
    return blocker.args[0], out, err


def test_success_streams_lines(qtbot) -> None:
    result, out, err = run(qtbot, py("print('a'); print('b')"))
    assert result.ok and result.exit_code == 0
    assert out == ["a", "b"] and err == []
    assert result.stdout_tail == ("a", "b")


def test_nonzero_exit_and_stderr(qtbot) -> None:
    result, _, err = run(qtbot, py("import sys; sys.stderr.write('oops\\n'); sys.exit(3)"))
    assert result.outcome is ProcessOutcome.FINISHED
    assert result.exit_code == 3 and not result.ok
    assert err == ["oops"]


def test_partial_last_line_crlf_and_utf8(qtbot) -> None:
    code = "import sys; sys.stdout.buffer.write('привіт\\r\\nend'.encode())"
    _, out, _ = run(qtbot, py(code))
    assert out == ["привіт", "end"]


def test_cwd_and_env(qtbot, tmp_path: Path) -> None:
    code = "import os; print(os.getcwd()); print(os.environ['STMR_TEST'])"
    _, out, _ = run(qtbot, py(code, cwd=tmp_path, env={"STMR_TEST": "42"}))
    assert Path(out[0]).resolve() == tmp_path.resolve()
    assert out[1] == "42"


def test_long_line_is_chunked(qtbot) -> None:
    _, out, _ = run(qtbot, py(f"print('x' * {MAX_LINE_BYTES + 10})"))
    assert [len(line) for line in out] == [MAX_LINE_BYTES, 10]


def test_timeout_kills_process(qtbot) -> None:
    result, _, _ = run(qtbot, py("import time; time.sleep(30)", timeout_ms=300))
    assert result.outcome is ProcessOutcome.TIMED_OUT
    assert result.exit_code is None


def test_cancel(qtbot) -> None:
    runner = ProcessRunner()
    with qtbot.waitSignal(runner.started, timeout=5000):
        runner.start(py("import time; print('ready', flush=True); time.sleep(30)"))
    with qtbot.waitSignal(runner.finished, timeout=5000) as blocker:
        runner.cancel()
    assert blocker.args[0].outcome is ProcessOutcome.CANCELLED


def test_failed_to_start(qtbot) -> None:
    result, _, _ = run(qtbot, ProcessSpec("/nonexistent/definitely-not-a-program"))
    assert result.outcome is ProcessOutcome.FAILED_TO_START
    assert result.error


def test_single_process_at_a_time_and_reusable(qtbot) -> None:
    runner = ProcessRunner()
    with qtbot.waitSignal(runner.finished, timeout=10_000):
        runner.start(py("import time; time.sleep(0.2)"))
        with pytest.raises(RuntimeError, match="already running"):
            runner.start(py("pass"))
    with qtbot.waitSignal(runner.finished, timeout=10_000) as blocker:
        runner.start(py("print('again')"))
    assert blocker.args[0].stdout_tail == ("again",)


def test_spec_rejects_string_args() -> None:
    with pytest.raises(TypeError):
        ProcessSpec(PY, "-c print(1)")  # type: ignore[arg-type]


def test_command_line_is_quoted() -> None:
    assert ProcessSpec("cargo", ("build", "a b")).command_line == 'cargo build "a b"'


def test_run_sync_success_and_failure() -> None:
    ok = run_sync(py("print('v1.2.3')"))
    assert ok.ok and ok.stdout_tail == ("v1.2.3",)
    missing = run_sync(ProcessSpec("/nonexistent/prog"))
    assert missing.outcome is ProcessOutcome.FAILED_TO_START
    slow = run_sync(py("import time; time.sleep(5)", timeout_ms=200))
    assert slow.outcome is ProcessOutcome.TIMED_OUT


def test_shutdown_kills_synchronously(qtbot) -> None:
    runner = ProcessRunner()
    with qtbot.waitSignal(runner.started, timeout=5000):
        runner.start(py("import time; time.sleep(30)"))
    with qtbot.waitSignal(runner.finished, timeout=5000) as blocker:
        runner.shutdown()
    assert blocker.args[0].outcome is ProcessOutcome.CANCELLED
    assert not runner.is_running
    runner.shutdown()  # nothing running: no-op
