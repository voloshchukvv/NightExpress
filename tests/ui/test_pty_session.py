"""PtySession with real shells in real pseudo-terminals (macOS / Linux)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nightexpress.services import pty_session
from nightexpress.services.pty_session import (
    PtyError,
    PtySession,
    default_shell,
    shell_environment,
)

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pseudo-terminals"),
]


class Collector:
    def __init__(self, session: PtySession) -> None:
        self.data = bytearray()
        session.output.connect(self.data.extend)

    @property
    def text(self) -> str:
        return self.data.decode(errors="replace")


@pytest.fixture
def session(qtbot):
    s = PtySession()
    yield s
    s.close()


def test_default_shell_and_environment() -> None:
    assert default_shell("darwin", {"SHELL": "/opt/homebrew/bin/fish"}) == [
        "/opt/homebrew/bin/fish",
        "-l",
    ]
    assert default_shell("darwin", {}) == ["/bin/zsh", "-l"]
    assert default_shell("linux", {}) == ["/bin/bash", "-l"]
    assert default_shell("win32", {})[0] == "powershell.exe"

    env = shell_environment({"PATH": "/usr/bin", "HOME": "/h"}, search_path="/usr/bin:/cargo")
    assert env["TERM"] == "xterm-256color" and env["PATH"] == "/usr/bin:/cargo"
    assert env["LANG"] == "en_US.UTF-8" and env["HOME"] == "/h"
    assert shell_environment({"LANG": "uk_UA.UTF-8"})["LANG"] == "uk_UA.UTF-8"


def test_shell_runs_in_a_real_terminal(qtbot, session: PtySession, tmp_path: Path) -> None:
    out = Collector(session)
    script = "tty; stty size; pwd; echo $TERM; read answer; echo got:$answer; exit 3"
    session.start(tmp_path, cols=100, rows=30, argv=["/bin/sh", "-c", script])
    assert session.running
    qtbot.waitUntil(lambda: "xterm-256color" in out.text, timeout=5000)
    with qtbot.waitSignal(session.exited, timeout=5000) as exited:
        session.write("привіт\r".encode())
    assert exited.args == [3]
    assert not session.running
    lines = out.text.splitlines()
    assert lines[0].startswith("/dev/")  # a terminal, not a pipe
    assert "30 100" in lines and str(tmp_path.resolve()) in out.text
    assert "got:привіт" in out.text


def test_resize_reaches_the_shell(qtbot, session: PtySession, tmp_path: Path) -> None:
    out = Collector(session)
    session.start(tmp_path, argv=["/bin/sh", "-c", "read x; stty size; read y"])
    qtbot.wait(200)
    session.resize(132, 43)
    session.write(b"\r")
    qtbot.waitUntil(lambda: "43 132" in out.text, timeout=5000)


def test_ctrl_c_interrupts_the_running_command(qtbot, session: PtySession, tmp_path: Path) -> None:
    out = Collector(session)
    session.start(tmp_path, argv=["/bin/sh", "-c", "sleep 30; echo status:$?; read x"])
    qtbot.wait(300)
    session.write(b"\x03")
    with qtbot.waitSignal(session.exited, timeout=5000):  # SIGINT stops the whole job
        pass
    assert "status:" not in out.text


def test_close_stops_background_jobs_too(qtbot, session: PtySession, tmp_path: Path) -> None:
    out = Collector(session)
    session.start(tmp_path, argv=["/bin/sh", "-c", "sleep 600 & echo started; wait"])
    qtbot.waitUntil(lambda: "started" in out.text, timeout=5000)
    backend = session._pty
    assert backend is not None
    tty = pty_session._controlling_tty(backend.pid)
    assert tty is not None and len(pty_session._tty_pids(tty)) == 2  # sh + sleep
    with qtbot.assertNotEmitted(session.exited, wait=300):
        session.close()  # a deliberate close is not reported as an exit
    assert pty_session._tty_pids(tty) == []
    assert not session.running
    session.close()  # closing twice is harmless


def test_start_errors(tmp_path: Path) -> None:
    session = PtySession()
    with pytest.raises(PtyError, match="немає"):
        session.start(tmp_path / "missing", argv=["/bin/sh"])
    with pytest.raises(PtyError, match="Не знайдено оболонку"):
        session.start(tmp_path, argv=["no-such-shell-xyz"])
    assert not session.running


def test_a_non_utf8_locale_gets_a_utf8_encoding() -> None:
    env = shell_environment({"LANG": "C"})
    assert env["LANG"] == "C" and env["LC_CTYPE"] == "en_US.UTF-8"
    assert "LC_CTYPE" not in shell_environment({"LANG": "C", "LC_ALL": "C"})  # an explicit choice
    kept = shell_environment({"LANG": "C", "LC_CTYPE": "uk_UA.UTF-8"})
    assert kept["LC_CTYPE"] == "uk_UA.UTF-8"
