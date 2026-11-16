"""Format Rust source with rustfmt (Edit → Format document, "format on save").

The text goes through stdin/stdout, so the file on disk is not touched and the change lands in
the editor as one undoable edit. rustfmt runs in the project folder, which makes rustup pick the
toolchain from rust-toolchain.toml and rustfmt read the project's rustfmt.toml, if any.

Synchronous: formatting one file takes a few hundred milliseconds at most.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path

from nightexpress.i18n import _

TIMEOUT_S = 10
DEFAULT_EDITION = "2021"

_LOCATION_RE = re.compile(r"-->\s*<stdin>:(\d+):(\d+)")


class RustfmtError(Exception):
    """Formatting failed (user-facing message in Ukrainian)."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


def project_edition(project_dir: Path) -> str:
    """``edition`` from Cargo.toml; rustfmt alone would assume 2015."""
    try:
        data = tomllib.loads((project_dir / "Cargo.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_EDITION
    edition = data.get("package", {}).get("edition")
    return str(edition) if isinstance(edition, str | int) else DEFAULT_EDITION


def format_rust(
    text: str,
    command: Sequence[str],
    project_dir: Path,
    env: dict[str, str] | None = None,
) -> str:
    """Return ``text`` formatted by rustfmt, or raise RustfmtError."""
    args = [*command, "--edition", project_edition(project_dir), "--emit", "stdout"]
    try:
        result = subprocess.run(
            args,
            input=text.encode("utf-8"),
            cwd=project_dir,
            env=env,
            capture_output=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RustfmtError(
            _("Не знайдено rustfmt. Встановіть: rustup component add rustfmt")
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RustfmtError(
            _("rustfmt не відповів за {seconds:g} с.").format(seconds=TIMEOUT_S)
        ) from exc
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        if "not installed" in stderr:
            raise RustfmtError(
                _("Для цього toolchain не встановлено rustfmt: rustup component add rustfmt")
            )
        match = _LOCATION_RE.search(stderr)
        line = int(match.group(1)) if match else None
        first = next(
            (ln.strip() for ln in stderr.splitlines() if ln.strip()), _("невідома помилка")
        )
        first = first.removeprefix("error: ")
        if line:
            message = _("Не вдалося відформатувати (рядок {line}): {error}").format(
                line=line, error=first
            )
        else:
            message = _("Не вдалося відформатувати: {error}").format(error=first)
        raise RustfmtError(message, line)
    return result.stdout.decode("utf-8")
