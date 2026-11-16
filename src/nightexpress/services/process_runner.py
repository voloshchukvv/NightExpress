"""The single way NightExpress starts external processes.

Rules: never through a shell, executable and every argument passed
separately, explicit working directory, bounded output, timeouts and cancellation.

- :class:`ProcessRunner` wraps ``QProcess`` for long operations (cargo build, probe-rs download):
  it never blocks the UI thread and reports output line by line through signals.
- :func:`run_sync` is for short queries (``cargo --version``) executed from a worker thread.
"""

from __future__ import annotations

import os
import subprocess
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

MAX_LINE_BYTES = 64 * 1024  # longer lines are emitted in chunks
TAIL_LINES = 200  # lines of each stream kept in ProcessResult for error reporting
CANCEL_GRACE_MS = 2000  # terminate -> kill delay


class ProcessOutcome(Enum):
    FINISHED = "finished"  # exited normally; check exit_code
    FAILED_TO_START = "failed_to_start"
    CRASHED = "crashed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProcessSpec:
    program: str
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env: Mapping[str, str] | None = None  # overrides on top of the current environment
    timeout_ms: int | None = None
    # A lone "\r" ends a line too: tools that redraw a progress bar in place (dfu-util).
    carriage_return_ends_line: bool = False

    def __post_init__(self) -> None:
        if not self.program:
            raise ValueError("program is empty")
        if isinstance(self.args, str):
            raise TypeError("args must be a sequence of strings, not a single string")

    @property
    def command_line(self) -> str:
        """Human-readable command, for messages and tests (never executed)."""
        return subprocess.list2cmdline([self.program, *self.args])


@dataclass(frozen=True)
class ProcessResult:
    outcome: ProcessOutcome
    exit_code: int | None = None
    stdout_tail: tuple[str, ...] = ()
    stderr_tail: tuple[str, ...] = ()
    error: str = ""  # QProcess/OS error text for FAILED_TO_START and CRASHED

    @property
    def ok(self) -> bool:
        return self.outcome is ProcessOutcome.FINISHED and self.exit_code == 0


@dataclass
class _LineSplitter:
    """Turns a byte stream into decoded lines; bounded memory per line."""

    pending: bytearray = field(default_factory=bytearray)
    carriage_return_ends_line: bool = False

    def feed(self, data: bytes) -> list[str]:
        if self.carriage_return_ends_line:
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        self.pending += data
        lines: list[str] = []
        while True:
            index = self.pending.find(b"\n")
            if index < 0:
                break
            raw = self.pending[:index]
            del self.pending[: index + 1]
            lines.extend(
                _decode(raw[i : i + MAX_LINE_BYTES])
                for i in range(0, max(len(raw), 1), MAX_LINE_BYTES)
            )
        while len(self.pending) > MAX_LINE_BYTES:
            lines.append(_decode(self.pending[:MAX_LINE_BYTES]))
            del self.pending[:MAX_LINE_BYTES]
        return lines

    def flush(self) -> list[str]:
        if not self.pending:
            return []
        line = _decode(self.pending)
        self.pending.clear()
        return [line]


def _decode(raw: bytes | bytearray) -> str:
    return bytes(raw).decode("utf-8", errors="replace").rstrip("\r")


class ProcessRunner(QObject):
    """Runs one process at a time; reusable after ``finished``."""

    started = Signal()
    stdout_line = Signal(str)
    stderr_line = Signal(str)
    finished = Signal(object)  # ProcessResult

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._stdout = _LineSplitter()
        self._stderr = _LineSplitter()
        self._stdout_tail: deque[str] = deque(maxlen=TAIL_LINES)
        self._stderr_tail: deque[str] = deque(maxlen=TAIL_LINES)
        self._forced: ProcessOutcome | None = None
        self._timeout = QTimer(self, singleShot=True)
        self._timeout.timeout.connect(self._on_timeout)
        self._kill_timer = QTimer(self, singleShot=True)
        self._kill_timer.timeout.connect(self._kill)

    @property
    def is_running(self) -> bool:
        return self._process is not None

    def start(self, spec: ProcessSpec) -> None:
        if self._process is not None:
            raise RuntimeError("a process is already running")
        cr = spec.carriage_return_ends_line
        self._stdout, self._stderr = (
            _LineSplitter(carriage_return_ends_line=cr),
            _LineSplitter(carriage_return_ends_line=cr),
        )
        self._stdout_tail.clear()
        self._stderr_tail.clear()
        self._forced = None

        process = QProcess(self)
        process.setProgram(spec.program)
        process.setArguments(list(spec.args))
        if spec.cwd is not None:
            process.setWorkingDirectory(str(spec.cwd))
        if spec.env:
            env = QProcessEnvironment.systemEnvironment()
            for key, value in spec.env.items():
                env.insert(key, value)
            process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._on_error)
        process.finished.connect(self._on_finished)
        process.started.connect(self.started)
        self._process = process

        if spec.timeout_ms is not None:
            self._timeout.start(spec.timeout_ms)
        process.start()

    def cancel(self) -> None:
        """Ask the process to stop; it is killed if it does not exit within the grace period."""
        if self._process is None:
            return
        self._forced = self._forced or ProcessOutcome.CANCELLED
        self._process.terminate()
        self._kill_timer.start(CANCEL_GRACE_MS)

    def shutdown(self, wait_ms: int = 1000) -> None:
        """Kill the process synchronously (application exit): no orphans, no Qt warnings."""
        process = self._process
        if process is None:
            return
        self._forced = ProcessOutcome.CANCELLED
        process.kill()
        process.waitForFinished(wait_ms)

    # --- internals -------------------------------------------------------------------------

    def _read_stdout(self) -> None:
        if self._process is not None:
            data = bytes(self._process.readAllStandardOutput().data())
            self._emit(self._stdout.feed(data), self.stdout_line, self._stdout_tail)

    def _read_stderr(self) -> None:
        if self._process is not None:
            data = bytes(self._process.readAllStandardError().data())
            self._emit(self._stderr.feed(data), self.stderr_line, self._stderr_tail)

    @staticmethod
    def _emit(lines: list[str], signal: object, tail: deque[str]) -> None:
        for line in lines:
            tail.append(line)
            signal.emit(line)  # type: ignore[attr-defined]

    def _on_timeout(self) -> None:
        if self._process is not None:
            self._forced = ProcessOutcome.TIMED_OUT
            self._kill()

    def _kill(self) -> None:
        if self._process is not None:
            self._process.kill()

    def _on_error(self, error: QProcess.ProcessError) -> None:
        # FailedToStart never produces `finished`, so it has to end the run here.
        if error is QProcess.ProcessError.FailedToStart and self._process is not None:
            message = self._process.errorString()
            self._complete(ProcessResult(ProcessOutcome.FAILED_TO_START, error=message))

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if self._process is None:
            return
        self._read_stdout()
        self._read_stderr()
        self._emit(self._stdout.flush(), self.stdout_line, self._stdout_tail)
        self._emit(self._stderr.flush(), self.stderr_line, self._stderr_tail)

        if self._forced is not None:
            outcome, code = self._forced, None
        elif exit_status is QProcess.ExitStatus.CrashExit:
            outcome, code = ProcessOutcome.CRASHED, None
        else:
            outcome, code = ProcessOutcome.FINISHED, exit_code
        error = self._process.errorString() if outcome is ProcessOutcome.CRASHED else ""
        self._complete(
            ProcessResult(outcome, code, tuple(self._stdout_tail), tuple(self._stderr_tail), error)
        )

    def _complete(self, result: ProcessResult) -> None:
        self._timeout.stop()
        self._kill_timer.stop()
        process, self._process = self._process, None
        if process is not None:
            process.deleteLater()
        self.finished.emit(result)


def run_sync(spec: ProcessSpec) -> ProcessResult:
    """Run a short command to completion. Must not be called from the UI thread."""
    env = None
    if spec.env:
        env = {**os.environ, **spec.env}
    try:
        completed = subprocess.run(
            [spec.program, *spec.args],
            cwd=spec.cwd,
            env=env,
            capture_output=True,
            timeout=None if spec.timeout_ms is None else spec.timeout_ms / 1000,
            check=False,
        )
    except FileNotFoundError as exc:
        return ProcessResult(ProcessOutcome.FAILED_TO_START, error=str(exc))
    except PermissionError as exc:
        return ProcessResult(ProcessOutcome.FAILED_TO_START, error=str(exc))
    except subprocess.TimeoutExpired:
        return ProcessResult(ProcessOutcome.TIMED_OUT)

    def tail(raw: bytes) -> tuple[str, ...]:
        lines = raw.decode("utf-8", errors="replace").splitlines()
        return tuple(lines[-TAIL_LINES:])

    return ProcessResult(
        ProcessOutcome.FINISHED,
        completed.returncode,
        tail(completed.stdout),
        tail(completed.stderr),
    )
