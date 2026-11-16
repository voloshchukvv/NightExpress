"""Run ``cargo build`` for a firmware project and report structured results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from nightexpress.models.entities import BuildProfile, Diagnostic
from nightexpress.services.cargo_message_parser import (
    ArtifactEvent,
    CargoMessageParser,
    DiagnosticEvent,
    FinishedEvent,
)
from nightexpress.services.process_runner import (
    ProcessOutcome,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)

MESSAGE_FORMAT = "--message-format=json-diagnostic-rendered-ansi"


@dataclass(frozen=True)
class BuildResult:
    outcome: ProcessOutcome
    success: bool
    elf_path: Path | None
    diagnostics: tuple[Diagnostic, ...]
    exit_code: int | None = None
    error: str = ""  # process-level error (cargo not found, crashed, ...)


class CargoService(QObject):
    """One build at a time. Signals are delivered in the thread that owns the service."""

    log_line = Signal(str)  # human-readable output, may contain ANSI colours
    diagnostic = Signal(object)  # Diagnostic
    finished = Signal(object)  # BuildResult

    def __init__(
        self,
        cargo_command: Sequence[str] = ("cargo",),
        search_path: str | None = None,
        parent: QObject | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._command: tuple[str, ...] = ()
        self.set_command(cargo_command)
        self._search_path = search_path
        self._extra_env = dict(extra_env or {})
        self._runner = ProcessRunner(self)
        self._runner.stdout_line.connect(self._on_stdout)
        self._runner.stderr_line.connect(self.log_line)
        self._runner.finished.connect(self._on_finished)
        self._parser: CargoMessageParser | None = None
        self._diagnostics: list[Diagnostic] = []
        self._elf: Path | None = None
        self._cargo_success: bool | None = None

    @property
    def is_running(self) -> bool:
        return self._runner.is_running

    def set_command(self, cargo_command: Sequence[str]) -> None:
        """Absolute path is best: QProcess resolves bare names with the app's own PATH."""
        if not cargo_command:
            raise ValueError("cargo_command is empty")
        self._command = tuple(cargo_command)

    def _env(self) -> dict[str, str] | None:
        env = dict(self._extra_env)
        if self._search_path:
            env["PATH"] = self._search_path
        return env or None

    def build_spec(self, project_dir: Path, profile: BuildProfile) -> ProcessSpec:
        args = [*self._command[1:], "build", MESSAGE_FORMAT, "--color=always"]
        if profile is BuildProfile.RELEASE:
            args.append("--release")
        return ProcessSpec(self._command[0], tuple(args), cwd=project_dir, env=self._env())

    def clean(self, project_dir: Path) -> None:
        """`cargo clean` (only on explicit user request: it throws away the build cache)."""
        if self.is_running:
            raise RuntimeError("cargo is already running")
        self._parser = None
        self._diagnostics = []
        self._elf = None
        self._cargo_success = None
        args = (*self._command[1:], "clean", "--color=always")
        self._runner.start(ProcessSpec(self._command[0], args, cwd=project_dir, env=self._env()))

    def build(self, project_dir: Path, profile: BuildProfile = BuildProfile.RELEASE) -> None:
        if self.is_running:
            raise RuntimeError("a build is already running")
        self._parser = CargoMessageParser(project_dir)
        self._diagnostics = []
        self._elf = None
        self._cargo_success = None
        self._runner.start(self.build_spec(project_dir, profile))

    def cancel(self) -> None:
        self._runner.cancel()

    def shutdown(self) -> None:
        self._runner.shutdown()

    # --- internals -------------------------------------------------------------------------

    def _on_stdout(self, line: str) -> None:
        if self._parser is None:  # `cargo clean` output is plain text
            if line.strip():
                self.log_line.emit(line)
            return
        event = self._parser.parse_line(line)
        if isinstance(event, DiagnosticEvent):
            self._diagnostics.append(event.diagnostic)
            for rendered in event.diagnostic.rendered.rstrip("\n").splitlines():
                self.log_line.emit(rendered)
            self.diagnostic.emit(event.diagnostic)
        elif isinstance(event, ArtifactEvent):
            self._elf = event.executable
        elif isinstance(event, FinishedEvent):
            self._cargo_success = event.success
        elif event is None and line.strip() and not line.lstrip().startswith("{"):
            self.log_line.emit(line)

    def _on_finished(self, result: ProcessResult) -> None:
        success = result.ok and self._cargo_success is not False
        self.finished.emit(
            BuildResult(
                outcome=result.outcome,
                success=success,
                elf_path=self._elf if success else None,
                diagnostics=tuple(self._diagnostics),
                exit_code=result.exit_code,
                error=result.error,
            )
        )
