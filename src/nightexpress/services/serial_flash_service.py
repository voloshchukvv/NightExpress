"""Run the AN3155 flasher in a worker thread and report back through Qt signals."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from nightexpress.i18n import _
from nightexpress.services.elf_tools import BinaryImage
from nightexpress.services.serial_bootloader import (
    BootloaderError,
    BootloaderErrorKind,
    Transport,
    flash_image,
    open_serial,
)

log = logging.getLogger(__name__)

SHUTDOWN_WAIT_S = 3.0  # how long the application exit waits for the flasher to let go


class _Emitter(QObject):
    log_line = Signal(str)
    progress = Signal(object)  # UploadProgress
    finished = Signal(object)  # BootloaderError | None


class _FlashTask(QRunnable):
    def __init__(
        self,
        emitter: _Emitter,
        open_transport: Callable[[str], Transport],
        device: str,
        image: BinaryImage,
        pid: int,
        flash_bytes: int,
        cancel: threading.Event,
        done: threading.Event,
    ) -> None:
        super().__init__()
        self._emitter = emitter
        self._open = open_transport
        self._args = (device, image, pid, flash_bytes)
        self._cancel = cancel
        self._done = done

    def _emit(self, signal_name: str, value: object) -> None:
        # The service (and its emitter) may be gone if the window was closed meanwhile.
        with contextlib.suppress(RuntimeError):
            getattr(self._emitter, signal_name).emit(value)

    def run(self) -> None:
        device, image, pid, flash_bytes = self._args
        error: BootloaderError | None = None
        transport: Transport | None = None
        try:
            transport = self._open(device)
            flash_image(
                transport,
                image,
                pid,
                flash_bytes,
                progress=lambda p: self._emit("progress", p),
                log=lambda line: self._emit("log_line", line),
                cancel=self._cancel,
            )
        except BootloaderError as exc:
            error = exc
        except (OSError, ValueError) as exc:  # e.g. the adapter was unplugged mid-transfer
            error = BootloaderError(BootloaderErrorKind.PORT_UNAVAILABLE, str(exc))
        except Exception as exc:  # a bug here must not leave the upload "running" forever
            log.exception("serial upload crashed")
            error = BootloaderError(
                BootloaderErrorKind.NO_RESPONSE, _("Внутрішня помилка: {error}").format(error=exc)
            )
        finally:
            if transport is not None:
                with contextlib.suppress(OSError):
                    transport.close()
            self._done.set()
        self._emit("finished", error)


class SerialFlashService(QObject):
    log_line = Signal(str)
    progress = Signal(object)  # UploadProgress
    finished = Signal(object)  # BootloaderError | None (success)

    def __init__(
        self,
        open_transport: Callable[[str], Transport] = open_serial,
        pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._open = open_transport
        self._pool = pool or QThreadPool.globalInstance()
        self._emitter = _Emitter(self)
        self._emitter.log_line.connect(self.log_line)
        self._emitter.progress.connect(self.progress)
        self._emitter.finished.connect(self._on_finished)
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def flash(self, device: str, image: BinaryImage, pid: int, flash_bytes: int) -> None:
        if self._busy:
            raise RuntimeError("a serial upload is already running")
        self._busy = True
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._pool.start(
            _FlashTask(
                self._emitter, self._open, device, image, pid, flash_bytes, self._cancel, self._done
            )
        )

    def cancel(self) -> None:
        self._cancel.set()

    def shutdown(self) -> None:
        """Application exit: stop writing and give the worker a moment to close the port."""
        if not self._busy:
            return
        self._cancel.set()
        if not self._done.wait(SHUTDOWN_WAIT_S):
            log.warning("the serial upload did not stop in time")

    def _on_finished(self, error: BootloaderError | None) -> None:
        self._busy = False
        self.finished.emit(error)
