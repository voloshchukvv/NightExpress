"""RTT output of the running program, read through the debug probe (``probe-rs attach``).

The program writes into a ring buffer in its own RAM (the RTT control block, usually through
``defmt-rtt``); probe-rs reads it over SWD while the program runs and decodes defmt frames
with the ELF. No UART and no extra wires: the ST-Link that flashed the board is enough.

One attach runs at a time and keeps the probe busy, so it is stopped before every other use
of the probe (see ``stop``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection
from PySide6.QtCore import QObject, Signal

from nightexpress.services.probe_rs import ProbeRsError, _result_error
from nightexpress.services.process_runner import (
    ProcessOutcome,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)

# The symbol of the RTT control block (defmt-rtt, rtt-target): the ELF says where it is.
RTT_SYMBOL = "_SEGGER_RTT"


def has_rtt(elf: Path) -> bool:
    """Whether the program has an RTT control block, so that there is something to read."""
    try:
        with elf.open("rb") as stream:
            for section in ELFFile(stream).iter_sections():
                if isinstance(section, SymbolTableSection) and section.get_symbol_by_name(
                    RTT_SYMBOL
                ):
                    return True
    except (OSError, ELFError):
        return False
    return False


def attach_args(elf: Path, chip: str, probe: str | None) -> list[str]:
    args = ["attach", "--chip", chip, "--non-interactive"]
    if probe:
        args += ["--probe", probe]
    # probe-rs would switch the channels to "block when full": once we detach, the program
    # would then hang as soon as the buffer fills up. Dropping messages is the safe choice.
    args += ["--rtt-channel-mode", "no-block-skip", "--no-location", str(elf)]
    return args


class RttService(QObject):
    line = Signal(str)  # a line of the program's output (or a message of probe-rs)
    started = Signal()
    stopped = Signal(object)  # ProbeRsError | None: None when stopped on purpose

    def __init__(self, search_path: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._search_path = search_path
        self._runner = ProcessRunner(self)
        self._runner.stdout_line.connect(self.line)
        self._runner.stderr_line.connect(self.line)
        self._runner.started.connect(self.started)
        self._runner.finished.connect(self._on_finished)
        self._after_stop: list[Callable[[], None]] = []

    @property
    def is_running(self) -> bool:
        return self._runner.is_running

    def attach(self, command: Sequence[str], elf: Path, chip: str, probe: str | None) -> None:
        """Start reading; ``command`` is how probe-rs is started (e.g. ``("probe-rs",)``)."""
        if self._runner.is_running:
            raise RuntimeError("RTT is already attached")
        env = {"PATH": self._search_path} if self._search_path else None
        args = (*command[1:], *attach_args(elf, chip, probe))
        self._runner.start(ProcessSpec(command[0], args, env=env))

    def stop(self, then: Callable[[], None] | None = None) -> None:
        """Detach; ``then`` runs once the probe is free again (at once if nothing runs)."""
        if not self._runner.is_running:
            if then is not None:
                then()
            return
        if then is not None:
            self._after_stop.append(then)
        self._runner.cancel()

    def shutdown(self) -> None:
        self._after_stop.clear()
        self._runner.shutdown()

    def _on_finished(self, result: ProcessResult) -> None:
        stopped_on_purpose = result.outcome is ProcessOutcome.CANCELLED or result.ok
        self.stopped.emit(None if stopped_on_purpose else _result_error(result))
        callbacks, self._after_stop = self._after_stop, []
        for callback in callbacks:
            callback()


__all__ = ["RTT_SYMBOL", "ProbeRsError", "RttService", "attach_args", "has_rtt"]
