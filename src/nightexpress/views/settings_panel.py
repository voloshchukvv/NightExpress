"""Application preferences shared by the welcome page and the editor's settings dialog."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, Text, _, live, on_change, redo_on_switch, resolve
from nightexpress.models.entities import ToolStatus
from nightexpress.models.entities.font import FONT_SIZE_MAX, FONT_SIZE_MIN, FontArea
from nightexpress.views.icons import LABEL_ICON_SIZE, label_icon
from nightexpress.views.theme import palette
from nightexpress.views.widgets import ElidedLabel

# Checkbox options: key -> (section, text). The keys match SettingsStore methods.
OPTIONS = {
    "format_on_save": ("editor", N_("Форматувати файли .rs під час збереження (rustfmt)")),
    "bracket_pair_guides": ("editor", N_("Показувати напрямні парних дужок")),
    "minimap": ("editor", N_("Показувати мінікарту коду праворуч")),
    "save_before_build": ("behaviour", N_("Зберігати змінені файли перед збіркою")),
    "open_last_project": ("behaviour", N_("Відкривати останній проєкт під час запуску")),
}

FONT_AREA_NAMES = {
    FontArea.EDITOR: N_("Редактор"),
    FontArea.TERMINAL: N_("Термінал"),
    FontArea.UART: "UART",
    FontArea.LOGS: N_("Журнали збірки й прошивання"),
}
# The rows of the settings when every area has a size of its own.
FONT_AREA_LABELS = {
    FontArea.EDITOR: N_("Шрифт редактора:"),
    FontArea.TERMINAL: N_("Шрифт термінала:"),
    FontArea.UART: N_("Шрифт UART:"),
    FontArea.LOGS: N_("Шрифт журналів збірки:"),
}
SINGLE_FONT_SIZE_TEXT = N_("Один розмір для редактора, термінала, UART і журналів")


class AppSettingsPanel(QWidget):
    projects_directory_requested = Signal()
    font_size_changed = Signal(object, int)  # FontArea, size in points
    single_font_size_changed = Signal(bool)  # one size for every area, or one for each
    check_tools_requested = Signal()
    option_changed = Signal(str, bool)  # one of OPTIONS, new value
    theme_changed = Signal(str)  # the id picked in the list of themes
    language_changed = Signal(str)  # the code picked in the list of languages, or "system"

    def __init__(self, parent: QWidget, *, welcome: bool = False) -> None:
        super().__init__(parent)

        projects_title = QLabel(self)
        live(projects_title.setText, N_("Тека для нових проєктів"))
        projects_title.setObjectName("sectionTitle")
        self.projects_dir = ElidedLabel("", self)
        self.projects_dir.setObjectName("mutedText")
        self.change_dir = QPushButton(self)
        live(self.change_dir.setText, N_("Змінити…"))
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.change_dir)
        dir_row.addWidget(self.projects_dir, 1)

        look_title = QLabel(self)
        live(look_title.setText, N_("Вигляд"))
        look_title.setObjectName("sectionTitle")
        self.theme_combo = QComboBox(self)
        self.theme_combo.setMinimumWidth(220)
        self.theme_combo.activated.connect(
            lambda index: self.theme_changed.emit(self.theme_combo.itemData(index))
        )
        theme_label = QLabel(self)
        live(theme_label.setText, N_("Тема:"))
        theme_row = QHBoxLayout()
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        self.language_combo = QComboBox(self)
        self.language_combo.setMinimumWidth(220)
        self.language_combo.activated.connect(
            lambda index: self.language_changed.emit(self.language_combo.itemData(index))
        )
        language_label = QLabel(self)
        live(language_label.setText, N_("Мова:"))
        language_row = QHBoxLayout()
        language_row.addWidget(language_label)
        language_row.addWidget(self.language_combo)
        language_row.addStretch(1)

        # The two lists start at the same place whatever the lengths of their captions.
        def align_captions() -> None:
            for label in (theme_label, language_label):
                label.setMinimumWidth(0)
                label.setMaximumWidth(16777215)
            width = max(theme_label.sizeHint().width(), language_label.sizeHint().width())
            theme_label.setFixedWidth(width)
            language_label.setFixedWidth(width)

        on_change(self, align_captions, "align captions")

        # Font sizes: one box while a single size serves every area, a box per area otherwise.
        self.single_font_size = QCheckBox(self)
        live(self.single_font_size.setText, SINGLE_FONT_SIZE_TEXT)
        self.single_font_size.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.single_font_size.setChecked(True)
        self.single_font_size.toggled.connect(self._on_single_font_size_toggled)
        self.font_sizes: dict[FontArea, QSpinBox] = {}
        self._font_labels: dict[FontArea, QLabel] = {}
        font_grid = QGridLayout()
        font_grid.setHorizontalSpacing(12)
        font_grid.setVerticalSpacing(10)
        for row, area in enumerate(FontArea):
            box = QSpinBox(self)
            box.setRange(FONT_SIZE_MIN, FONT_SIZE_MAX)
            box.setSuffix(" pt")
            # The style sheet's left padding does not reach the inner line edit.
            box.lineEdit().setTextMargins(8, 0, 0, 0)
            box.valueChanged.connect(lambda size, a=area: self.font_size_changed.emit(a, size))
            label = QLabel(self)
            label.setBuddy(box)
            font_grid.addWidget(label, row, 0)
            font_grid.addWidget(box, row, 1)
            self.font_sizes[area] = box
            self._font_labels[area] = label
        font_grid.setColumnStretch(2, 1)
        self._show_font_rows()

        editor_title = QLabel(self)
        live(editor_title.setText, N_("Редактор"))
        editor_title.setObjectName("sectionTitle")

        self.options: dict[str, QCheckBox] = {}
        for key, (_section, text) in OPTIONS.items():
            box = QCheckBox(self)
            live(box.setText, text)
            box.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            box.toggled.connect(lambda checked, k=key: self.option_changed.emit(k, checked))
            self.options[key] = box
        behaviour_title = QLabel(self)
        live(behaviour_title.setText, N_("Робота з проєктом"))
        behaviour_title.setObjectName("sectionTitle")

        tools_title = QLabel(self)
        live(tools_title.setText, N_("Інструменти"))
        tools_title.setObjectName("sectionTitle")
        self._statuses: tuple[ToolStatus, ...] = ()
        self.tools = QLabel(self)
        self.tools.setTextFormat(Qt.TextFormat.RichText)
        self.tools.setWordWrap(True)
        self.tools.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.recheck = QPushButton(label_icon("refresh-cw", palette.TEXT), "", self)
        live(self.recheck.setText, N_("Перевірити знову"))
        self.recheck.setIconSize(LABEL_ICON_SIZE)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        if welcome:
            layout.setContentsMargins(32, 36, 32, 28)  # welcome_view.PAGE_MARGINS
            title = QLabel(self)
            live(title.setText, N_("Налаштування"))
            title.setObjectName("welcomeTitle")
            layout.addWidget(title)
            layout.addSpacing(14)
        else:
            layout.setContentsMargins(18, 8, 18, 16)
        sections: list[QWidget | QLayout] = [projects_title, dir_row, look_title, theme_row]
        sections += [language_row, font_grid, self.single_font_size, editor_title]
        sections += [b for k, b in self.options.items() if OPTIONS[k][0] == "editor"]
        sections.append(behaviour_title)
        sections += [b for k, b in self.options.items() if OPTIONS[k][0] == "behaviour"]
        sections.append(tools_title)
        for widget in sections:
            if isinstance(widget, QLayout):
                layout.addLayout(widget)
            elif isinstance(widget, QCheckBox):
                layout.addWidget(widget)
            else:
                layout.addSpacing(10)
                layout.addWidget(widget)
        layout.addWidget(self.tools)
        layout.addWidget(self.recheck, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.change_dir.clicked.connect(self.projects_directory_requested)
        self.recheck.clicked.connect(self.check_tools_requested)

    def set_checking(self, checking: bool) -> None:
        self.recheck.setEnabled(not checking)

    def set_projects_directory(self, path: Path) -> None:
        self.projects_dir.setText(str(path))
        self.projects_dir.setToolTip(str(path))

    def set_options(self, values: dict[str, bool]) -> None:
        for key, value in values.items():
            if key in self.options:
                with QSignalBlocker(self.options[key]):
                    self.options[key].setChecked(value)

    @redo_on_switch
    def set_themes(self, choices: Iterable[tuple[str, Text]]) -> None:
        """The list of themes as (id, name), in the order to show."""
        current = self.theme_combo.currentData()
        with QSignalBlocker(self.theme_combo):
            self.theme_combo.clear()
            for theme_id, name in choices:
                self.theme_combo.addItem(resolve(name), theme_id)
        self.set_theme(current)

    def set_theme(self, theme_id: str | None) -> None:
        index = self.theme_combo.findData(theme_id)
        if index >= 0:
            with QSignalBlocker(self.theme_combo):
                self.theme_combo.setCurrentIndex(index)

    @redo_on_switch
    def set_languages(self, choices: Iterable[tuple[str, Text]]) -> None:
        """The list of languages as (code, name), in the order to show."""
        current = self.language_combo.currentData()
        with QSignalBlocker(self.language_combo):
            self.language_combo.clear()
            for code, name in choices:
                self.language_combo.addItem(resolve(name), code)
        self.set_language(current)

    def set_language(self, code: str | None) -> None:
        index = self.language_combo.findData(code)
        if index >= 0:
            with QSignalBlocker(self.language_combo):
                self.language_combo.setCurrentIndex(index)

    def set_font_sizes(self, sizes: dict[FontArea, int], single: bool) -> None:
        """Show the saved sizes and whether one size serves every area."""
        for area, size in sizes.items():
            with QSignalBlocker(self.font_sizes[area]):
                self.font_sizes[area].setValue(size)
        with QSignalBlocker(self.single_font_size):
            self.single_font_size.setChecked(single)
        self._show_font_rows()

    def _on_single_font_size_toggled(self, single: bool) -> None:
        self._show_font_rows()
        self.single_font_size_changed.emit(single)

    @redo_on_switch
    def _show_font_rows(self) -> None:
        single = self.single_font_size.isChecked()
        for area, label in self._font_labels.items():
            shown = area is FontArea.EDITOR or not single
            label.setVisible(shown)
            self.font_sizes[area].setVisible(shown)
            label.setText(_(FONT_AREA_LABELS[area]))
        if single:  # the only box left stands for all of them
            self._font_labels[FontArea.EDITOR].setText(_("Розмір шрифту:"))

    def refresh_theme(self) -> None:
        self.set_statuses(self._statuses)  # the list carries colours inside its text

    @redo_on_switch
    def set_statuses(self, statuses: Iterable[ToolStatus]) -> None:
        self._statuses = tuple(statuses)
        rows = []
        for s in self._statuses:
            if s.found:
                rows.append(
                    f"<p><b>{s.name}</b> {s.version or ''}<br>"
                    f"<span style='color:{palette.TEXT_MUTED}'>{s.path or ''}</span></p>"
                )
            else:
                hint = s.install_hint.replace("<", "&lt;")
                color = palette.ERROR if s.required else palette.WARNING
                # Present but broken: the hint says what is wrong, "not found" would argue.
                state = _("не працює") if s.path is not None else _("не знайдено")
                rows.append(
                    f"<p><b style='color:{color}'>{s.name}</b> — {state}<br>"
                    f"<span style='color:{palette.TEXT_MUTED}'>{hint}</span></p>"
                )
        self.tools.setText("".join(rows))
