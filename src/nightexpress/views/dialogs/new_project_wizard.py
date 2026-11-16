"""New project wizard: name and location -> board -> template (with file preview)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from nightexpress.i18n import N_, _, live
from nightexpress.models.entities import (
    BoardDefinition,
    ChipDefinition,
    ExampleDefinition,
    NewProjectRequest,
    project_name_error,
)
from nightexpress.views.board_picker import BoardPicker, SearchFunction
from nightexpress.views.formatting import home_relative
from nightexpress.views.theme import palette

ExamplesFunction = Callable[[str], list[ExampleDefinition]]
PreviewFunction = Callable[[NewProjectRequest], list[str]]

DEFAULT_NAME = "my_project"


def _add_header(layout: QVBoxLayout, title: str, subtitle: str) -> None:
    """Page heading in the app's own type scale (QWizard's banner ignores the theme)."""
    heading = QLabel(title)
    heading.setObjectName("wizardTitle")
    note = QLabel(subtitle)
    note.setObjectName("mutedText")
    note.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(note)
    layout.addSpacing(12)


class _NamePage(QWizardPage):
    def __init__(self, wizard: NewProjectWizard) -> None:
        super().__init__(wizard)
        self.name_edit = QLineEdit(DEFAULT_NAME, self)
        self.location_edit = QLineEdit(self)
        browse = QPushButton(_("Огляд…"), self)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.location_edit, 1)
        row.addWidget(browse)
        self.path_label = QLabel(self)
        self.error_label = QLabel(self)
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        _add_header(
            layout, _("Назва й розташування"), _("Проєкт — це звичайний Cargo-проєкт у новій теці.")
        )
        form = QFormLayout()
        layout.addLayout(form)
        layout.addStretch(1)
        form.addRow(_("Назва:"), self.name_edit)
        form.addRow(_("Тека:"), row)
        form.addRow("", self.path_label)
        self.path_label.setObjectName("mutedText")
        form.addRow("", self.error_label)
        self.name_edit.textChanged.connect(self._validate)
        self.location_edit.textChanged.connect(self._validate)

    def _browse(self) -> None:
        start = self.location_edit.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, _("Тека для проєктів"), start)
        if chosen:
            self.location_edit.setText(chosen)

    def _target(self) -> Path | None:
        """The folder to create, or None while the location cannot be resolved."""
        name = self.name_edit.text().strip()
        location = self.location_edit.text().strip()
        if not name or not location:
            return None
        try:
            return Path(location).expanduser() / name
        except RuntimeError:  # "~unknown_user"
            return None

    def error(self) -> str | None:
        name = self.name_edit.text().strip()
        if (message := project_name_error(name)) is not None:
            return message
        location = self.location_edit.text().strip()
        if not location:
            return _("Виберіть теку.")
        target = self._target()
        if target is None:
            return _("Теку {path} не знайдено.").format(path=location)
        try:
            taken = target.exists() and (not target.is_dir() or any(target.iterdir()))
        except OSError:  # e.g. no permission to look inside; typed text must never crash
            return _("Тека {path} недоступна.").format(path=target)
        if taken:
            return _("Тека {path} уже існує й не порожня.").format(path=target)
        return None

    def _validate(self) -> None:
        error = self.error()
        target = self._target()
        self.path_label.setText(
            _("Буде створено: {path}").format(path=home_relative(target, max_length=54))
            if target
            else ""
        )
        self.path_label.setToolTip(str(target) if target else "")
        self.error_label.setText(error or "")
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return self.error() is None


class _BoardPage(QWizardPage):
    def __init__(self, wizard: NewProjectWizard) -> None:
        super().__init__(wizard)
        self.picker = BoardPicker(self)
        self.picker.board_changed.connect(lambda _id: self.completeChanged.emit())
        layout = QVBoxLayout(self)
        _add_header(layout, _("Плата"), _("Знайдіть свою плату за назвою або маркуванням на чипі."))
        layout.addWidget(self.picker, 1)

    def isComplete(self) -> bool:  # noqa: N802
        return self.picker.current_board_id is not None


class _ExamplePage(QWizardPage):
    def __init__(self, wizard: NewProjectWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        self.examples = QListWidget(self)
        self.examples.currentItemChanged.connect(self._on_example_changed)
        self.description = QLabel(self)
        self.description.setWordWrap(True)
        self.files = QListWidget(self)
        self.files.setObjectName("previewFiles")
        # A preview, not a choice: nothing in it to select or focus.
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.files.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.error_label = QLabel(self)
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        _add_header(layout, _("Шаблон"), _("Почніть із порожнього проєкту або виберіть приклад."))
        layout.addWidget(self.examples, 1)
        layout.addWidget(self.description)
        self.destination = QLabel(self)
        self.destination.setObjectName("mutedText")
        layout.addWidget(self.destination)
        layout.addWidget(self.files, 1)
        # The project is not a git repository yet; the file is for those who will make it one.
        self.gitignore = QCheckBox(self)
        live(self.gitignore.setText, N_("Створити .gitignore"))
        self.gitignore.setChecked(True)
        self.gitignore.setToolTip(
            _("Файл для git: не додавати до репозиторію теку збірки target/ і файли *.bin")
        )
        self.gitignore.toggled.connect(lambda _on: self._show_files())
        layout.addWidget(self.gitignore)
        layout.addWidget(self.error_label)

    def initializePage(self) -> None:  # noqa: N802
        self.examples.clear()
        self.error_label.clear()
        board_id = self._wizard.board_page.picker.current_board_id
        if board_id is None:
            return

        self.examples.addItem("Blank Project")
        preferred = 0
        for example in self._wizard.examples_for(board_id):
            if example.id == self._wizard.default_example_id:
                preferred = self.examples.count()
            item = QListWidgetItem(example.title)
            item.setData(Qt.ItemDataRole.UserRole, example)
            self.examples.addItem(item)
        self.examples.ensurePolished()
        rows_height = sum(self.examples.sizeHintForRow(row) for row in range(self.examples.count()))
        # Reserve room for a short list before sharing the remaining height with the file preview.
        list_height = rows_height + 2 * self.examples.frameWidth()
        self.examples.setMinimumHeight(min(list_height, self.examples.sizeHint().height()))
        self.examples.setCurrentRow(preferred)

    @property
    def current_example(self) -> ExampleDefinition | None:
        item = self.examples.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_example_changed(self, current: QListWidgetItem | None, _previous: object) -> None:
        description = ""
        if current is not None:
            example = self.current_example
            description = (
                example.description
                if example is not None
                else _("Мінімальний Cargo-проєкт для вибраної плати зі стартовим main.rs.")
            )
        self.description.setText(description)
        self._show_files()
        self.completeChanged.emit()

    def _show_files(self) -> None:
        self.files.clear()
        self.destination.clear()
        self.destination.setToolTip("")
        request = self._wizard.request()
        if request is not None:
            self.files.addItems(self._wizard.preview(request))
            folder = home_relative(request.project_dir, max_length=54)
            live(
                self.destination.setText,
                lambda: _("Буде створено в {folder}:").format(folder=folder),
            )
            self.destination.setToolTip(str(request.project_dir))

    def isComplete(self) -> bool:  # noqa: N802
        return self.examples.currentItem() is not None


STEPS = (N_("Назва й розташування"), N_("Плата"), N_("Шаблон"))


class _StepList(QFrame):
    """Left column: the three steps, the current one highlighted."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("wizardSteps")
        self.setFixedWidth(210)
        self.labels: list[QLabel] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(6)
        for text in STEPS:
            label = QLabel(_(text), self)
            label.setObjectName("wizardStep")
            label.setWordWrap(True)
            layout.addWidget(label)
            self.labels.append(label)
        layout.addStretch(1)

    def set_current(self, index: int) -> None:
        for i, label in enumerate(self.labels):
            label.setProperty("state", "current" if i == index else "done" if i < index else "")
            label.style().unpolish(label)
            label.style().polish(label)


class NewProjectWizard(QWizard):
    """Emits ``create_requested``; the controller answers with accept_created() or show_error()."""

    create_requested = Signal(object)  # NewProjectRequest

    def __init__(
        self,
        parent: QWidget | None,
        boards: Iterable[BoardDefinition],
        chips: Mapping[str, ChipDefinition],
        default_location: Path,
        search: SearchFunction | None = None,
        examples_for: ExamplesFunction | None = None,
        preview: PreviewFunction | None = None,
        default_board_id: str | None = None,
        default_name: str = DEFAULT_NAME,
        default_example_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        live(self.setWindowTitle, N_("Новий проєкт"))
        self.default_example_id = default_example_id
        # Classic style: no banner drawn by QWizard itself (it ignores the theme); the steps
        # are shown on the left instead.
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.steps = _StepList(self)
        self.setSideWidget(self.steps)
        self.currentIdChanged.connect(lambda _id: self.steps.set_current(self.current_step))
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setButtonText(QWizard.WizardButton.FinishButton, _("Створити"))
        self.setButtonText(QWizard.WizardButton.NextButton, _("Далі"))
        self.setButtonText(QWizard.WizardButton.BackButton, _("Назад"))
        self.setButtonText(QWizard.WizardButton.CancelButton, _("Скасувати"))
        for role in (QWizard.WizardButton.NextButton, QWizard.WizardButton.FinishButton):
            palette.set_button_role(self.button(role), "primary")
        self.setButtonLayout(
            [
                QWizard.WizardButton.CancelButton,
                QWizard.WizardButton.Stretch,
                QWizard.WizardButton.BackButton,
                QWizard.WizardButton.NextButton,
                QWizard.WizardButton.FinishButton,
            ]
        )
        self.resize(860, 540)
        self.examples_for: ExamplesFunction = examples_for or (lambda _board: [])
        self.preview: PreviewFunction = preview or (lambda _request: [])

        self.name_page = _NamePage(self)
        self.board_page = _BoardPage(self)
        self.example_page = _ExamplePage(self)
        for page in (self.name_page, self.board_page, self.example_page):
            self.addPage(page)
        self.name_page.name_edit.setText(default_name)
        self.name_page.location_edit.setText(str(default_location))
        self.board_page.picker.set_boards(boards, chips, search)
        if default_board_id:
            self.board_page.picker.select(default_board_id)

    @property
    def current_step(self) -> int:
        """0-based index of the visible page."""
        return self.pageIds().index(self.currentId()) if self.currentId() >= 0 else 0

    def request(self) -> NewProjectRequest | None:
        board_id = self.board_page.picker.current_board_id
        if board_id is None or not self.example_page.isComplete() or self.name_page.error():
            return None
        example = self.example_page.current_example
        return NewProjectRequest(
            name=self.name_page.name_edit.text().strip(),
            location=Path(self.name_page.location_edit.text().strip()).expanduser(),
            board_id=board_id,
            example_id=example.id if example is not None else None,
            create_gitignore=self.example_page.gitignore.isChecked(),
        )

    def accept(self) -> None:
        request = self.request()
        if request is not None:
            self.example_page.error_label.clear()
            self.create_requested.emit(request)

    def accept_created(self) -> None:
        super().accept()

    def show_error(self, message: str) -> None:
        self.example_page.error_label.setText(message)
