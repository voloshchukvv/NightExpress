"""Start screen: sidebar navigation, project cards, environment."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, Text, _, live, redo_on_switch
from nightexpress.models.entities import ExampleDefinition, RecentProject, ToolStatus
from nightexpress.views.formatting import home_relative, relative_day
from nightexpress.views.icons import LABEL_ICON_SIZE, label_icon
from nightexpress.views.settings_panel import AppSettingsPanel
from nightexpress.views.theme import palette
from nightexpress.views.widgets import (
    CardButton,
    ClickableCard,
    Separator,
    icon_label,
    set_icon,
)

HOME, PROJECTS, SETTINGS = "home", "projects", "settings"
PAGE_MARGINS = (32, 36, 32, 28)  # the same on every page, so the title stays put between them
SIDEBAR_WIDTH = 216  # its right edge is the line the header's divider continues
CONTENT_MAX_WIDTH = 960

# Environment rows on the start screen: (title, tools that must be found).
ENVIRONMENT_ROWS = (
    ("Rust toolchain", ("rustup", "rustc")),
    ("Cargo", ("cargo",)),
    ("rust-analyzer", ("rust-analyzer",)),
    ("probe-rs", ("probe-rs",)),
)
# Rows for tools nothing uses yet: missing is not a problem, so no warning colour.
NOT_USED_YET = {"rust-analyzer": N_("необов'язково")}


REVEAL_TEXT = N_("Показати у Finder") if sys.platform == "darwin" else N_("Показати в теці")

# Example id -> icon on the start-screen card (unknown examples get a generic one).
EXAMPLE_ICONS = {
    "blink": "lightbulb",
    "serial_hello": "message-square-text",
    "button": "mouse-pointer-click",
    "rtt_hello": "file-text",
}


class _ProjectList(QWidget):
    """Vertical list of recent-project cards (right click: reveal / remove from the list)."""

    open_requested = Signal(object)  # Path
    reveal_requested = Signal(object)  # Path
    remove_requested = Signal(object)  # Path

    def __init__(self, empty_text: Text, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(14)
        self.empty = QLabel(self)
        live(self.empty.setText, empty_text)
        self.empty.setObjectName("mutedText")
        self.empty.setWordWrap(True)
        self.cards: list[CardButton] = []
        self._layout.addWidget(self.empty)
        self._layout.addStretch(1)

    @redo_on_switch
    def set_projects(self, projects: Iterable[RecentProject]) -> None:
        for card in self.cards:
            card.deleteLater()
        self.cards = []
        items = list(projects)
        for index, project in enumerate(items):
            card = self._card(project)
            self._layout.insertWidget(index, card)
            self.cards.append(card)
        self.empty.setVisible(not items)

    def _card(self, project: RecentProject) -> CardButton:
        if project.missing:
            card = CardButton(
                "folder-x", project.name, home_relative(project.path), self, "recentCard",
                icon_color=palette.TEXT_MUTED,
            )  # fmt: skip
            card.setProperty("missing", True)  # greyed out by the stylesheet
            card.right.setText(_("не знайдено"))
            card.setToolTip(
                _(
                    "Теки {path} більше немає. Клацніть правою кнопкою, щоб "
                    "прибрати проєкт зі списку."
                ).format(path=project.path)
            )
        else:
            card = CardButton(
                "folder", project.name, home_relative(project.path), self, "recentCard"
            )
            details = [d for d in (project.chip_label, relative_day(project.opened_at)) if d]
            card.right.setText("  ·  ".join(details))
            card.setToolTip(str(project.path))
        card.clicked.connect(lambda p=project.path: self.open_requested.emit(p))
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda position, c=card, pr=project: self._show_menu(c, pr, position)
        )
        return card

    def menu_for(self, project: RecentProject) -> QMenu:
        menu = QMenu(self)
        menu.addAction(_("Відкрити"), lambda: self.open_requested.emit(project.path)).setEnabled(
            not project.missing
        )
        menu.addAction(_(REVEAL_TEXT), lambda: self.reveal_requested.emit(project.path)).setEnabled(
            not project.missing
        )
        menu.addSeparator()
        menu.addAction(_("Прибрати зі списку"), lambda: self.remove_requested.emit(project.path))
        return menu

    def _show_menu(self, card: CardButton, project: RecentProject, position: QPoint) -> None:
        menu = self.menu_for(project)
        menu.exec(card.mapToGlobal(position))
        menu.deleteLater()


class _ExampleCard(ClickableCard):
    """ "Start from an example": icon and title, a one-line description under them."""

    def __init__(self, example: ExampleDefinition, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.example_id = example.id
        live(
            self.setToolTip,
            lambda: _("Створити проєкт із прикладу «{title}»").format(title=example.title),
        )
        mark = icon_label(
            EXAMPLE_ICONS.get(example.id, "file-code"), 20, palette.ACCENT_BRIGHT, self
        )
        self.title = QLabel(example.title, self)
        self.title.setObjectName("exampleTitle")
        self.description = QLabel(self)
        live(self.description.setText, lambda: example.description)
        self.description.setObjectName("cardSubtitle")
        self.description.setWordWrap(True)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(mark)
        top.addWidget(self.title, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.description)
        layout.addStretch(1)
        for child in (self.title, self.description, mark):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


class _EnvironmentCard(QFrame):
    check_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("envCard")
        self._statuses: tuple[ToolStatus, ...] | None = None
        self.header_icon = icon_label("refresh-cw", 30, palette.TEXT_MUTED, self)
        self.header = QLabel(self)
        live(self.header.setText, N_("Перевіряю середовище…"))
        self.header.setObjectName("envTitle")
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(self.header_icon)
        top.addWidget(self.header, 1)

        self.rows: dict[str, tuple[QLabel, QLabel]] = {}
        rows = QVBoxLayout()
        rows.setSpacing(16)
        for title, _tools in ENVIRONMENT_ROWS:
            mark = QLabel(self)
            mark.setFixedSize(22, 22)
            text = QLabel(title, self)
            text.setObjectName("envRow")
            text.setTextFormat(Qt.TextFormat.RichText)
            line = QHBoxLayout()
            line.setSpacing(14)
            line.addWidget(mark)
            line.addWidget(text, 1)
            rows.addLayout(line)
            self.rows[title] = (mark, text)

        self.check_link = QPushButton(self)
        live(self.check_link.setText, N_("Перевірити налаштування  ›"))
        self.check_link.setObjectName("linkButton")
        self.check_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_link.clicked.connect(self.check_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(18)
        layout.addLayout(top)
        layout.addWidget(Separator(vertical=False, parent=self))
        layout.addLayout(rows)
        layout.addWidget(Separator(vertical=False, parent=self))
        layout.addWidget(self.check_link, 0, Qt.AlignmentFlag.AlignLeft)
        self.setFixedWidth(350)

    def refresh_theme(self) -> None:
        if self._statuses is not None:  # the rows carry colours inside their text
            self.set_statuses(self._statuses)

    @redo_on_switch
    def set_checking(self) -> None:
        self._statuses = None
        set_icon(self.header_icon, "refresh-cw", 30, palette.TEXT_MUTED)
        self.header.setText(_("Перевіряю середовище…"))

    @redo_on_switch
    def set_statuses(self, statuses: Iterable[ToolStatus]) -> bool:
        self._statuses = tuple(statuses)
        by_name = {s.name: s for s in self._statuses}
        blocking = False
        for title, tools in ENVIRONMENT_ROWS:
            mark, text = self.rows[title]
            found = [by_name[t] for t in tools if t in by_name]
            missing = [s for s in found if not s.found]
            text.setText(title)
            if found and not missing:
                set_icon(mark, "circle-check", 22, palette.SUCCESS)
                text.setToolTip(", ".join(f"{s.name} {s.version or ''}" for s in found))
            elif missing and title in NOT_USED_YET:
                set_icon(mark, "circle-dashed", 22, palette.TEXT_MUTED)
                text.setText(
                    f"{title} <span style='color:{palette.TEXT_MUTED}'>· "
                    f"{_(NOT_USED_YET[title])}</span>"
                )
                text.setToolTip(_("Поки не потрібен: підказки в редакторі з'являться після MVP."))
            elif any(s.required for s in missing):
                blocking = True
                set_icon(mark, "circle-x", 22, palette.ERROR)
                text.setToolTip(missing[0].install_hint)
            else:
                set_icon(mark, "circle-alert", 22, palette.WARNING)
                text.setToolTip(missing[0].install_hint if missing else _("не перевірено"))
        if blocking:
            set_icon(self.header_icon, "circle-alert", 30, palette.ERROR)
            self.header.setText(_("Потрібне налаштування"))
        else:
            set_icon(self.header_icon, "circle-check", 30, palette.SUCCESS)
            self.header.setText(_("Середовище готове"))
        return not blocking


class _HomePage(QWidget):
    def __init__(self, view: WelcomeView) -> None:
        super().__init__(view)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title = QLabel(self)
        live(title.setText, N_("Вітаємо у NightExpress"))
        title.setObjectName("welcomeTitle")
        subtitle = QLabel(self)
        live(
            subtitle.setText, N_("Rust для STM32 — почніть із нового проєкту або продовжте роботу")
        )
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setWordWrap(True)
        self.new_card = CardButton(
            "plus",
            N_("Новий проєкт"),
            N_("Створити Cargo-проєкт для STM32"),
            self,
            "primaryCard",
            icon_color=palette.ACCENT_BRIGHT,
        )
        self.open_card = CardButton(
            "folder", N_("Відкрити проєкт"), N_("Відкрити наявну теку Cargo"), self, "card"
        )
        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards.addWidget(self.new_card, 1)
        cards.addWidget(self.open_card, 1)
        recent_title = QLabel(self)
        live(recent_title.setText, N_("Недавні проєкти"))
        recent_title.setObjectName("sectionTitle")
        self.recent = _ProjectList(
            N_(
                "Поки порожньо. Створіть перший проєкт кнопкою «Новий проєкт» вище "
                "або почніть із прикладу нижче."
            ),
            self,
        )
        self.examples_title = QLabel(self)
        live(self.examples_title.setText, N_("Почати з прикладу"))
        self.examples_title.setObjectName("sectionTitle")
        self.example_cards: list[_ExampleCard] = []
        self._examples_row = QHBoxLayout()
        self._examples_row.setSpacing(12)
        self.environment = _EnvironmentCard(self)

        # Capped: on a wide screen a card must not stretch its name and its date far apart.
        content = QWidget(self)
        content.setMaximumWidth(CONTENT_MAX_WIDTH)
        left = QVBoxLayout(content)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        left.addWidget(title)
        left.addSpacing(8)
        left.addWidget(subtitle)
        left.addSpacing(28)
        left.addLayout(cards)
        left.addSpacing(32)
        left.addWidget(recent_title)
        left.addSpacing(14)
        left.addWidget(self.recent)
        left.addSpacing(26)
        left.addWidget(self.examples_title)
        left.addSpacing(14)
        left.addLayout(self._examples_row)
        left.addStretch(1)
        self.examples_title.hide()  # until set_examples

        self._layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._layout.setContentsMargins(*PAGE_MARGINS)
        self._layout.setSpacing(24)
        self._layout.addWidget(content)
        self._layout.addWidget(self.environment, 0, Qt.AlignmentFlag.AlignTop)
        self._layout.addStretch()
        self._content = content

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        wide = event.size().width() >= 1050
        direction = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self._layout.direction() != direction:
            self._layout.setDirection(direction)
            self._layout.setStretchFactor(self._content, int(wide))
            self._layout.setStretch(2, int(not wide))

    def set_examples(self, examples: Iterable[ExampleDefinition]) -> list[_ExampleCard]:
        for card in self.example_cards:
            card.deleteLater()
        self.example_cards = [_ExampleCard(example, self) for example in examples]
        for card in self.example_cards:
            self._examples_row.addWidget(card, 1)
        self.examples_title.setVisible(bool(self.example_cards))
        return self.example_cards


class _ProjectsPage(QWidget):
    def __init__(self, view: WelcomeView) -> None:
        super().__init__(view)
        title = QLabel(self)
        live(title.setText, N_("Проєкти"))
        title.setObjectName("welcomeTitle")
        self.new_button = QPushButton(label_icon("plus", palette.ACCENT_TEXT), "", self)
        live(self.new_button.setText, N_("Новий проєкт"))
        self.new_button.setProperty("buttonRole", "primary")
        self.open_button = QPushButton(label_icon("folder-open", palette.TEXT), "", self)
        live(self.open_button.setText, N_("Відкрити…"))
        for button in (self.new_button, self.open_button):
            button.setIconSize(LABEL_ICON_SIZE)
        buttons = QHBoxLayout()
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        self.list = _ProjectList(N_("Проєктів поки немає — створіть перший."), self)
        self.list.setMaximumWidth(CONTENT_MAX_WIDTH)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.list)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(24)
        layout.addWidget(title)
        layout.addLayout(buttons)
        layout.addWidget(scroll, 1)


class WelcomeView(QWidget):
    new_project_requested = Signal()
    open_project_requested = Signal()
    open_recent_requested = Signal(object)  # Path
    reveal_recent_requested = Signal(object)  # Path
    remove_recent_requested = Signal(object)  # Path
    example_requested = Signal(str)  # example id
    check_tools_requested = Signal()
    tools_dialog_requested = Signal()
    change_projects_directory_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomeView")

        sidebar = QFrame(self)
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        logo = icon_label("cpu", 64, palette.ACCENT_BRIGHT, sidebar)
        name = QLabel("NightExpress", sidebar)
        name.setObjectName("sidebarTitle")
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 44, 10, 20)
        side.setSpacing(6)
        side.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        side.addSpacing(4)
        side.addWidget(name, 0, Qt.AlignmentFlag.AlignHCenter)
        side.addSpacing(44)
        for key, icon_name, text in (
            (HOME, "house", N_("Початок")),
            (PROJECTS, "folder", N_("Проєкти")),
            (SETTINGS, "settings", N_("Налаштування")),
        ):
            button = QPushButton(label_icon(icon_name, palette.TEXT_MUTED), "", sidebar)
            live(button.setText, text)
            button.setObjectName("navButton")
            button.setIconSize(LABEL_ICON_SIZE)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.nav_group.addButton(button)
            self.nav_buttons[key] = button
            side.addWidget(button)
        side.addStretch(1)

        self.home = _HomePage(self)
        self.projects = _ProjectsPage(self)
        self.settings = AppSettingsPanel(self, welcome=True)
        self.pages = QStackedWidget(self)
        self._pages: dict[str, QWidget] = {PROJECTS: self.projects}
        for key, page in ((HOME, self.home), (SETTINGS, self.settings)):
            scroll = QScrollArea(self.pages)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self._pages[key] = scroll
        for page in self._pages.values():
            self.pages.addWidget(page)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(self.pages, 1)

        for source in (self.home.new_card.clicked, self.projects.new_button.clicked):
            source.connect(self.new_project_requested)
        for source in (self.home.open_card.clicked, self.projects.open_button.clicked):
            source.connect(self.open_project_requested)
        for project_list in (self.home.recent, self.projects.list):
            project_list.open_requested.connect(self.open_recent_requested)
            project_list.reveal_requested.connect(self.reveal_recent_requested)
            project_list.remove_requested.connect(self.remove_recent_requested)
        self.home.environment.check_requested.connect(self.tools_dialog_requested)
        self.settings.check_tools_requested.connect(self.check_tools_requested)
        self.settings.projects_directory_requested.connect(self.change_projects_directory_requested)
        self.show_page(HOME)
        self.set_checking(True)

    # --- navigation ------------------------------------------------------------------------

    def show_page(self, key: str) -> None:
        self.pages.setCurrentWidget(self._pages[key])
        self.nav_buttons[key].setChecked(True)

    @property
    def current_page(self) -> str:
        return next(k for k, b in self.nav_buttons.items() if b.isChecked())

    # --- data ------------------------------------------------------------------------------

    def set_recent_projects(self, projects: Iterable[RecentProject]) -> None:
        items = list(projects)
        self.home.recent.set_projects(items[:3])
        self.projects.list.set_projects(items)

    def set_examples(self, examples: Iterable[ExampleDefinition]) -> None:
        for card in self.home.set_examples(examples):
            card.clicked.connect(lambda i=card.example_id: self.example_requested.emit(i))

    def set_checking(self, checking: bool) -> None:
        self.settings.set_checking(checking)
        if checking:
            self.home.environment.set_checking()

    def set_tool_statuses(self, statuses: Iterable[ToolStatus]) -> bool:
        items = list(statuses)
        ready = self.home.environment.set_statuses(items)
        self.settings.set_statuses(items)
        self.set_checking(False)
        return ready

    def set_projects_directory(self, path: Path) -> None:
        self.settings.set_projects_directory(path)
