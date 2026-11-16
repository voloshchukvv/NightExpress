"""Parse ``cargo build --message-format=json-diagnostic-rendered-ansi`` output.

Cargo writes one JSON object per line to stdout. The reasons we care about:

- ``compiler-message``: a rustc diagnostic -> :class:`DiagnosticEvent`
- ``compiler-artifact`` of a ``bin`` target with an ``executable`` -> :class:`ArtifactEvent`
- ``build-finished`` -> :class:`FinishedEvent`

Everything else (dependency artifacts, build-script output) is ignored. Human-readable progress
("Compiling ...") goes to stderr and is handled by the caller as plain log lines.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nightexpress.models.entities import Diagnostic, DiagnosticLevel

_LEVELS = {
    "error": DiagnosticLevel.ERROR,
    "error: internal compiler error": DiagnosticLevel.ERROR,
    "warning": DiagnosticLevel.WARNING,
    "note": DiagnosticLevel.NOTE,
    "help": DiagnosticLevel.HELP,
}

# Summary lines that duplicate information already reported by real diagnostics.
_NOISE_RE = re.compile(
    r"^(aborting due to|\d+ warnings? emitted|could not compile|"
    r"some errors have detailed explanations|for more information about this error)",
    re.IGNORECASE,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


@dataclass(frozen=True)
class DiagnosticEvent:
    diagnostic: Diagnostic


@dataclass(frozen=True)
class ArtifactEvent:
    executable: Path
    package_name: str


@dataclass(frozen=True)
class FinishedEvent:
    success: bool


CargoEvent = DiagnosticEvent | ArtifactEvent | FinishedEvent


class CargoMessageParser:
    """Stateless per line; feed it every stdout line of one cargo invocation."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir

    def parse_line(self, line: str) -> CargoEvent | None:
        line = line.strip()
        if not line.startswith("{"):
            return None
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None

        reason = data.get("reason")
        if reason == "compiler-message":
            diagnostic = self._diagnostic(data.get("message") or {})
            return DiagnosticEvent(diagnostic) if diagnostic else None
        if reason == "compiler-artifact":
            return self._artifact(data)
        if reason == "build-finished":
            return FinishedEvent(bool(data.get("success")))
        return None

    # --- internals -------------------------------------------------------------------------

    def _diagnostic(self, message: dict[str, Any]) -> Diagnostic | None:
        level = _LEVELS.get(str(message.get("level", "")))
        text = str(message.get("message", ""))
        if level is None or _NOISE_RE.match(text):
            return None  # e.g. "failure-note: For more information ... rustc --explain"

        file = line = column = None
        span = next((s for s in message.get("spans") or [] if s.get("is_primary")), None)
        if span is not None:
            # Follow macro expansions back to the user's code when possible: the outermost
            # call site inside the project (relative path), however many macros of
            # dependencies (absolute paths) lie in between.
            outer = span
            while outer.get("expansion") and outer["expansion"].get("span"):
                outer = outer["expansion"]["span"]
                name = str(outer.get("file_name", ""))
                if name and not name.startswith("<") and not Path(name).is_absolute():
                    span = outer
            file = self._resolve(str(span.get("file_name", "")))
            line = span.get("line_start") or None
            column = span.get("column_start") or None

        code = message.get("code") or {}
        return Diagnostic(
            level=level,
            message=text,
            file=file,
            line=line,
            column=column,
            code=code.get("code") if isinstance(code, dict) else None,
            rendered=str(message.get("rendered") or text),
        )

    def _resolve(self, file_name: str) -> Path | None:
        if not file_name or file_name.startswith("<"):  # e.g. "<::core::macros::panic>"
            return None
        path = Path(file_name)
        return path if path.is_absolute() else self._project_dir / path

    def _artifact(self, data: dict[str, Any]) -> ArtifactEvent | None:
        executable = data.get("executable")
        target = data.get("target") or {}
        if not executable or "bin" not in (target.get("kind") or []):
            return None
        return ArtifactEvent(Path(executable), str(target.get("name", "")))
