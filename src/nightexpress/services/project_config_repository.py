"""Project metadata in Cargo.toml: ``[package.metadata.nightexpress]``.

Cargo.toml is the single source of truth. It is edited with tomlkit
so the user's comments and formatting survive.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

from nightexpress.i18n import _
from nightexpress.models.entities import BuildProfile, ProjectConfig, UploadMethod
from nightexpress.services.file_service import FileService

METADATA_SCHEMA = 1
METADATA_KEY = "nightexpress"
# The application was called STMarryRust before: projects made then keep their metadata under
# this key. It is read as it is and becomes METADATA_KEY the next time the metadata is written.
LEGACY_METADATA_KEY = "stmarryrust"


class ProjectConfigError(Exception):
    """User-facing message in Ukrainian."""


class NotCargoProjectError(ProjectConfigError):
    pass


class MissingMetadataError(ProjectConfigError):
    """A valid Cargo package that has never been opened in NightExpress."""

    def __init__(self, message: str, package_name: str) -> None:
        super().__init__(message)
        self.package_name = package_name


class ProjectConfigRepository:
    def __init__(self, files: FileService | None = None) -> None:
        self._files = files or FileService()

    @staticmethod
    def cargo_toml(project_dir: Path) -> Path:
        return project_dir / "Cargo.toml"

    def _load(self, project_dir: Path) -> tomlkit.TOMLDocument:
        path = self.cargo_toml(project_dir)
        if not path.is_file():
            raise NotCargoProjectError(
                _("У теці {path} немає Cargo.toml — це не Rust-проєкт.").format(path=project_dir)
            )
        try:
            return tomlkit.parse(self._files.read_text(path))
        except TOMLKitError as exc:
            raise ProjectConfigError(
                _("Cargo.toml містить помилку: {error}").format(error=exc)
            ) from exc

    def read(self, project_dir: Path) -> ProjectConfig:
        doc = self._load(project_dir)
        package = doc.get("package")
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise NotCargoProjectError(
                _(
                    "Cargo.toml не описує пакет ([package] з полем name). "
                    "Workspace-проєкти поки не підтримуються."
                )
            )
        name = str(package["name"])
        metadata = package.get("metadata")
        meta = None
        if isinstance(metadata, dict):
            meta = metadata.get(METADATA_KEY, metadata.get(LEGACY_METADATA_KEY))
        if not isinstance(meta, dict):
            raise MissingMetadataError(
                _("Проєкт «{name}» ще не налаштовано для NightExpress: виберіть плату.").format(
                    name=name
                ),
                name,
            )
        schema = meta.get("schema", METADATA_SCHEMA)
        if not isinstance(schema, int) or schema > METADATA_SCHEMA:
            raise ProjectConfigError(
                _(
                    "Проєкт «{name}» створено новішою версією NightExpress (schema {schema})."
                ).format(name=name, schema=schema)
            )
        board = meta.get("board")
        if not isinstance(board, str) or not board:
            raise MissingMetadataError(
                _("У проєкті «{name}» не вказано плату.").format(name=name), name
            )
        raw_method = meta.get("upload_method", UploadMethod.PROBE_RS.value)
        try:
            method = UploadMethod(str(raw_method))
        except ValueError:
            raise ProjectConfigError(
                _("Невідомий спосіб прошивання «{method}» у Cargo.toml.").format(method=raw_method)
            ) from None
        return ProjectConfig(
            path=project_dir,
            name=name,
            board_id=board,
            upload_method=method,
            build_profile=BuildProfile.RELEASE,
        )

    def write_metadata(self, project_dir: Path, board_id: str, upload_method: UploadMethod) -> None:
        self._load(project_dir)  # the usual "not a Cargo project" / "broken TOML" errors
        original = self._files.read_text(self.cargo_toml(project_dir))
        text = self.with_metadata(original, board_id, upload_method)
        self._files.write_text_atomic(self.cargo_toml(project_dir), text)

    def with_metadata(self, cargo_toml: str, board_id: str, upload_method: UploadMethod) -> str:
        """The text of a Cargo.toml with our metadata set; everything else stays as written."""
        try:
            doc = tomlkit.parse(cargo_toml)
        except TOMLKitError as exc:
            raise ProjectConfigError(
                _("Cargo.toml містить помилку: {error}").format(error=exc)
            ) from exc
        package = doc.get("package")
        if not isinstance(package, dict):
            raise NotCargoProjectError(_("Cargo.toml не містить розділу [package]."))
        values = {
            "schema": METADATA_SCHEMA,
            "board": board_id,
            "upload_method": upload_method.value,
        }
        metadata = package.get("metadata")
        if metadata is None:
            # Append a new table at the end instead of letting tomlkit insert it between the
            # user's sections; the user's text stays byte-for-byte unchanged.
            table = tomlkit.table()
            for key, value in values.items():
                table[key] = value
            original = tomlkit.dumps(doc)
            separator = "" if original.endswith("\n") else "\n"
            text = original + separator + _dotted_table_text(table)
        elif not isinstance(metadata, dict):
            raise ProjectConfigError(_("У Cargo.toml поле package.metadata має бути таблицею."))
        else:
            current = metadata.get(METADATA_KEY)
            if not isinstance(current, dict) and isinstance(
                metadata.get(LEGACY_METADATA_KEY), dict
            ):
                current = metadata.pop(LEGACY_METADATA_KEY)  # the old name, written anew
                metadata[METADATA_KEY] = current
            if not isinstance(current, dict):
                current = tomlkit.table()
                metadata[METADATA_KEY] = current
            for key, value in values.items():
                current[key] = value
            text = tomlkit.dumps(doc)
        try:
            tomlkit.parse(text)
        except TOMLKitError as exc:  # pragma: no cover - defensive
            raise ProjectConfigError(
                _("Не вдалося оновити Cargo.toml: {error}").format(error=exc)
            ) from exc
        return text


def _dotted_table_text(table: tomlkit.items.Table) -> str:
    body = tomlkit.dumps(table)
    return f"\n[package.metadata.{METADATA_KEY}]\n{body}"
