"""Reading files for the editor and saving them safely."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from collections.abc import Collection
from pathlib import Path

from PySide6.QtCore import QFile

from nightexpress.i18n import _

MAX_EDITOR_FILE_BYTES = 2 * 1024 * 1024
MAX_LISTED_FILES = 5000
FORBIDDEN_NAME_CHARS = '/\\:*?"<>|'

FileSignature = tuple[int, int]  # (modification time in ns, size): cheap "did it change?"


class FileServiceError(Exception):
    """User-facing message in Ukrainian."""


class FileService:
    def read_text(self, path: Path, max_bytes: int = MAX_EDITOR_FILE_BYTES) -> str:
        if path.is_dir():
            raise FileServiceError(_("{path} — це тека, а не файл.").format(path=path))
        try:
            size = path.stat().st_size
            if size > max_bytes:
                raise FileServiceError(
                    _("Файл {name} завеликий для редактора ({size_kb} KB).").format(
                        name=path.name, size_kb=size // 1024
                    )
                )
            data = path.read_bytes()
        except FileNotFoundError:
            raise FileServiceError(_("Файл {path} не знайдено.").format(path=path)) from None
        except OSError as exc:
            raise FileServiceError(
                _("Не вдалося прочитати {path}: {reason}").format(path=path, reason=exc.strerror)
            ) from exc
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise FileServiceError(
                _("Файл {name} не є текстом у кодуванні UTF-8 і не може бути відкритий.").format(
                    name=path.name
                )
            ) from None

    def write_text_atomic(self, path: Path, text: str) -> None:
        """Write via a temporary file + rename, so a crash never leaves a half-written file."""
        if path.is_symlink():
            # Replace the file the link points to; renaming over the link would turn the link
            # itself into a regular file and leave the real one untouched.
            try:
                path = path.resolve(strict=False)
            except (OSError, RuntimeError) as exc:  # e.g. a link loop
                raise FileServiceError(
                    _("Не вдалося зберегти {path}: {error}").format(path=path, error=exc)
                ) from exc
        mode = None
        with contextlib.suppress(FileNotFoundError):
            mode = stat.S_IMODE(path.stat().st_mode)
        try:
            fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        except OSError as exc:
            raise FileServiceError(
                _("Не вдалося зберегти {path}: {reason}").format(path=path, reason=exc.strerror)
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            # mkstemp makes the file private (0600): keep the mode of the file being replaced,
            # and give a new file the usual one.
            os.chmod(temp, mode if mode is not None else 0o666 & ~_umask())
            os.replace(temp, path)
        except OSError as exc:
            Path(temp).unlink(missing_ok=True)
            raise FileServiceError(
                _("Не вдалося зберегти {path}: {reason}").format(path=path, reason=exc.strerror)
            ) from exc

    # --- project tree operations -----------------------------------------------------------

    @staticmethod
    def check_name(name: str) -> None:
        """Raise unless ``name`` is usable as a single file or folder name everywhere."""
        if not name or name in (".", ".."):
            raise FileServiceError(_("Назва не може бути порожньою."))
        bad = sorted({c for c in name if c in FORBIDDEN_NAME_CHARS or ord(c) < 32})
        if bad:
            raise FileServiceError(_("Назва не може містити: {bad}").format(bad=" ".join(bad)))

    def create_directory(self, path: Path) -> None:
        if path.exists():
            raise FileServiceError(_("{name} уже існує.").format(name=path.name))
        try:
            path.mkdir(parents=True)
        except OSError as exc:
            raise FileServiceError(
                _("Не вдалося створити теку {path}: {reason}").format(
                    path=path, reason=exc.strerror
                )
            ) from exc

    def rename(self, source: Path, target: Path) -> None:
        # A case-only rename ("Main.rs" -> "main.rs") finds itself on case-insensitive disks.
        if target.exists() and not _same_file(source, target):
            raise FileServiceError(_("{name} уже існує.").format(name=target.name))
        try:
            os.rename(source, target)
        except OSError as exc:
            raise FileServiceError(
                _("Не вдалося перейменувати {name}: {reason}").format(
                    name=source.name, reason=exc.strerror
                )
            ) from exc

    def move_to_trash(self, path: Path) -> None:
        if not path.exists():
            return
        if not QFile.moveToTrash(str(path)):
            raise FileServiceError(
                _("Не вдалося перемістити {name} у кошик.").format(name=path.name)
            )

    def list_files(
        self,
        root: Path,
        ignored_names: Collection[str] = (),
        limit: int = MAX_LISTED_FILES,
        root_ignored_names: Collection[str] = (),
    ) -> list[Path]:
        """Files below ``root`` (sorted), skipping folders and files named in ``ignored_names``
        at any depth and those in ``root_ignored_names`` directly in ``root``."""
        found: list[Path] = []
        for directory, folders, files in os.walk(root):
            skipped = (
                {*ignored_names, *root_ignored_names} if Path(directory) == root else ignored_names
            )
            folders[:] = sorted(f for f in folders if f not in skipped)
            for name in sorted(files):
                if name not in skipped:
                    found.append(Path(directory) / name)
                    if len(found) >= limit:
                        return found
        return found

    def signature(self, path: Path) -> FileSignature | None:
        """None when the file is missing or unreadable."""
        try:
            info = path.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size


def _umask() -> int:
    current = os.umask(0)  # the only way to read it is to set it
    os.umask(current)
    return current


def _same_file(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False
