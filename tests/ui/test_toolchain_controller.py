from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QGuiApplication

from nightexpress.controllers.toolchain_controller import ToolchainController, summary
from nightexpress.models.entities import ToolStatus
from nightexpress.models.state import ToolchainModel
from nightexpress.views.dialogs.toolchain_dialog import ToolchainDialog
from nightexpress.views.main_window import MainWindow

pytestmark = pytest.mark.ui

OK = (
    ToolStatus("rustup", True, "1.28.2"),
    ToolStatus("cargo", True, "1.95.0"),
    ToolStatus("rustc", True, "1.95.0"),
    ToolStatus("probe-rs", True, "0.32.0", required=False),
)


class FakeProbe:
    search_path = "/usr/bin"

    def __init__(self, statuses: tuple[ToolStatus, ...]) -> None:
        self.statuses = statuses
        self.calls = 0

    def check_all(self) -> tuple[ToolStatus, ...]:
        self.calls += 1
        return self.statuses


def test_summary() -> None:
    assert summary(OK) == ("Rust: готовий", True)
    no_probe = (*OK[:3], ToolStatus("probe-rs", False, required=False))
    assert summary(no_probe) == ("Rust: готовий", True)  # optional tools do not block
    assert summary((ToolStatus("cargo", False),)) == ("Rust: не знайдено cargo", False)


def test_check_runs_in_background_and_updates_views(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    model = ToolchainModel()
    probe = FakeProbe(OK)
    controller = ToolchainController(model, probe, window)  # type: ignore[arg-type]

    with qtbot.waitSignal(controller.finished, timeout=5000):
        window.check_tools_requested.emit()  # e.g. "Check again" on the welcome screen
        controller.check()  # a second request while checking is ignored
    assert probe.calls == 1
    assert model.is_ready and not model.checking
    assert window.tools_status.text() == "Rust: готовий"
    assert window.welcome.home.environment.header.text() == "Середовище готове"
    assert "probe-rs" in window.welcome.settings.tools.text()
    assert window.settings_dialog.application.tools.text() == window.welcome.settings.tools.text()


MISSING = (
    ToolStatus("rustup", True, "1.28.2", path=Path("/Users/dev/.cargo/bin/rustup")),
    ToolStatus(
        "cargo",
        False,
        path=Path("/Users/dev/.cargo/bin/cargo"),
        install_hint="cargo знайдено, але він не працює: no default. Спробуйте: rustup default",
        install_command="rustup default stable",
        help_url="https://rustup.rs",
    ),
    ToolStatus(
        "probe-rs",
        False,
        install_hint="Для ST-Link встановіть probe-rs: brew install probe-rs-tools",
        required=False,
        install_command="brew install probe-rs-tools",
        help_url="https://probe.rs/docs/getting-started/installation/",
    ),
    ToolStatus(
        "rust-analyzer",
        False,
        install_hint="Встановіть Rust: завантажте rustup-init.exe з https://rustup.rs",
        required=False,
        help_url="https://rustup.rs",
    ),  # no command: the hint says what to do (the Windows installer case)
)


def test_dialog_rows(qtbot) -> None:
    dialog = ToolchainDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.header.text() == "Перевіряю інструменти…"
    assert not dialog.recheck_button.isEnabled()

    dialog.set_statuses(MISSING)
    assert dialog.recheck_button.isEnabled()
    assert dialog.header.text() == "Потрібне налаштування"
    assert "Без cargo проєкт не збереться" in dialog.summary.text()

    rustup = dialog.rows["rustup"]
    assert rustup.version.text() == "1.28.2" and rustup.version.isVisible()
    assert rustup.path.isVisible()
    assert not rustup.copy_button.isVisible() and not rustup.help_button.isVisible()

    cargo = dialog.rows["cargo"]
    assert "не працює" in cargo.title.text()
    assert "no default" in cargo.hint.text() and cargo.hint.isVisible()
    assert cargo.command.text() == "rustup default stable"

    probe = dialog.rows["probe-rs"]
    assert "не встановлено" in probe.title.text() and "USB-TTL" in probe.purpose.text()
    assert not probe.hint.isVisible() and probe.copy_button.isVisible()
    with qtbot.waitSignal(dialog.command_copied, timeout=1000) as copied:
        probe.copy_button.click()
    assert copied.args == ["brew install probe-rs-tools"]
    assert QGuiApplication.clipboard().text() == "brew install probe-rs-tools"
    with qtbot.waitSignal(dialog.open_url_requested, timeout=1000) as opened:
        probe.help_button.click()
    assert opened.args == ["https://probe.rs/docs/getting-started/installation/"]

    analyzer = dialog.rows["rust-analyzer"]
    assert not analyzer.command.isVisible() and not analyzer.copy_button.isVisible()
    assert "rustup-init.exe" in analyzer.hint.text() and analyzer.help_button.isVisible()

    # Rust missing altogether: the rustup command is shown once, not for cargo and rustc too.
    rust = "curl https://sh.rustup.rs | sh"
    dialog.set_statuses(
        [
            ToolStatus(name, False, install_command=rust, help_url="https://rustup.rs")
            for name in ("rustup", "cargo", "rustc")
        ]
    )
    assert dialog.rows["rustup"].copy_button.isVisible()
    for name in ("cargo", "rustc"):
        row = dialog.rows[name]
        assert not row.copy_button.isVisible() and not row.help_button.isVisible()
        assert row.hint.text() == "Встановиться разом із rustup — команда вище."

    dialog.set_statuses(OK)  # rows are rebuilt, not appended
    assert dialog.header.text() == "Середовище готове"
    assert dialog.summary.text() == "Усі інструменти на місці."
    assert list(dialog.rows) == ["rustup", "cargo", "rustc", "probe-rs"]


def test_dialog_from_window_checks_again(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    model = ToolchainModel()
    probe = FakeProbe(MISSING)
    controller = ToolchainController(model, probe, window)  # type: ignore[arg-type]
    urls: list[str] = []
    window.open_url = urls.append  # type: ignore[method-assign]

    with qtbot.waitSignal(controller.finished, timeout=5000):
        window.actions_by_name["check_tools"].trigger()  # Help → Check the Rust tools…
    dialog = controller.dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.header.text() == "Потрібне налаштування"
    assert window.tools_status.text() == "Rust: не працює cargo"  # present, but broken

    dialog.rows["probe-rs"].help_button.click()
    assert urls == ["https://probe.rs/docs/getting-started/installation/"]

    probe.statuses = OK  # the user installed the tools and presses "Check again"
    with qtbot.waitSignal(controller.finished, timeout=5000):
        dialog.recheck_button.click()
    assert dialog.header.text() == "Середовище готове"
    assert probe.calls == 2

    with qtbot.waitSignal(controller.finished, timeout=5000):
        window.tools_status.clicked.emit()  # the same dialog comes back and checks again
    assert controller.dialog is dialog and probe.calls == 3


def test_crashed_check_does_not_stay_checking(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    model = ToolchainModel()
    probe = FakeProbe(OK)
    controller = ToolchainController(model, probe, window)  # type: ignore[arg-type]
    with qtbot.waitSignal(controller.finished, timeout=5000):
        controller.check()

    def crash() -> tuple[ToolStatus, ...]:
        raise KeyError("bug")

    probe.check_all = crash  # type: ignore[method-assign]
    with qtbot.waitSignal(controller.finished, timeout=5000):
        controller.check()
    assert not model.checking
    assert window.tools_status.text() == "Rust: перевірка не вдалася"
    assert model.statuses == OK  # what was known before stays on screen
