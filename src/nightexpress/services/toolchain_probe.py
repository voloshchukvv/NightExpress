"""Locate the Rust/embedded tools and read their versions.

GUI applications on macOS do not inherit the shell PATH (an app started from Finder sees only
``/usr/bin:/bin:/usr/sbin:/sbin``), so the usual install locations are searched explicitly and
the same augmented PATH is passed to every child process.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nightexpress.i18n import _
from nightexpress.models.entities import ToolStatus
from nightexpress.services.process_runner import ProcessResult, ProcessSpec, run_sync

VERSION_TIMEOUT_MS = 10_000

_VERSION_RE = re.compile(r"^\S+\s+v?(\d+\.\d+(?:\.\d+)?\S*)")  # dfu-util: "0.11"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    required: bool


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("rustup", required=True),
    ToolSpec("cargo", required=True),
    ToolSpec("rustc", required=True),
    ToolSpec("probe-rs", required=False),  # only the ST-Link upload method needs it
    ToolSpec("dfu-util", required=False),  # only flashing over USB DFU (Black Pill) needs it
    ToolSpec("rust-analyzer", required=False),  # editor intelligence (after MVP)
)

_RUSTUP_UNIX = "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
_RUSTUP_URL = "https://rustup.rs"
_PROBE_RS_URL = "https://probe.rs/docs/getting-started/installation/"
_PROBE_RS_SETUP_URL = "https://probe.rs/docs/getting-started/probe-setup/"
_RUST_ANALYZER_URL = "https://rust-analyzer.github.io/"
_DFU_UTIL_URL = "https://dfu-util.sourceforge.net/"
FIX_DEFAULT_TOOLCHAIN = "rustup default stable"


@dataclass(frozen=True)
class InstallInfo:
    """How to get a missing tool (user-facing text in Ukrainian)."""

    hint: str  # one line for messages and tooltips
    command: str  # to paste into a terminal; "" when there is none (Windows installer)
    url: str  # instructions page


def _platform_key(platform: str) -> str:
    if platform.startswith("win"):
        return "win32"
    if platform == "darwin":
        return "darwin"
    return "linux"


def install_info(tool: str, platform: str = sys.platform) -> InstallInfo:
    key = _platform_key(platform)
    if tool == "rust-analyzer":
        command = "rustup component add rust-analyzer"
        return InstallInfo(
            _("Встановіть rust-analyzer: {command}").format(command=command),
            command,
            _RUST_ANALYZER_URL,
        )
    if tool == "dfu-util":
        if key == "win32":
            return InstallInfo(
                _(
                    "Для прошивки через USB DFU завантажте dfu-util з {url} і поставте "
                    "драйвер WinUSB (Zadig)"
                ).format(url=_DFU_UTIL_URL),
                "",
                _DFU_UTIL_URL,
            )
        command = "brew install dfu-util" if key == "darwin" else "sudo apt install dfu-util"
        return InstallInfo(
            _("Для прошивки через USB DFU встановіть dfu-util: {command}").format(command=command),
            command,
            _DFU_UTIL_URL,
        )
    if tool == "probe-rs":
        command = (
            "brew install probe-rs-tools" if key == "darwin"
            else "cargo install probe-rs-tools --locked"
        )  # fmt: skip
        if key == "linux":
            hint = _(
                "Для ST-Link встановіть probe-rs: {command} і додайте правила udev ({url})"
            ).format(command=command, url=_PROBE_RS_SETUP_URL)
        else:
            hint = _("Для ST-Link встановіть probe-rs: {command}").format(command=command)
        return InstallInfo(hint, command, _PROBE_RS_URL)
    if key == "win32":
        return InstallInfo(
            _("Встановіть Rust: завантажте rustup-init.exe з {url}").format(url=_RUSTUP_URL),
            "",
            _RUSTUP_URL,
        )
    return InstallInfo(
        _("Встановіть Rust: {command}").format(command=_RUSTUP_UNIX),
        _RUSTUP_UNIX,
        _RUSTUP_URL,
    )


def install_hint(tool: str, platform: str = sys.platform) -> str:
    return install_info(tool, platform).hint


def extra_search_dirs(platform: str, home: Path) -> list[Path]:
    dirs = [home / ".cargo" / "bin"]
    if platform == "darwin":
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    elif not platform.startswith("win"):
        dirs += [Path("/usr/local/bin"), home / ".local" / "bin"]
    return dirs


def build_search_path(current: str, platform: str, home: Path) -> str:
    """PATH with the usual tool locations appended (existing entries keep priority)."""
    sep = ";" if platform.startswith("win") else ":"
    entries = [e for e in current.split(sep) if e]
    for extra in map(str, extra_search_dirs(platform, home)):
        if extra not in entries:
            entries.append(extra)
    return sep.join(entries)


def parse_version(output: str) -> str | None:
    """``cargo 1.95.0-nightly (ce69df6 2026-02-12)`` -> ``1.95.0-nightly``."""
    for line in output.splitlines():
        match = _VERSION_RE.match(line.strip())
        if match:
            return match.group(1)
    return None


class ToolchainProbe:
    """Synchronous; call from a worker thread (each tool takes a few milliseconds to a second)."""

    def __init__(
        self,
        platform: str = sys.platform,
        home: Path | None = None,
        env_path: str | None = None,
        which: Callable[..., str | None] = shutil.which,
        run: Callable[[ProcessSpec], ProcessResult] = run_sync,
    ) -> None:
        self._platform = platform
        self._home = home if home is not None else Path.home()
        self._search_path = build_search_path(
            os.environ.get("PATH", "") if env_path is None else env_path, platform, self._home
        )
        self._which = which
        self._run = run

    @property
    def search_path(self) -> str:
        """PATH to use for every child process that needs the Rust toolchain."""
        return self._search_path

    def find(self, name: str) -> Path | None:
        found = self._which(name, path=self._search_path)
        return Path(found) if found else None

    def check(self, tool: ToolSpec) -> ToolStatus:
        info = install_info(tool.name, self._platform)
        path = self.find(tool.name)
        if path is None:
            return ToolStatus(
                tool.name,
                found=False,
                install_hint=info.hint,
                required=tool.required,
                install_command=info.command,
                help_url=info.url,
            )

        # Run outside any project so a rust-toolchain.toml cannot trigger a toolchain download.
        result = self._run(
            ProcessSpec(
                str(path),
                ("--version",),
                cwd=self._home,
                env={"PATH": self._search_path},
                timeout_ms=VERSION_TIMEOUT_MS,
            )
        )
        version = parse_version("\n".join(result.stdout_tail)) if result.ok else None
        if version is None:
            # Present but not usable, e.g. a rustup proxy without a default toolchain.
            detail = next((line for line in result.stderr_tail if line.strip()), result.error)
            message = (
                _("{name} знайдено, але він не працює: {detail}")
                .format(name=tool.name, detail=detail)
                .rstrip(": ")
            )
            command = info.command
            if tool.name in ("cargo", "rustc"):
                message = _("{message}. Спробуйте: {command}").format(
                    message=message, command=FIX_DEFAULT_TOOLCHAIN
                )
                command = FIX_DEFAULT_TOOLCHAIN
            return ToolStatus(
                tool.name,
                found=False,
                path=path,
                install_hint=message,
                required=tool.required,
                install_command=command,
                help_url=info.url,
            )
        return ToolStatus(tool.name, found=True, version=version, path=path, required=tool.required)

    def check_all(self) -> tuple[ToolStatus, ...]:
        return tuple(self.check(tool) for tool in TOOLS)
