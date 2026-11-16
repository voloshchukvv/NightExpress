"""Persistent user settings (QSettings in the OS user config location).

Machine-specific values live here, not in the project's Cargo.toml: the selected port of a
project, user labels for USB adapters, serial monitor speed, theme and window geometry.
Lists are stored as JSON strings so every QSettings backend (plist, registry, INI) round-trips
them identically.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings

from nightexpress import APP_NAME
from nightexpress.models.entities import DEFAULT_BAUD_RATE, BuildProfile
from nightexpress.models.entities.font import DEFAULT_FONT_SIZE, FontArea, clamp_font_size
from nightexpress.services.language_catalog import SYSTEM_LANGUAGE
from nightexpress.services.theme_catalog import DEFAULT_THEME_ID

# Values written by versions that had one built-in theme -> today's theme ids.
LEGACY_THEMES = {"dark": DEFAULT_THEME_ID}


def _project_key(project: Path) -> str:
    """QSettings treats '/' as a group separator, so project paths are hashed."""
    digest = hashlib.sha1(str(project.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"projects/{digest}"


def _adapter_key(stable_id: str) -> str:
    digest = hashlib.sha1(stable_id.encode("utf-8")).hexdigest()[:16]
    return f"adapters/{digest}"


# The application was called STMarryRust before; its settings are carried over once.
LEGACY_APP_NAME = "STMarryRust"


def carry_over(old: QSettings, new: QSettings) -> bool:
    """Copy every setting of ``old`` into an empty ``new``. Returns whether it copied."""
    # On macOS the settings also show the keys of the whole system (AppleLocale, ...): only
    # the application's own keys count here.
    fallbacks = old.fallbacksEnabled(), new.fallbacksEnabled()
    old.setFallbacksEnabled(False)
    new.setFallbacksEnabled(False)
    try:
        keys = old.allKeys()
        if new.allKeys() or not keys:
            return False
        for key in keys:
            new.setValue(key, old.value(key))
        new.sync()
        return True
    finally:
        old.setFallbacksEnabled(fallbacks[0])
        new.setFallbacksEnabled(fallbacks[1])


class SettingsStore:
    def __init__(self, settings: QSettings | None = None) -> None:
        if settings is None:
            settings = QSettings(APP_NAME, APP_NAME)
            carry_over(QSettings(LEGACY_APP_NAME, LEGACY_APP_NAME), settings)
        self._s = settings

    def sync(self) -> None:
        self._s.sync()

    # --- generic helpers -------------------------------------------------------------------

    def _get_json(self, key: str, default: Any) -> Any:
        raw = self._s.value(key)
        if raw is None:
            return default
        try:
            return json.loads(str(raw))
        except json.JSONDecodeError:
            return default

    def _set_json(self, key: str, value: Any) -> None:
        self._s.setValue(key, json.dumps(value, ensure_ascii=False))

    def _get_str(self, key: str) -> str | None:
        raw = self._s.value(key)
        return None if raw in (None, "") else str(raw)

    def _get_flag(self, key: str, default: bool) -> bool:
        raw = self._s.value(key)
        return default if raw is None else str(raw).lower() == "true"

    def _set_flag(self, key: str, value: bool) -> None:
        self._s.setValue(key, "true" if value else "false")

    def _get_dict(self, key: str) -> dict[str, Any]:
        raw = self._get_str(key)
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _set_optional(self, key: str, value: str | None) -> None:
        if value is None:
            self._s.remove(key)
        else:
            self._s.setValue(key, value)

    # --- recent projects -------------------------------------------------------------------

    def recent_projects(self) -> list[Path]:
        value = self._get_json("recent_projects", [])
        return [Path(p) for p in value if isinstance(p, str)] if isinstance(value, list) else []

    def set_recent_projects(self, paths: list[Path]) -> None:
        self._set_json("recent_projects", [str(p) for p in paths])

    def projects_directory(self) -> Path:
        """Where the new project wizard proposes to create projects."""
        value = self._get_str("projects_directory")
        return Path(value) if value else Path.home() / APP_NAME

    def set_projects_directory(self, path: Path) -> None:
        self._s.setValue("projects_directory", str(path))

    # --- per-project values ----------------------------------------------------------------

    def project_upload_target(self, project: Path) -> str | None:
        """Stable id of the probe / serial adapter last used for this project."""
        return self._get_str(f"{_project_key(project)}/upload_target")

    def set_project_upload_target(self, project: Path, stable_id: str | None) -> None:
        key = _project_key(project)
        self._s.setValue(f"{key}/path", str(project.resolve()))  # for humans reading the file
        self._set_optional(f"{key}/upload_target", stable_id)

    def project_device_detached(self, project: Path) -> bool:
        """The user chose "Disconnect": no device is picked for this project automatically."""
        return self._get_flag(f"{_project_key(project)}/device_detached", False)

    def set_project_device_detached(self, project: Path, detached: bool) -> None:
        self._set_flag(f"{_project_key(project)}/device_detached", detached)

    def project_opened_at(self, project: Path) -> datetime | None:
        raw = self._get_str(f"{_project_key(project)}/opened_at")
        try:
            return datetime.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    def set_project_opened_at(self, project: Path, moment: datetime) -> None:
        self._s.setValue(f"{_project_key(project)}/opened_at", moment.isoformat(timespec="seconds"))

    def project_build_profile(self, project: Path) -> BuildProfile:
        raw = self._get_str(f"{_project_key(project)}/build_profile")
        try:
            return BuildProfile(raw) if raw else BuildProfile.RELEASE
        except ValueError:
            return BuildProfile.RELEASE

    def set_project_build_profile(self, project: Path, profile: BuildProfile) -> None:
        self._s.setValue(f"{_project_key(project)}/build_profile", profile.value)

    def project_monitor_port(self, project: Path) -> str | None:
        return self._get_str(f"{_project_key(project)}/monitor_port")

    def set_project_monitor_port(self, project: Path, stable_id: str | None) -> None:
        self._set_optional(f"{_project_key(project)}/monitor_port", stable_id)

    def project_monitor_baud_rate(self, project: Path) -> int | None:
        raw = self._get_str(f"{_project_key(project)}/monitor_baud_rate")
        return int(raw) if raw and raw.isdigit() and int(raw) > 0 else None

    def set_project_monitor_baud_rate(self, project: Path, baud_rate: int) -> None:
        self._set_optional(f"{_project_key(project)}/monitor_baud_rate", str(baud_rate))

    def project_monitor_line_ending(self, project: Path) -> str | None:
        return self._get_str(f"{_project_key(project)}/monitor_line_ending")

    def set_project_monitor_line_ending(self, project: Path, ending: str) -> None:
        self._set_optional(f"{_project_key(project)}/monitor_line_ending", ending)

    # --- adapters --------------------------------------------------------------------------

    def adapter_label(self, stable_id: str) -> str | None:
        """User-given name such as "to STM32"."""
        return self._get_str(f"{_adapter_key(stable_id)}/label")

    def set_adapter_label(self, stable_id: str, label: str | None) -> None:
        key = _adapter_key(stable_id)
        cleaned = label.strip() if label else None
        self._s.setValue(f"{key}/id", stable_id)
        self._set_optional(f"{key}/label", cleaned or None)

    def boot_dialog_hidden(self) -> bool:
        return str(self._s.value("upload/boot_dialog_hidden", "false")).lower() == "true"

    def set_boot_dialog_hidden(self, hidden: bool) -> None:
        self._s.setValue("upload/boot_dialog_hidden", "true" if hidden else "false")

    # --- project tree ----------------------------------------------------------------------

    def show_service_files(self) -> bool:
        return str(self._s.value("tree/show_service_files", "true")).lower() == "true"

    def set_show_service_files(self, show: bool) -> None:
        self._s.setValue("tree/show_service_files", "true" if show else "false")

    def show_build_dir(self) -> bool:
        return str(self._s.value("tree/show_build_dir", "false")).lower() == "true"

    def set_show_build_dir(self, show: bool) -> None:
        self._s.setValue("tree/show_build_dir", "true" if show else "false")

    # --- first run -------------------------------------------------------------------------

    def first_run_tips_shown(self) -> bool:
        return str(self._s.value("onboarding/first_run_tips_shown", "false")).lower() == "true"

    def set_first_run_tips_shown(self, shown: bool) -> None:
        self._s.setValue("onboarding/first_run_tips_shown", "true" if shown else "false")

    # --- serial monitor --------------------------------------------------------------------

    def monitor_baud_rate(self) -> int:
        raw = self._s.value("serial_monitor/baud_rate")
        try:
            value = int(raw) if raw is not None else DEFAULT_BAUD_RATE
        except (TypeError, ValueError):
            return DEFAULT_BAUD_RATE
        return value if value > 0 else DEFAULT_BAUD_RATE

    def set_monitor_baud_rate(self, baud_rate: int) -> None:
        if baud_rate <= 0:
            raise ValueError(f"baud rate must be positive, got {baud_rate}")
        self._s.setValue("serial_monitor/baud_rate", baud_rate)

    # --- appearance ------------------------------------------------------------------------

    def font_size(self, area: FontArea = FontArea.EDITOR) -> int:
        """The font size of ``area``. An area that never got a size of its own (settings of
        an older version, or nothing saved yet) has the editor's."""
        raw = self._s.value(f"{area.value}/font_size")
        try:
            return clamp_font_size(int(raw))
        except (TypeError, ValueError):
            pass  # nothing saved, or not a number
        return DEFAULT_FONT_SIZE if area is FontArea.EDITOR else self.font_size(FontArea.EDITOR)

    def set_font_size(self, area: FontArea, size: int) -> None:
        self._s.setValue(f"{area.value}/font_size", clamp_font_size(size))

    def single_font_size(self) -> bool:
        """One size for the editor, the terminal, the UART monitor and the logs."""
        return self._get_flag("appearance/single_font_size", True)

    def set_single_font_size(self, value: bool) -> None:
        self._set_flag("appearance/single_font_size", value)

    def theme(self) -> str:
        """The chosen theme: a theme id or "system". Whether such a theme still exists is for
        the theme catalog to decide (an unknown one falls back to the default theme)."""
        value = self._get_str("appearance/theme") or DEFAULT_THEME_ID
        return LEGACY_THEMES.get(value, value)

    def set_theme(self, theme: str) -> None:
        if not theme.strip():
            raise ValueError("the theme id is empty")
        self._s.setValue("appearance/theme", theme)

    def language(self) -> str:
        """The chosen interface language: a language code or "system". Whether such a language
        still exists is for the language catalog to decide (an unknown one means "system")."""
        return self._get_str("appearance/language") or SYSTEM_LANGUAGE

    def set_language(self, language: str) -> None:
        if not language.strip():
            raise ValueError("the language code is empty")
        self._s.setValue("appearance/language", language)

    def window_geometry(self) -> bytes | None:
        raw = self._s.value("window/geometry")
        if isinstance(raw, QByteArray) and not raw.isEmpty():
            return bytes(raw.data())
        return None

    def set_window_geometry(self, geometry: bytes) -> None:
        self._s.setValue("window/geometry", QByteArray(geometry))

    def window_state(self) -> bytes | None:
        raw = self._s.value("window/state")
        if isinstance(raw, QByteArray) and not raw.isEmpty():
            return bytes(raw.data())
        return None

    def set_window_state(self, state: bytes) -> None:
        self._s.setValue("window/state", QByteArray(state))

    def window_layout(self) -> dict[str, Any]:
        """Panel sizes, hidden panels, the bottom tab (see MainWindow.layout_state)."""
        return self._get_dict("window/layout")

    def set_window_layout(self, layout: dict[str, Any]) -> None:
        self._s.setValue("window/layout", json.dumps(layout))

    # --- per-project editor session ---------------------------------------------------------

    def project_session(self, project: Path) -> dict[str, Any]:
        """{"open": [relative paths], "active": path, "cursors": {path: [line, column]}}."""
        return self._get_dict(f"{_project_key(project)}/session")

    def set_project_session(self, project: Path, session: dict[str, Any]) -> None:
        self._s.setValue(f"{_project_key(project)}/session", json.dumps(session))

    # --- behaviour ----------------------------------------------------------------------------

    def open_last_project(self) -> bool:
        return self._get_flag("behaviour/open_last_project", True)

    def set_open_last_project(self, value: bool) -> None:
        self._set_flag("behaviour/open_last_project", value)

    def save_before_build(self) -> bool:
        return self._get_flag("behaviour/save_before_build", True)

    def set_save_before_build(self, value: bool) -> None:
        self._set_flag("behaviour/save_before_build", value)

    def format_on_save(self) -> bool:
        return self._get_flag("editor/format_on_save", False)

    def set_format_on_save(self, value: bool) -> None:
        self._set_flag("editor/format_on_save", value)

    def bracket_pair_guides(self) -> bool:
        return self._get_flag("editor/bracket_pair_guides", False)

    def set_bracket_pair_guides(self, value: bool) -> None:
        self._set_flag("editor/bracket_pair_guides", value)

    def minimap(self) -> bool:
        return self._get_flag("editor/minimap", True)

    def set_minimap(self, value: bool) -> None:
        self._set_flag("editor/minimap", value)
