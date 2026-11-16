from __future__ import annotations

import os
from pathlib import Path

import pytest

from nightexpress.models.entities import BuildProfile, UploadMethod
from nightexpress.services.file_service import FileService, FileServiceError
from nightexpress.services.project_config_repository import (
    MissingMetadataError,
    NotCargoProjectError,
    ProjectConfigError,
    ProjectConfigRepository,
)

REFERENCE = Path(__file__).resolve().parents[2] / "reference" / "blinky_f103c6"

PLAIN_CARGO = """\
# my own project
[package]
name = "plain"   # keep this comment
version = "0.1.0"
edition = "2021"

[dependencies]
cortex-m = "0.7"
"""


# --- FileService ----------------------------------------------------------------------------


def test_read_text_roundtrip_and_atomic_write(tmp_path: Path) -> None:
    files = FileService()
    path = tmp_path / "main.rs"
    files.write_text_atomic(path, "fn main() {}\r\n// привіт\n")
    assert files.read_text(path) == "fn main() {}\r\n// привіт\n"  # line endings preserved
    assert [p.name for p in tmp_path.iterdir()] == ["main.rs"]  # no temp files left


def test_write_preserves_permissions(tmp_path: Path) -> None:
    path = tmp_path / "flash.sh"
    path.write_text("old")
    os.chmod(path, 0o755)
    FileService().write_text_atomic(path, "new")
    assert path.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda p: None, "не знайдено"),
        (lambda p: p.mkdir(), "тека"),
        (lambda p: p.write_bytes(b"\xff\xfe\x00binary"), "UTF-8"),
        (lambda p: p.write_bytes(b"x" * 11), "завеликий"),
    ],
)
def test_read_text_errors(tmp_path: Path, setup, message: str) -> None:
    path = tmp_path / "file"
    setup(path)
    with pytest.raises(FileServiceError, match=message):
        FileService().read_text(path, max_bytes=10)


def test_write_into_missing_directory_fails_cleanly(tmp_path: Path) -> None:
    with pytest.raises(FileServiceError, match="Не вдалося зберегти"):
        FileService().write_text_atomic(tmp_path / "missing" / "x.rs", "x")


# --- ProjectConfigRepository ----------------------------------------------------------------


def test_names_are_checked() -> None:
    files = FileService()
    files.check_name("led_driver.rs")
    for bad in ("", "..", "a/b.rs", "what?.rs"):
        with pytest.raises(FileServiceError):
            files.check_name(bad)


def test_create_directory_and_rename(tmp_path: Path) -> None:
    files = FileService()
    files.create_directory(tmp_path / "src" / "drivers")
    assert (tmp_path / "src" / "drivers").is_dir()
    with pytest.raises(FileServiceError, match="уже існує"):
        files.create_directory(tmp_path / "src")

    (tmp_path / "a.rs").write_text("a")
    (tmp_path / "b.rs").write_text("b")
    files.rename(tmp_path / "a.rs", tmp_path / "c.rs")
    assert (tmp_path / "c.rs").read_text() == "a"
    with pytest.raises(FileServiceError, match="уже існує"):
        files.rename(tmp_path / "c.rs", tmp_path / "b.rs")
    assert (tmp_path / "b.rs").read_text() == "b"


def test_list_files_skips_ignored_names(tmp_path: Path) -> None:
    for name in ("src/main.rs", "src/led.rs", "target/debug/app", ".git/HEAD", "Cargo.toml"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    files = FileService()
    listed = [
        p.relative_to(tmp_path).as_posix() for p in files.list_files(tmp_path, {"target", ".git"})
    ]
    assert listed == ["Cargo.toml", "src/led.rs", "src/main.rs"]
    assert len(files.list_files(tmp_path, limit=2)) == 2


def test_signature_changes_with_the_file(tmp_path: Path) -> None:
    files = FileService()
    path = tmp_path / "main.rs"
    assert files.signature(path) is None
    path.write_text("a")
    first = files.signature(path)
    path.write_text("longer")
    assert first is not None and files.signature(path) != first


def test_move_to_trash_reports_failure(tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtCore import QFile

    calls: list[str] = []
    monkeypatch.setattr(QFile, "moveToTrash", lambda path: calls.append(path) or False)
    files = FileService()
    files.move_to_trash(tmp_path / "missing.rs")  # nothing to do
    assert calls == []
    (tmp_path / "a.rs").write_text("a")
    with pytest.raises(FileServiceError, match="кошик"):
        files.move_to_trash(tmp_path / "a.rs")
    assert calls == [str(tmp_path / "a.rs")]


def test_read_reference_project() -> None:
    config = ProjectConfigRepository().read(REFERENCE)
    assert config.name == "blinky_f103c6"
    assert config.board_id == "stm32f103c6-minimal"
    assert config.upload_method is UploadMethod.PROBE_RS
    assert config.build_profile is BuildProfile.RELEASE
    assert config.path == REFERENCE


def test_not_a_cargo_project(tmp_path: Path) -> None:
    with pytest.raises(NotCargoProjectError, match=r"немає Cargo\.toml"):
        ProjectConfigRepository().read(tmp_path)


def test_workspace_without_package(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["a"]\n')
    with pytest.raises(NotCargoProjectError, match="Workspace"):
        ProjectConfigRepository().read(tmp_path)


def test_broken_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package\nname = ")
    with pytest.raises(ProjectConfigError, match="помилку"):
        ProjectConfigRepository().read(tmp_path)


def test_missing_metadata_then_write_keeps_comments(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(PLAIN_CARGO)
    repo = ProjectConfigRepository()
    with pytest.raises(MissingMetadataError) as info:
        repo.read(tmp_path)
    assert info.value.package_name == "plain"

    repo.write_metadata(tmp_path, "stm32f103c6-minimal", UploadMethod.SERIAL_BOOTLOADER)
    text = (tmp_path / "Cargo.toml").read_text()
    assert text.startswith(PLAIN_CARGO)  # everything the user wrote is untouched
    assert "[package.metadata.nightexpress]" in text
    config = repo.read(tmp_path)
    assert (config.board_id, config.upload_method) == (
        "stm32f103c6-minimal",
        UploadMethod.SERIAL_BOOTLOADER,
    )


def test_update_existing_metadata_in_place(tmp_path: Path) -> None:
    original = (REFERENCE / "Cargo.toml").read_text()
    (tmp_path / "Cargo.toml").write_text(original)
    repo = ProjectConfigRepository()
    repo.write_metadata(tmp_path, "stm32f103c6-minimal", UploadMethod.SERIAL_BOOTLOADER)
    updated = (tmp_path / "Cargo.toml").read_text()
    assert updated == original.replace(
        'upload_method = "probe-rs"', 'upload_method = "serial-bootloader"'
    )


@pytest.mark.parametrize(
    ("meta", "error", "message"),
    [
        ('schema = 99\nboard = "x"', ProjectConfigError, "новішою версією"),
        ('schema = 1\nupload_method = "probe-rs"', MissingMetadataError, "не вказано плату"),
        ('schema = 1\nboard = "x"\nupload_method = "jtag"', ProjectConfigError, "jtag"),
    ],
)
def test_invalid_metadata(tmp_path: Path, meta: str, error: type, message: str) -> None:
    (tmp_path / "Cargo.toml").write_text(
        f'[package]\nname = "p"\nversion = "0.1.0"\n\n[package.metadata.nightexpress]\n{meta}\n'
    )
    with pytest.raises(error, match=message):
        ProjectConfigRepository().read(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_write_through_a_symlink_keeps_the_link(tmp_path: Path) -> None:
    real = tmp_path / "shared" / "memory.x"
    real.parent.mkdir()
    real.write_text("old")
    link = tmp_path / "memory.x"
    link.symlink_to(real)
    FileService().write_text_atomic(link, "new")
    assert link.is_symlink() and real.read_text() == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["memory.x", "shared"]
    assert [p.name for p in real.parent.iterdir()] == ["memory.x"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_new_file_gets_the_usual_permissions(tmp_path: Path) -> None:
    path = tmp_path / "new.rs"
    previous = os.umask(0o022)
    try:
        FileService().write_text_atomic(path, "fn main() {}\n")
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o644


def test_list_files_can_ignore_names_only_at_the_root(tmp_path: Path) -> None:
    for name in ("src/target/mod.rs", "target/debug/app", "Cargo.toml"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    listed = FileService().list_files(tmp_path, root_ignored_names={"target"})
    assert [p.relative_to(tmp_path).as_posix() for p in listed] == [
        "Cargo.toml",
        "src/target/mod.rs",
    ]


def test_metadata_that_is_not_a_table(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "p"\nmetadata = "oops"\n')
    repository = ProjectConfigRepository()
    with pytest.raises(MissingMetadataError):
        repository.read(tmp_path)
    with pytest.raises(ProjectConfigError, match="має бути таблицею"):
        repository.write_metadata(tmp_path, "stm32f103c6-minimal", UploadMethod.PROBE_RS)
    assert 'metadata = "oops"' in (tmp_path / "Cargo.toml").read_text()


def test_projects_made_under_the_old_name_still_open(tmp_path: Path) -> None:
    from nightexpress.services.project_config_repository import ProjectConfigRepository

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "blink"\n\n'
        '[package.metadata.stmarryrust]\nschema = 1\nboard = "stm32f103c6-minimal"\n'
        'upload_method = "probe-rs"\n'
    )
    repository = ProjectConfigRepository(FileService())
    assert repository.read(tmp_path).board_id == "stm32f103c6-minimal"
    repository.write_metadata(tmp_path, "nucleo-f401re", UploadMethod.PROBE_RS)
    text = (tmp_path / "Cargo.toml").read_text()
    assert "[package.metadata.nightexpress]" in text and "stmarryrust" not in text
    assert repository.read(tmp_path).board_id == "nucleo-f401re"
