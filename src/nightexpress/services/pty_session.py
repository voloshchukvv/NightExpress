"""An interactive shell in a pseudo-terminal, for the "Terminal" tab.

A pseudo-terminal (not a pipe) is what makes the shell behave as in a real terminal: prompts,
colours, progress bars, vim, Ctrl+C and job control all depend on it.

- macOS / Linux: ``os.forkpty`` — the child becomes a session leader with the PTY as its
  controlling terminal, then execs the user's login shell. The child runs no Python beyond
  ``chdir`` + ``execve`` (everything is prepared before the fork), so forking from a process
  with threads is safe in practice.
- Windows: ConPTY through ``pywinpty`` (optional dependency, imported lazily).

Output is read in a background thread and delivered through the ``output`` signal (queued to
the receiver's thread by Qt).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from nightexpress.i18n import _

READ_CHUNK = 64 * 1024
CLOSE_TIMEOUT_S = 1.0


class PtyError(Exception):
    """The shell cannot be started (user-facing message in Ukrainian)."""


def default_shell(
    platform: str = sys.platform, environ: Mapping[str, str] = os.environ
) -> list[str]:
    """The user's shell as a login shell, so their profile (PATH with cargo) is loaded."""
    if platform.startswith("win"):
        return ["powershell.exe", "-NoLogo"]
    shell = environ.get("SHELL") or ("/bin/zsh" if platform == "darwin" else "/bin/bash")
    return [shell, "-l"]


def shell_environment(
    environ: Mapping[str, str] = os.environ, search_path: str | None = None
) -> dict[str, str]:
    env = dict(environ)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["TERM_PROGRAM"] = "NightExpress"
    if search_path:
        env["PATH"] = search_path  # an app started from Finder has a minimal PATH
    # Without a UTF-8 locale (common for GUI apps on macOS) shells mangle non-ASCII input.
    locale = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
    if not locale.upper().endswith(("UTF-8", "UTF8")):
        if not env.get("LANG"):
            env["LANG"] = "en_US.UTF-8"
        elif not env.get("LC_ALL"):
            env["LC_CTYPE"] = "en_US.UTF-8"  # keep the language, fix the encoding only
    return env


def _run_quietly(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _controlling_tty(pid: int) -> str | None:
    """``ttys002`` (macOS) / ``pts/3`` (Linux): the terminal every process of the session has."""
    result = _run_quietly(["/bin/ps", "-o", "tty=", "-p", str(pid)])
    name = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    return name if name and name not in ("?", "??") else None


def _tty_pids(tty: str) -> list[int]:
    """Processes attached to the terminal (``pgrep -t`` does not see PTYs on macOS)."""
    result = _run_quietly(["/bin/ps", "-t", tty, "-o", "pid="])
    if result is None or result.returncode != 0:
        return []
    return [int(word) for word in result.stdout.split() if word.isdigit()]


class _UnixPty:
    def __init__(self, argv: Sequence[str], cwd: Path, env: dict[str, str], cols: int, rows: int):
        import pty

        program = argv[0]
        if not os.path.isabs(program):
            found = shutil.which(program, path=env.get("PATH"))
            if found is None:
                raise PtyError(_("Не знайдено оболонку «{program}».").format(program=program))
            program = found
        if not os.access(program, os.X_OK):
            raise PtyError(_("Не вдається запустити оболонку «{program}».").format(program=program))
        args = [os.fsencode(a) for a in argv]
        env_bytes = {os.fsencode(k): os.fsencode(v) for k, v in env.items()}
        directory = os.fsencode(cwd)
        program_bytes = os.fsencode(program)
        with warnings.catch_warnings():
            # "use of forkpty() may lead to deadlocks in the child": the child only execs.
            warnings.simplefilter("ignore", DeprecationWarning)
            pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                os.chdir(directory)
                os.execve(program_bytes, args, env_bytes)
            finally:
                os._exit(127)
        self.pid = pid
        self.fd = fd
        self.resize(cols, rows)

    def read(self) -> bytes:
        try:
            return os.read(self.fd, READ_CHUNK)
        except OSError:  # EIO: the shell exited and the slave side is closed
            return b""

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self.fd, view)
            view = view[written:]

    def resize(self, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        # The kernel sends SIGWINCH to the foreground job, so vim/less redraw.
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def wait(self) -> int:
        _pid, status = os.waitpid(self.pid, 0)
        return os.waitstatus_to_exitcode(status)

    def poll(self) -> int | None:
        pid, status = os.waitpid(self.pid, os.WNOHANG)
        return os.waitstatus_to_exitcode(status) if pid else None

    def terminate(self) -> None:
        """Stop the whole session: the shell and everything started from it.

        A hangup alone is not enough: background jobs live in their own process groups and
        shells do not reliably pass the hangup on, which would leave e.g. a serial monitor
        holding the port. Every process attached to this terminal is signalled.
        """
        tty = _controlling_tty(self.pid)
        for sig in (signal.SIGHUP, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.pid, sig)
            for pid in _tty_pids(tty) if tty is not None else ():
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, sig)
            deadline = time.monotonic() + CLOSE_TIMEOUT_S / 2
            while time.monotonic() < deadline:
                with contextlib.suppress(ChildProcessError):
                    self.poll()  # reap the shell, or it stays a zombie on the terminal
                if not (_tty_pids(tty) if tty is not None else self._alive()):
                    return
                time.sleep(0.05)

    def _alive(self) -> bool:
        try:
            return self.poll() is None
        except ChildProcessError:
            return False

    def close_fd(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self.fd)


class _WindowsPty:
    def __init__(self, argv: Sequence[str], cwd: Path, env: dict[str, str], cols: int, rows: int):
        try:
            from winpty import PtyProcess  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PtyError(_("Для термінала у Windows потрібен пакет pywinpty.")) from exc
        self._process: Any = PtyProcess.spawn(
            list(argv), cwd=str(cwd), env=env, dimensions=(rows, cols)
        )

    def read(self) -> bytes:
        try:
            return self._process.read(READ_CHUNK).encode("utf-8")
        except EOFError:
            return b""

    def write(self, data: bytes) -> None:
        self._process.write(data.decode("utf-8", errors="replace"))

    def resize(self, cols: int, rows: int) -> None:
        self._process.setwinsize(rows, cols)

    def wait(self) -> int:
        self._process.wait()
        return int(self._process.exitstatus or 0)

    def terminate(self) -> None:
        with contextlib.suppress(Exception):
            self._process.terminate(force=True)

    def close_fd(self) -> None:
        with contextlib.suppress(Exception):
            self._process.close()


class PtySession(QObject):
    """One shell. ``start`` it, feed ``write``, get ``output``; ``exited`` when it is gone."""

    output = Signal(bytes)
    exited = Signal(int)  # exit code; not emitted for close()
    _ended = Signal(object, int)  # (backend, exit code) from the reader thread

    def __init__(self, parent: QObject | None = None, platform: str = sys.platform) -> None:
        super().__init__(parent)
        self._platform = platform
        self._pty: _UnixPty | _WindowsPty | None = None
        self._reader: threading.Thread | None = None
        self._ended.connect(self._on_ended)  # queued: the session lives in the GUI thread

    @property
    def running(self) -> bool:
        return self._pty is not None

    def start(
        self,
        cwd: Path,
        cols: int = 80,
        rows: int = 24,
        argv: Sequence[str] | None = None,
        search_path: str | None = None,
    ) -> None:
        if self._pty is not None:
            raise RuntimeError("the session is already running")
        if not cwd.is_dir():
            raise PtyError(_("Теки {path} немає.").format(path=cwd))
        command = list(argv) if argv else default_shell(self._platform)
        env = shell_environment(search_path=search_path)
        backend = _WindowsPty if self._platform.startswith("win") else _UnixPty
        try:
            self._pty = backend(command, cwd, env, max(cols, 2), max(rows, 1))
        except OSError as exc:
            raise PtyError(_("Не вдалося запустити термінал: {error}").format(error=exc)) from exc
        self._reader = threading.Thread(
            target=self._read_loop, args=(self._pty,), name="pty-reader", daemon=True
        )
        self._reader.start()

    def write(self, data: bytes) -> None:
        if self._pty is not None and data:
            with contextlib.suppress(OSError):
                self._pty.write(data)

    def resize(self, cols: int, rows: int) -> None:
        if self._pty is not None and cols > 0 and rows > 0:
            with contextlib.suppress(OSError):
                self._pty.resize(cols, rows)

    def close(self) -> None:
        """Stop the shell and everything started from it; blocks for at most ~2 s."""
        backend = self._pty
        if backend is None:
            return
        self._pty = None  # from now on the reader's "ended" is ignored
        backend.terminate()
        if self._reader is not None:
            self._reader.join(CLOSE_TIMEOUT_S)
        backend.close_fd()
        self._reader = None

    def _read_loop(self, backend: _UnixPty | _WindowsPty) -> None:
        while True:
            data = backend.read()
            if not data:
                break
            with contextlib.suppress(RuntimeError):  # the receiver may be gone at exit
                self.output.emit(data)
        try:
            code = backend.wait()
        except ChildProcessError:  # already reaped by close()
            code = 0
        with contextlib.suppress(RuntimeError):
            self._ended.emit(backend, code)

    def _on_ended(self, backend: object, code: int) -> None:
        if backend is not self._pty:  # closed on purpose, or an older session
            return
        self._pty.close_fd()
        self._pty = None
        self._reader = None
        self.exited.emit(code)
