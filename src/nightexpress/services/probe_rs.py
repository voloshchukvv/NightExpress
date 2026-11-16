"""probe-rs CLI: list debug probes, check the connected chip, download firmware.

The CLI output is not a stable API, so everything that parses it lives here and is covered by
tests on output recorded from probe-rs 0.32. Raw output is always forwarded to the log.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from nightexpress.i18n import N_, _
from nightexpress.models.entities import ProbeInfo
from nightexpress.services.process_runner import (
    ProcessOutcome,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)

LIST_TIMEOUT_MS = 10_000
READ_TIMEOUT_MS = 20_000
DOWNLOAD_TIMEOUT_MS = 180_000

# "[0]: STLink V2 -- 0483:3748:2C3B15157416303030303032 (ST-LINK)"
_PROBE_RE = re.compile(
    r"^\[\d+\]:\s*(?P<name>.+?)\s+--\s+(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})"
    r"(?::(?P<serial>[^\s()]+))?(?:\s*\((?P<kind>[^)]*)\))?\s*$"
)
# "e0042000: 10006412"
_READ_RE = re.compile(r"^\s*[0-9a-fA-F]+:\s*([0-9a-fA-F]+)")

# DBGMCU_IDCODE.DEV_ID -> human-readable line (used to explain a mismatch).
KNOWN_DEV_IDS: Mapping[int, str] = {
    0x412: "STM32F1 low-density (F103x4/x6)",
    0x410: "STM32F1 medium-density (F103x8/xB)",
    0x414: "STM32F1 high-density (F103xC/xD/xE)",
    0x430: "STM32F1 XL-density",
    0x418: "STM32F105/F107",
    0x423: "STM32F401xB/xC",
    0x433: "STM32F401xD/xE",
    0x431: "STM32F411",
    0x413: "STM32F405/407/415/417",
    0x415: "STM32L47x/L48x",
}


class ProbeRsErrorKind(Enum):
    PROBE_NOT_FOUND = "probe_not_found"
    PROBE_UNAVAILABLE = "probe_unavailable"  # in use by another program / no USB permission
    TARGET_NOT_RESPONDING = "target_not_responding"
    UNKNOWN_CHIP = "unknown_chip"
    VERIFY_FAILED = "verify_failed"
    WRONG_CHIP = "wrong_chip"
    NOT_INSTALLED = "not_installed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


# User-facing explanations (Ukrainian UI).
ERROR_HINTS: Mapping[ProbeRsErrorKind, str] = {
    ProbeRsErrorKind.PROBE_NOT_FOUND: (
        N_(
            "ST-Link не знайдено. Перевірте USB-кабель і виберіть програматор у меню "
            "кнопки підключення."
        )
    ),
    ProbeRsErrorKind.PROBE_UNAVAILABLE: (
        N_(
            "ST-Link зайнятий іншою програмою (STM32CubeIDE, OpenOCD, інший probe-rs) або немає "
            "доступу до USB. Закрийте інші програми; на Linux додайте правила udev."
        )
    ),
    ProbeRsErrorKind.TARGET_NOT_RESPONDING: (
        N_(
            "Чип не відповідає. Перевірте дроти SWDIO/SWCLK/GND, живлення плати й перемичку "
            "BOOT0 = 0. Якщо прошивка вимикає SWD, спробуйте ще раз, утримуючи Reset."
        )
    ),
    ProbeRsErrorKind.UNKNOWN_CHIP: N_("probe-rs не знає цього чипа: оновіть probe-rs."),
    ProbeRsErrorKind.VERIFY_FAILED: (
        N_(
            "Записане не збігається з прошивкою. Спробуйте ще раз; якщо повторюється — можливо, "
            "flash захищена від запису або чип пошкоджений."
        )
    ),
    ProbeRsErrorKind.WRONG_CHIP: N_("Підключено не той чип, для якого зібрано проєкт."),
    ProbeRsErrorKind.NOT_INSTALLED: N_("probe-rs не встановлено."),
    ProbeRsErrorKind.CANCELLED: N_("Прошивання зупинено."),
    ProbeRsErrorKind.TIMEOUT: N_("probe-rs не відповідає занадто довго."),
    ProbeRsErrorKind.UNKNOWN: N_("probe-rs повідомив про помилку — подробиці в журналі."),
}

_PATTERNS: tuple[tuple[ProbeRsErrorKind, tuple[str, ...]], ...] = (
    (ProbeRsErrorKind.PROBE_NOT_FOUND, ("no connected probes", "no probe", "probe not found")),
    (
        ProbeRsErrorKind.PROBE_UNAVAILABLE,
        ("in use", "busy", "access denied", "permission denied", "insufficient permissions"),
    ),
    (ProbeRsErrorKind.UNKNOWN_CHIP, ("not found in the database", "unknown chip", "no target")),
    (ProbeRsErrorKind.VERIFY_FAILED, ("verif",)),
    (
        ProbeRsErrorKind.TARGET_NOT_RESPONDING,
        (
            "connecting to the chip was unsuccessful",
            "did not respond",
            "communication error",
            "communication with the target",
            "swdapfault",
            "idcode",
            "no device",
            "failed to attach",
        ),
    ),
)


@dataclass(frozen=True)
class ProbeRsError:
    kind: ProbeRsErrorKind
    hint: str
    details: str = ""


def parse_probe_list(text: str) -> list[ProbeInfo]:
    probes = []
    for line in text.splitlines():
        match = _PROBE_RE.match(line.strip())
        if match:
            probes.append(
                ProbeInfo(
                    name=match["name"].strip(),
                    vid=int(match["vid"], 16),
                    pid=int(match["pid"], 16),
                    serial_number=match["serial"] or None,
                )
            )
    return probes


def parse_read_word(text: str) -> int | None:
    for line in text.splitlines():
        match = _READ_RE.match(line)
        if match:
            return int(match.group(1), 16)
    return None


def classify_error(text: str) -> ProbeRsError:
    lowered = text.lower()
    for kind, needles in _PATTERNS:
        if any(n in lowered for n in needles):
            return ProbeRsError(kind, _(ERROR_HINTS[kind]), text.strip())
    return ProbeRsError(
        ProbeRsErrorKind.UNKNOWN, _(ERROR_HINTS[ProbeRsErrorKind.UNKNOWN]), text.strip()
    )


def describe_dev_id(dev_id: int) -> str:
    return KNOWN_DEV_IDS.get(
        dev_id, _("невідомий чип (DEV_ID 0x{dev_id:03X})").format(dev_id=dev_id)
    )


def _result_error(result: ProcessResult) -> ProbeRsError:
    if result.outcome is ProcessOutcome.FAILED_TO_START:
        return ProbeRsError(
            ProbeRsErrorKind.NOT_INSTALLED,
            _(ERROR_HINTS[ProbeRsErrorKind.NOT_INSTALLED]),
            result.error,
        )
    if result.outcome is ProcessOutcome.CANCELLED:
        return ProbeRsError(ProbeRsErrorKind.CANCELLED, _(ERROR_HINTS[ProbeRsErrorKind.CANCELLED]))
    if result.outcome is ProcessOutcome.TIMED_OUT:
        return ProbeRsError(ProbeRsErrorKind.TIMEOUT, _(ERROR_HINTS[ProbeRsErrorKind.TIMEOUT]))
    return classify_error("\n".join((*result.stderr_tail, *result.stdout_tail)))


@dataclass(frozen=True)
class ChipCheck:
    idcode: int | None
    expected_dev_id: int
    error: ProbeRsError | None = None

    @property
    def dev_id(self) -> int | None:
        return None if self.idcode is None else self.idcode & 0xFFF

    @property
    def ok(self) -> bool:
        return self.error is None and self.dev_id == self.expected_dev_id


@dataclass(frozen=True)
class DownloadResult:
    error: ProbeRsError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ProbeRsService(QObject):
    """Asynchronous probe-rs operations; one listing and one target operation at a time."""

    log_line = Signal(str)
    probes_listed = Signal(object)  # list[ProbeInfo] (empty also when probe-rs is missing)
    chip_checked = Signal(object)  # ChipCheck
    download_finished = Signal(object)  # DownloadResult

    def __init__(
        self,
        command: Sequence[str] = ("probe-rs",),
        search_path: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._command: tuple[str, ...] = tuple(command)
        self._search_path = search_path
        self._list_runner = ProcessRunner(self)
        self._list_runner.finished.connect(self._on_listed)
        self._target_runner = ProcessRunner(self)
        self._target_runner.stdout_line.connect(self.log_line)
        self._target_runner.stderr_line.connect(self.log_line)
        self._target_runner.finished.connect(self._on_target_finished)
        self._pending: str | None = None  # "read" | "download"
        self._expected_dev_id = 0

    def set_command(self, command: Sequence[str]) -> None:
        if not command:
            raise ValueError("command is empty")
        self._command = tuple(command)

    @property
    def is_busy(self) -> bool:
        return self._target_runner.is_running

    def _spec(self, args: Sequence[str], timeout_ms: int) -> ProcessSpec:
        env = {"PATH": self._search_path} if self._search_path else None
        return ProcessSpec(
            self._command[0], (*self._command[1:], *args), env=env, timeout_ms=timeout_ms
        )

    @staticmethod
    def _target_args(chip: str, probe: str | None) -> list[str]:
        args = ["--chip", chip, "--non-interactive"]
        if probe:
            args += ["--probe", probe]
        return args

    # --- operations ------------------------------------------------------------------------

    def list_probes(self) -> bool:
        """Start a listing; False if one is already running."""
        if self._list_runner.is_running:
            return False
        self._list_runner.start(self._spec(["list"], LIST_TIMEOUT_MS))
        return True

    def check_chip(self, chip: str, probe: str | None, idcode_address: int, dev_id: int) -> None:
        self._expected_dev_id = dev_id
        self._start_target(
            "read",
            ["read", *self._target_args(chip, probe), "b32", f"0x{idcode_address:08X}", "1"],
            READ_TIMEOUT_MS,
        )

    def download(self, elf: Path, chip: str, probe: str | None) -> None:
        args = [
            "download",
            *self._target_args(chip, probe),
            "--verify",
            "--reset",
            "--disable-progressbars",
            str(elf),
        ]
        self._start_target("download", args, DOWNLOAD_TIMEOUT_MS)

    def cancel(self) -> None:
        self._target_runner.cancel()

    def shutdown(self) -> None:
        for runner in (self._list_runner, self._target_runner):
            runner.shutdown()

    # --- internals -------------------------------------------------------------------------

    def _start_target(self, operation: str, args: list[str], timeout_ms: int) -> None:
        if self._target_runner.is_running:
            raise RuntimeError("probe-rs is already running")
        self._pending = operation
        self._target_runner.start(self._spec(args, timeout_ms))

    def _on_listed(self, result: ProcessResult) -> None:
        probes = parse_probe_list("\n".join(result.stdout_tail)) if result.ok else []
        self.probes_listed.emit(probes)

    def _on_target_finished(self, result: ProcessResult) -> None:
        operation, self._pending = self._pending, None
        if operation == "read":
            idcode = parse_read_word("\n".join(result.stdout_tail)) if result.ok else None
            error = None
            if not result.ok:
                error = _result_error(result)
            elif idcode is None:
                error = classify_error("\n".join(result.stdout_tail) or "no output")
            self.chip_checked.emit(ChipCheck(idcode, self._expected_dev_id, error))
        elif operation == "download":
            self.download_finished.emit(
                DownloadResult(None if result.ok else _result_error(result))
            )
