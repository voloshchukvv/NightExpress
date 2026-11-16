from __future__ import annotations

from pathlib import Path

import pytest

from nightexpress.services.process_runner import ProcessOutcome, ProcessResult, ProcessSpec
from nightexpress.services.toolchain_probe import (
    TOOLS,
    ToolchainProbe,
    ToolSpec,
    build_search_path,
    install_hint,
    install_info,
    parse_version,
)

HOME = Path("/Users/dev")


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("cargo 1.95.0-nightly (ce69df6f7 2026-02-12)", "1.95.0-nightly"),
        ("rustc 1.83.0 (90b35a623 2024-11-26)", "1.83.0"),
        ("probe-rs 0.32.0 (git commit: crates.io)", "0.32.0"),
        ("rustup 1.28.2 (e4f3ad6f8 2025-04-28)\ninfo: This is the version for rustup", "1.28.2"),
        ("warning: something\ncargo 1.80.1 (376290515 2024-07-16)", "1.80.1"),
        ("tool v2.0.1", "2.0.1"),
        ("no version here", None),
        ("", None),
    ],
)
def test_parse_version(output: str, version: str | None) -> None:
    assert parse_version(output) == version


def test_search_path_macos_finder_environment() -> None:
    path = build_search_path("/usr/bin:/bin", "darwin", HOME)
    assert path.split(":") == [
        "/usr/bin",
        "/bin",
        "/Users/dev/.cargo/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]


def test_search_path_keeps_existing_priority_without_duplicates() -> None:
    path = build_search_path("/opt/homebrew/bin:/usr/bin", "darwin", HOME)
    entries = path.split(":")
    assert entries[0] == "/opt/homebrew/bin"
    assert entries.count("/opt/homebrew/bin") == 1


def test_search_path_windows_separator() -> None:
    home = Path("C:/Users/dev")
    path = build_search_path(r"C:\Windows", "win32", home)
    assert path.split(";") == [r"C:\Windows", str(home / ".cargo" / "bin")]


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_install_hints_exist_for_every_tool(platform: str) -> None:
    for tool in TOOLS:
        assert install_hint(tool.name, platform)
    assert "brew install probe-rs-tools" in install_hint("probe-rs", "darwin")
    assert "rustup" in install_hint("cargo", platform)


class FakeSystem:
    """Fake `which` + `run` with configurable tools."""

    def __init__(self, tools: dict[str, ProcessResult]) -> None:
        self.tools = tools
        self.specs: list[ProcessSpec] = []

    def which(self, name: str, path: str | None = None) -> str | None:
        assert path is not None and "/Users/dev/.cargo/bin" in path
        return f"/Users/dev/.cargo/bin/{name}" if name in self.tools else None

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        return self.tools[Path(spec.program).name]


def ok(stdout: str) -> ProcessResult:
    return ProcessResult(ProcessOutcome.FINISHED, 0, tuple(stdout.splitlines()))


def make_probe(system: FakeSystem) -> ToolchainProbe:
    return ToolchainProbe("darwin", HOME, "/usr/bin:/bin", system.which, system.run)


def test_check_all_tools_found() -> None:
    system = FakeSystem(
        {
            "rustup": ok("rustup 1.28.2 (e4f3ad6f8 2025-04-28)"),
            "cargo": ok("cargo 1.95.0 (ce69df6f7 2026-02-12)"),
            "rustc": ok("rustc 1.95.0 (873b4beb0 2026-02-15)"),
            "probe-rs": ok("probe-rs 0.32.0 (git commit: crates.io)"),
            "dfu-util": ok("dfu-util 0.11\n\nCopyright 2005-2009 Weston Schmidt, Harald Welte"),
            "rust-analyzer": ok("rust-analyzer 1.95.0 (873b4beb0 2026-02-15)"),
        }
    )
    statuses = {s.name: s for s in make_probe(system).check_all()}

    assert all(s.found for s in statuses.values())
    assert statuses["probe-rs"].version == "0.32.0"
    assert statuses["dfu-util"].version == "0.11"  # two numbers only
    assert not statuses["dfu-util"].required
    assert not statuses["probe-rs"].required
    assert statuses["cargo"].path == Path("/Users/dev/.cargo/bin/cargo")
    # Version queries run outside any project, with the augmented PATH.
    for spec in system.specs:
        assert spec.args == ("--version",)
        assert spec.cwd == HOME
        assert spec.env is not None and "/opt/homebrew/bin" in spec.env["PATH"]
        assert spec.timeout_ms


def test_missing_optional_probe_rs_is_not_blocking() -> None:
    system = FakeSystem({"cargo": ok("cargo 1.95.0 (x)")})
    probe = make_probe(system)
    status = probe.check(ToolSpec("probe-rs", required=False))
    assert not status.found and not status.is_blocking
    assert "brew install probe-rs-tools" in status.install_hint
    assert status.install_command == "brew install probe-rs-tools"
    assert status.help_url.startswith("https://probe.rs/")


def test_missing_required_tool_is_blocking() -> None:
    status = make_probe(FakeSystem({})).check(ToolSpec("cargo", required=True))
    assert status.is_blocking
    assert "rustup" in status.install_hint


def test_rustup_proxy_without_default_toolchain() -> None:
    broken = ProcessResult(
        ProcessOutcome.FINISHED,
        1,
        (),
        ("error: rustup could not choose a version of cargo to run, no default is configured.",),
    )
    status = make_probe(FakeSystem({"cargo": broken})).check(ToolSpec("cargo", required=True))
    assert not status.found
    assert status.path is not None
    assert "rustup could not choose" in status.install_hint
    assert "rustup default stable" in status.install_hint
    assert status.install_command == "rustup default stable"  # not reinstalling Rust


def test_tool_that_times_out() -> None:
    hung = ProcessResult(ProcessOutcome.TIMED_OUT)
    status = make_probe(FakeSystem({"probe-rs": hung})).check(ToolSpec("probe-rs", required=False))
    assert not status.found


def test_live_cargo_found_even_with_finder_path() -> None:
    """On a developer machine cargo must be found although PATH lacks ~/.cargo/bin."""
    if not (Path.home() / ".cargo" / "bin" / "cargo").exists():
        pytest.skip("cargo is not installed via rustup on this machine")
    probe = ToolchainProbe(env_path="/usr/bin:/bin")
    status = probe.check(ToolSpec("cargo", required=True))
    assert status.found and status.version


@pytest.mark.parametrize(
    ("tool", "platform", "command", "url"),
    [
        ("cargo", "darwin", "sh.rustup.rs | sh", "https://rustup.rs"),
        ("rustup", "linux", "sh.rustup.rs | sh", "https://rustup.rs"),
        ("cargo", "win32", "", "https://rustup.rs"),  # an installer, not a command
        ("probe-rs", "darwin", "brew install probe-rs-tools", "probe.rs/docs"),
        ("probe-rs", "linux", "cargo install probe-rs-tools --locked", "probe.rs/docs"),
        ("rust-analyzer", "darwin", "rustup component add rust-analyzer", "rust-analyzer"),
    ],
)
def test_install_info(tool: str, platform: str, command: str, url: str) -> None:
    info = install_info(tool, platform)
    assert (command in info.command) if command else info.command == ""
    assert url in info.url and info.url.startswith("https://")
    assert info.hint == install_hint(tool, platform)
