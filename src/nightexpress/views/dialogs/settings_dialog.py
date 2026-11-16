"""Settings opened above the editor while a project is open."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, QSize, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, live
from nightexpress.models.entities import BuildProfile, ProjectConfig
from nightexpress.views.icons import icon
from nightexpress.views.settings_panel import AppSettingsPanel
from nightexpress.views.theme import palette
from nightexpress.views.widgets import ElidedLabel


class SettingsDialog(QDialog):
    profile_selected = Signal(object)  # BuildProfile

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        live(self.setWindowTitle, N_("Налаштування"))
        self.resize(620, 564)

        title = QLabel(self)
        live(title.setText, N_("Налаштування"))
        title.setObjectName("sectionTitle")

        self.project_name = ElidedLabel("", self)
        self.project_name.setObjectName("envTitle")
        self.project_path = ElidedLabel("", self)
        self.project_path.setObjectName("mutedText")
        self.board_name = ElidedLabel("", self)
        self.target_triple = ElidedLabel("", self)
        self.profile_combo = QComboBox(self)
        live(self.profile_combo.setAccessibleName, N_("Профіль збірки"))
        for profile in BuildProfile:
            self.profile_combo.addItem(profile.target_dir_name, profile)
        self.profile_combo.activated.connect(
            lambda index: self.profile_selected.emit(self.profile_combo.itemData(index))
        )

        card = QFrame(self)
        card.setObjectName("panel")
        form = QFormLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)
        for caption, field in (
            (N_("Плата"), self.board_name),
            (N_("Ціль компіляції"), self.target_triple),
            (N_("Профіль збірки"), self.profile_combo),
        ):
            label = QLabel(card)
            live(label.setText, caption)
            form.addRow(label, field)

        project = QWidget(self)
        project_layout = QVBoxLayout(project)
        project_layout.setContentsMargins(18, 18, 18, 16)
        project_layout.setSpacing(6)
        project_layout.addWidget(self.project_name)
        project_layout.addWidget(self.project_path)
        project_layout.addSpacing(12)
        project_layout.addWidget(card)
        project_layout.addStretch(1)

        self.application = AppSettingsPanel(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.application)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("settingsTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.addTab(project, icon("folder", palette.TEXT_MUTED), "")
        self.tabs.addTab(scroll, icon("settings", palette.TEXT_MUTED), "")
        live(self.tabs.setTabText, N_("Проєкт"), 0)
        live(self.tabs.setTabText, N_("Застосунок"), 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        live(buttons.button(QDialogButtonBox.StandardButton.Close).setText, N_("Готово"))
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(buttons)

    def show_for_project(
        self, config: ProjectConfig, board: str, target: str, profile: BuildProfile
    ) -> None:
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            return
        self.project_name.setText(config.name)
        self.project_path.setText(str(config.path))
        self.project_path.setToolTip(str(config.path))
        self.board_name.setText(board)
        self.board_name.setToolTip(board)
        self.target_triple.setText(target)
        with QSignalBlocker(self.profile_combo):
            self.profile_combo.setCurrentIndex(self.profile_combo.findData(profile))
        self.tabs.setCurrentIndex(0)
        self.open()
