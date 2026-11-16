#!/usr/bin/env python3
"""Open the app with a demo project and isolated settings (never touches your real settings).

.venv/bin/python scripts/preview_ui.py                    # interactive
.venv/bin/python scripts/preview_ui.py --screenshot out   # writes out-welcome.png, out-main.png, …
.venv/bin/python scripts/preview_ui.py --lang en --theme light
.venv/bin/python scripts/preview_ui.py --workspace /tmp/Projects   # a tidy path on screen
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer

from nightexpress.app import build_app
from nightexpress.services.settings_store import SettingsStore

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "reference" / "blinky_f103c6"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path, help="file prefix for screenshots")
    parser.add_argument("--theme", help="theme id (see resources/themes), default: graphite")
    parser.add_argument(
        "--lang", default="uk", help="language code (see resources/locales), default: uk"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="folder for the demo project (default: a new temporary one); it is replaced",
    )
    options = parser.parse_args()

    if options.workspace:
        workspace = options.workspace
        shutil.rmtree(workspace / "Blink", ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)
    else:
        workspace = Path(tempfile.mkdtemp(prefix="nightexpress-preview-"))
    project = workspace / "Blink"
    shutil.copytree(REFERENCE, project, ignore=shutil.ignore_patterns("target", "*.bin"))
    settings = SettingsStore(QSettings(str(workspace / "settings.ini"), QSettings.Format.IniFormat))
    settings.set_recent_projects([project])
    settings.set_project_opened_at(project, datetime.now())
    settings.set_projects_directory(workspace)
    settings.set_language(options.lang)
    if options.theme:
        settings.set_theme(options.theme)
    if options.screenshot:
        settings.set_open_last_project(False)  # the first picture is the start screen

    qapp, controller = build_app(sys.argv[:1], settings)
    window = controller.window
    controller.start()

    if options.screenshot:
        prefix = options.screenshot

        def editor() -> None:
            window.grab().save(f"{prefix}-welcome.png")
            controller.projects.open_project(project)
            controller.builds.build_finished.connect(
                lambda _r: QTimer.singleShot(1500, main_window)
            )
            QTimer.singleShot(1500, controller.builds.verify)

        def main_window() -> None:
            window.grab().save(f"{prefix}-main.png")
            controller.projects.show_settings()
            window.settings_dialog.tabs.setCurrentIndex(1)  # "Application": the theme list
            QTimer.singleShot(500, settings_page)

        def settings_page() -> None:
            window.settings_dialog.grab().save(f"{prefix}-settings.png")
            window.settings_dialog.reject()  # modal: quit() alone would not end its loop
            QTimer.singleShot(0, qapp.quit)

        QTimer.singleShot(3000, editor)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
