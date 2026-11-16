"""Main window: the start screen or the editor of the open project.

The window only renders state and turns user actions into intent signals; controllers decide
what happens. Edit-menu actions are the exception: they act on the editor directly.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QProcess, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, Text, _, live, ngettext, pgettext, redo_on_switch, resolve
from nightexpress.models.entities import (
    BoardDefinition,
    ChipDefinition,
    ExampleDefinition,
    RecentProject,
    UploadMethod,
)
from nightexpress.models.entities.font import FontArea
from nightexpress.models.state import Screen
from nightexpress.views import formatting
from nightexpress.views.board_picker import SearchFunction
from nightexpress.views.dialogs.about_dialog import AboutDialog
from nightexpress.views.dialogs.board_chooser import BoardChooserDialog
from nightexpress.views.dialogs.boot_mode_dialog import BootModeDialog
from nightexpress.views.dialogs.quick_open import QuickOpenDialog
from nightexpress.views.dialogs.settings_dialog import SettingsDialog
from nightexpress.views.dialogs.shortcuts_dialog import ShortcutsDialog
from nightexpress.views.editor.editor_tabs import EditorTabsView
from nightexpress.views.editor.monaco_editor import EditorAction, MonacoEditor
from nightexpress.views.file_tree_view import FileTreeView
from nightexpress.views.header_bar import HeaderBar
from nightexpress.views.icons import badged_icon, icon
from nightexpress.views.panels.log_toolbar import LogToolbar
from nightexpress.views.panels.output_panel import OutputPanel
from nightexpress.views.panels.problems_panel import ProblemsPanel
from nightexpress.views.panels.rtt_panel import RttPanel
from nightexpress.views.panels.target_panel import TargetPanel
from nightexpress.views.panels.terminal_panel import TerminalView
from nightexpress.views.panels.uart_panel import UartPanel
from nightexpress.views.theme import palette
from nightexpress.views.tips import Tip, TipTour, widget_rect
from nightexpress.views.toast import Toast, ToastHost, ToastKind
from nightexpress.views.toolbar_view import ToolbarView
from nightexpress.views.welcome_view import WelcomeView
from nightexpress.views.widgets import (
    ActivityBar,
    ClickableLabel,
    Panel,
    Separator,
    Spinner,
    icon_label,
    set_icon,
)

# Control+Tab on every platform: Qt calls the macOS Control key "Meta".
NEXT_TAB_SHORTCUT = "Meta+Tab" if sys.platform == "darwin" else "Ctrl+Tab"
COMMAND_DEBOUNCE_S = 0.3  # a shortcut may reach both Qt and Monaco; act on it once
# Commands pressed in series (and auto-repeated while the key is held): only the echo of the
# same key press, which arrives within milliseconds, is dropped.
REPEATABLE_COMMANDS = frozenset(
    {"zoom_in", "zoom_out", "next_tab", "previous_tab", "next_problem", "previous_problem"}
)
REPEATABLE_DEBOUNCE_S = 0.02

PROBLEMS_TAB, BUILD_TAB, FLASH_TAB, UART_TAB, RTT_TAB, TERMINAL_TAB = range(6)
# How the bottom tabs are remembered between runs (by name: indexes move when a tab is added).
TAB_NAMES = ("problems", "build", "flash", "uart", "rtt", "terminal")
# The tabs before the RTT tab existed, for layouts saved by index.
_LEGACY_TABS = (PROBLEMS_TAB, BUILD_TAB, FLASH_TAB, UART_TAB, TERMINAL_TAB)
TAB_ICONS = ("triangle-alert", "hammer", "download", "square-terminal", "file-text", "terminal")


class SaveChoice(Enum):
    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"


LAYOUT_VERSION = 2  # bump when the default panel sizes change: older saved sizes are dropped
EDITOR_MIN_WIDTH = 520
EDITOR_MIN_HEIGHT = 320  # the editor row; the bottom panel gives way before it


def native_shortcut(sequence: QKeySequence | str) -> str:
    """⌘B on macOS, Ctrl+B elsewhere."""
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


_MAC = sys.platform == "darwin"
# Keys handled inside Monaco and xterm.js (not menu actions), shown on the shortcuts page.
EDITOR_SHORTCUTS = [
    (N_("Знайти"), native_shortcut("Ctrl+F")),
    (N_("Замінити"), native_shortcut("Ctrl+Alt+F" if _MAC else "Ctrl+H")),
    (N_("Перейти до рядка"), native_shortcut("Meta+G" if _MAC else "Ctrl+G")),
    (N_("Закоментувати рядок"), native_shortcut("Ctrl+/")),
    (N_("Наступне таке саме слово"), native_shortcut("Ctrl+D")),
    (
        N_("Перемістити рядок вгору / вниз"),
        native_shortcut("Alt+Up") + " / " + native_shortcut("Alt+Down"),
    ),
    (N_("Палітра команд"), "F1"),
]
TERMINAL_SHORTCUTS = [
    (N_("Перервати команду"), native_shortcut("Meta+C" if _MAC else "Ctrl+C")),
    (N_("Копіювати виділене"), native_shortcut("Ctrl+C" if _MAC else "Ctrl+Shift+C")),
    (N_("Вставити"), native_shortcut("Ctrl+V" if _MAC else "Ctrl+Shift+V")),
    *([(N_("Очистити"), native_shortcut("Ctrl+K"))] if _MAC else []),
]


class MainWindow(QMainWindow):
    new_project_requested = Signal()
    example_requested = Signal(str)  # example id: "File → Examples"
    open_project_requested = Signal()
    open_recent_requested = Signal(object)  # Path
    close_project_requested = Signal()
    new_file_requested = Signal()
    quick_open_requested = Signal()  # "Go to file…"
    file_chosen = Signal(object)  # Path picked in the quick-open list
    save_requested = Signal()
    save_all_requested = Signal()
    format_requested = Signal()  # Edit → Format document (rustfmt)
    verify_requested = Signal()  # "Build"
    upload_requested = Signal()  # "Build and flash"
    serial_monitor_requested = Signal()
    clean_requested = Signal()
    change_board_requested = Signal()
    add_peripheral_requested = Signal()  # Build → Add peripheral…
    stop_requested = Signal()
    reveal_project_requested = Signal()
    external_terminal_requested = Signal()  # open the OS terminal in the project folder
    check_tools_requested = Signal()
    tools_dialog_requested = Signal()  # the dialog with every tool and how to install it
    # Cmd+= / Cmd+- / Cmd+0: the area that has the keyboard focus, and +1 / -1 / 0 (= default)
    font_size_step_requested = Signal(object, int)
    home_requested = Signal()
    settings_requested = Signal()
    help_requested = Signal(str)  # topic id
    about_requested = Signal()

    def __init__(
        self,
        app_name: str,
        version: str,
        parent: QWidget | None = None,
        editor: MonacoEditor | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MainWindow")
        self._app_name = app_name
        self._version = version
        self.about_requested.connect(self.show_about)
        self.setWindowTitle(app_name)
        self.setMinimumSize(1100, 700)
        self.resize(1536, 1000)
        self._last_command: tuple[str, float] = ("", 0.0)
        self.tour: TipTour | None = None  # the first-run tips, while shown
        self._main_sizes_before_maximize: list[int] | None = None
        self.confirm_close: Callable[[], bool] | None = None  # set by the controller

        self.menu_bar = QMenuBar(self)
        self._build_menus()
        self.header = HeaderBar(app_name, self.menu_bar, self)
        self.header.home_button.clicked.connect(self.home_requested)
        self.setMenuWidget(self.header)

        self.welcome = WelcomeView(self)
        self.welcome.new_project_requested.connect(self.new_project_requested)
        self.welcome.open_project_requested.connect(self.open_project_requested)
        self.welcome.open_recent_requested.connect(self.open_recent_requested)
        self.welcome.check_tools_requested.connect(self.check_tools_requested)
        self.welcome.tools_dialog_requested.connect(self.tools_dialog_requested)
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.application.check_tools_requested.connect(self.check_tools_requested)
        self.welcome.example_requested.connect(self.example_requested)

        self.editor_page = self._build_editor_page(editor)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.editor_page)
        self.setCentralWidget(self.stack)

        self._build_status_bar(version)
        self.toasts = ToastHost(self, lambda: self.statusBar().height())
        self.set_screen(Screen.WELCOME)
        self.set_project(None, None)

    # --- construction ----------------------------------------------------------------------

    def _build_editor_page(self, editor: MonacoEditor | None) -> QWidget:
        page = QWidget(self)
        page.setObjectName("editorPage")

        self.toolbar = ToolbarView(page)
        self.toolbar.verify_requested.connect(lambda: self._command("verify"))
        self.toolbar.upload_requested.connect(lambda: self._command("upload"))
        self.toolbar.stop_requested.connect(self.stop_requested)
        self.activity_bar = ActivityBar(page)
        self.toolbar.open_project_requested.connect(self.open_project_requested)
        self.toolbar.project_selected.connect(self.open_recent_requested)

        self.file_tree = FileTreeView(page)
        self.project_panel = Panel(N_("Проєкт"), self.file_tree, page)
        self.new_file_button = self.project_panel.add_tool_button("plus", N_("Новий файл"))
        self.open_folder_button = self.project_panel.add_tool_button(
            "folder", N_("Відкрити проєкт")
        )
        self.new_file_button.clicked.connect(self.new_file_requested)
        self.open_folder_button.clicked.connect(self.open_project_requested)
        self.project_menu_button = self.project_panel.add_tool_button("ellipsis", N_("Ще"))
        self.project_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.project_menu = QMenu(self.project_menu_button)
        self.show_service_action = self.project_menu.addAction("")
        live(self.show_service_action.setText, N_("Показати службові файли"))
        self.show_service_action.setCheckable(True)
        self.show_service_action.setChecked(self.file_tree.shows_service_files)
        self.show_service_action.setToolTip("Cargo.lock, .cargo, .gitignore, rust-toolchain.toml")
        self.show_service_action.toggled.connect(self.file_tree.set_show_service_files)
        self.file_tree.show_service_changed.connect(self.show_service_action.setChecked)
        self.show_build_dir_action = self.project_menu.addAction("")
        live(self.show_build_dir_action.setText, N_("Показати теку збірки (target)"))
        self.show_build_dir_action.setCheckable(True)
        self.show_build_dir_action.setChecked(self.file_tree.shows_build_dir)
        live(
            self.show_build_dir_action.setToolTip,
            N_("Результати збірки Cargo: файли .elf, .bin та проміжні"),
        )
        self.show_build_dir_action.toggled.connect(self.file_tree.set_show_build_dir)
        self.file_tree.show_build_dir_changed.connect(self.show_build_dir_action.setChecked)
        live(
            self.project_menu.addAction("", self.file_tree.collapse_folders).setText,
            N_("Згорнути все"),
        )
        self.project_menu_button.setMenu(self.project_menu)
        self.file_tree.path_copied.connect(
            lambda text: self.show_message(_("Скопійовано: {text}").format(text=text))
        )
        self.file_tree.reveal_requested.connect(
            lambda path: self.reveal_in_file_manager(path, select=True)
        )

        self.editor_tabs = EditorTabsView(page, editor)
        self.editor_tabs.command_requested.connect(self._command)
        self.editor_tabs.cursor_moved.connect(self.set_cursor_position)
        self.editor_tabs.editing_changed.connect(
            lambda editing: self._show_cursor_position(
                editing and self.current_screen is Screen.EDITOR
            )
        )
        self.editor_tabs.reveal_in_tree_requested.connect(self.reveal_in_tree)
        self.editor_tabs.markdown_file_requested.connect(self.file_chosen)
        self.editor_tabs.path_copied.connect(
            lambda text: self.show_message(_("Скопійовано: {text}").format(text=text))
        )
        self.quick_open = QuickOpenDialog(self)
        self.quick_open.file_chosen.connect(self.file_chosen)
        self.editor_panel = QFrame(page)
        self.editor_panel.setObjectName("panel")
        editor_layout = QVBoxLayout(self.editor_panel)
        editor_layout.setContentsMargins(1, 1, 1, 1)
        editor_layout.addWidget(self.editor_tabs)

        self.target_view = TargetPanel(page)
        self.target_view.help_requested.connect(self.help_requested)
        self.target_view.pin_copied.connect(
            lambda pin: self.show_message(_("Скопійовано: {text}").format(text=pin))
        )
        self.target_panel = Panel(N_("Ціль"), self.target_view, page)

        self.problems_panel = ProblemsPanel(page)
        self.problems_panel.counts_changed.connect(self.set_problem_counts)
        self.output_panel = OutputPanel(page)
        self.flash_panel = OutputPanel(page)
        self.uart_panel = UartPanel(page)
        self.rtt_panel = RttPanel(page)
        self.problems_panel.code_help_requested.connect(self.open_url)
        self.terminal_panel = TerminalView(page)
        self.terminal_panel.command_requested.connect(self._command)
        self.terminal_panel.copied.connect(lambda: self.show_message(_("Скопійовано")))
        self.bottom_tabs = QTabWidget(page)
        self.bottom_tabs.setObjectName("bottomTabs")
        self.bottom_tabs.setDocumentMode(True)
        # macOS draws a light "base" line along a document-mode tab bar (and again, at another
        # height, behind the corner toolbar); the design has only the pane's own top border.
        self.bottom_tabs.tabBar().setDrawBase(False)
        self.bottom_tabs.setIconSize(QSize(16, 16))
        for widget, icon_name, title in (
            (self.problems_panel, "triangle-alert", N_("Проблеми")),
            (self.output_panel, "hammer", N_("Збірка")),
            (self.flash_panel, "download", N_("Прошивання")),
            (self.uart_panel, "square-terminal", "UART"),
            (self.rtt_panel, TAB_ICONS[RTT_TAB], "RTT"),
            (self.terminal_panel, "terminal", N_("Термінал")),
        ):
            index = self.bottom_tabs.addTab(widget, icon(icon_name, palette.TEXT_MUTED), "")
            live(self.bottom_tabs.setTabText, title, index)
        self.bottom_tabs.setCurrentIndex(BUILD_TAB)
        self._tab_badges: dict[int, bool] = {}  # tab index -> is it an error
        self.bottom_tabs.currentChanged.connect(self._clear_tab_badge)
        self._logs = {
            BUILD_TAB: self.output_panel,
            FLASH_TAB: self.flash_panel,
            UART_TAB: self.uart_panel.output,
            RTT_TAB: self.rtt_panel.output,
        }
        self.log_toolbar = LogToolbar(self.bottom_tabs)
        self.log_toolbar.clear_requested.connect(self._clear_current_log)
        self.log_toolbar.copied.connect(lambda: self.show_message(_("Журнал скопійовано")))
        # Not a corner widget: the macOS style shifts corner widgets ~13 px down, below the tab
        # titles. The toolbar sits on the right end of the tab row, placed by _place_log_toolbar.
        self.bottom_tabs.installEventFilter(self)
        self.log_toolbar.installEventFilter(self)
        self.bottom_tabs.currentChanged.connect(self._retarget_log_toolbar)
        self._retarget_log_toolbar(self.bottom_tabs.currentIndex())
        for log in self._logs.values():
            find = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), log)
            find.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            find.activated.connect(self.log_toolbar.open_search)
        # A double click on the tab row gives the panel the whole height (and back).
        self.bottom_tabs.tabBar().installEventFilter(self)
        self.bottom_panel = QFrame(page)
        self.bottom_panel.setObjectName("panel")
        bottom_layout = QVBoxLayout(self.bottom_panel)
        bottom_layout.setContentsMargins(1, 1, 1, 1)
        bottom_layout.addWidget(self.bottom_tabs)

        self.top_splitter = QSplitter(Qt.Orientation.Horizontal, page)
        for widget in (self.project_panel, self.editor_panel, self.target_panel):
            self.top_splitter.addWidget(widget)
        self.top_splitter.setSizes([260, 960, 300])
        self.top_splitter.setStretchFactor(1, 1)
        # The editor is the point of the window: in a small window the side panels give way
        # first, down to their own minimums.
        self.project_panel.setMinimumWidth(200)
        self.editor_panel.setMinimumWidth(EDITOR_MIN_WIDTH)
        self.target_panel.setMinimumWidth(240)
        self.main_splitter = QSplitter(Qt.Orientation.Vertical, page)
        self.main_splitter.addWidget(self.top_splitter)
        self.main_splitter.addWidget(self.bottom_panel)
        self.main_splitter.setSizes([640, 240])
        self.main_splitter.setStretchFactor(0, 1)
        self.top_splitter.setMinimumHeight(EDITOR_MIN_HEIGHT)
        for splitter in (self.top_splitter, self.main_splitter):
            splitter.setHandleWidth(8)
            splitter.setChildrenCollapsible(False)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.setSpacing(0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.activity_bar)
        layout.addSpacing(4)
        layout.addWidget(self.main_splitter, 1)
        return page

    def _retarget_log_toolbar(self, index: int) -> None:
        tool_targets = {PROBLEMS_TAB: self.problems_panel, TERMINAL_TAB: self.terminal_panel}
        self.log_toolbar.set_target(self._logs.get(index) or tool_targets.get(index))

    def _clear_current_log(self) -> None:
        index = self.bottom_tabs.currentIndex()
        if index == UART_TAB:
            self.uart_panel.clear_requested.emit()  # the monitor model owns that text
            self.uart_panel.clear_output()
        elif index == TERMINAL_TAB:
            self.terminal_panel.clear()
        elif index in self._logs:
            self._logs[index].clear()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if watched is self.bottom_tabs.tabBar() and kind is QEvent.Type.MouseButtonDblClick:
            self.set_bottom_maximized(not self.bottom_maximized)
            return True
        if (watched is self.bottom_tabs and kind in (QEvent.Type.Resize, QEvent.Type.Show)) or (
            watched is self.log_toolbar and kind is QEvent.Type.LayoutRequest  # search opened
        ):
            self._place_log_toolbar()
        return super().eventFilter(watched, event)

    def _place_log_toolbar(self) -> None:
        """Right end of the bottom tab row, vertically centred on the tab titles."""
        tabs = self.bottom_tabs.tabBar()
        hint = self.log_toolbar.sizeHint()
        left = self.bottom_tabs.width() - hint.width()
        # In a narrow window the tabs give way (scroll buttons) instead of hiding under it.
        tabs.setMaximumWidth(max(left, 0))
        self.log_toolbar.setGeometry(left, 0, hint.width(), max(tabs.height(), hint.height()))
        self.log_toolbar.raise_()

    @property
    def bottom_maximized(self) -> bool:
        return self.top_splitter.isHidden()

    def set_bottom_maximized(self, maximized: bool) -> None:
        if maximized and not self.bottom_maximized:
            # The hidden row reports a height of 0: keep the real one for layout_state().
            self._main_sizes_before_maximize = self.main_splitter.sizes()
        self.top_splitter.setVisible(not maximized)
        if maximized:
            self.bottom_panel.setVisible(True)
            self.actions_by_name["toggle_bottom"].setChecked(True)

    def _build_status_bar(self, version: str) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self._version_label = QLabel(f"{self._app_name} {version}", self)
        self._version_separator = Separator(parent=self)
        self.tools_icon = icon_label("rust", 20, palette.TEXT, self)
        self.tools_status = ClickableLabel("", self)
        live(self.tools_status.setText, N_("Rust: перевірка…"))
        self.tools_status.set_clickable(True)
        live(self.tools_status.setToolTip, N_("Показати стан інструментів Rust"))
        self.tools_status.clicked.connect(self.tools_dialog_requested)
        self.activity_spinner = Spinner(16, self)
        self.activity_status = ClickableLabel("", self)
        self.activity_status.setObjectName("activityStatus")
        self.activity_status.clicked.connect(self._on_activity_clicked)
        self._activity_tab: int | None = None
        self._activity_timer = QTimer(self, singleShot=True)
        self._activity_timer.timeout.connect(self._clear_activity)

        self.connection_icon = icon_label("link", 18, palette.TEXT_MUTED, self)
        self.connection_dot = QLabel("●", self)
        self.connection_dot.setObjectName("connectionDot")
        self.connection_status = ClickableLabel("—", self)
        self.connection_status.setObjectName("connectionStatus")
        self.connection_status.set_clickable(True)
        live(self.connection_status.setToolTip, N_("Вибрати пристрій для прошивання"))
        self.connection_status.clicked.connect(self.show_device_menu)
        self._problems_separator = Separator(parent=self)
        self.problems_icon = icon_label("circle-check", 20, palette.SUCCESS, self)
        self.problems_status = ClickableLabel("", self)
        live(self.problems_status.setText, N_("0 помилок"))
        self.problems_status.set_clickable(True)
        live(self.problems_status.setToolTip, N_("Показати вкладку «Проблеми»"))
        self.problems_status.clicked.connect(self.show_problems)
        # "Ln 17, Col 9" of the active file; hidden while no file is open.
        self.cursor_status = ClickableLabel("", self)
        self.cursor_status.set_clickable(True)
        live(self.cursor_status.setToolTip, N_("Перейти до рядка…"))
        self.cursor_status.clicked.connect(
            lambda: self.editor_tabs.trigger_action(EditorAction.GO_TO_LINE)
        )
        self._cursor_separator = Separator(parent=self)

        left = QWidget(self)
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(10, 4, 0, 4)
        left_layout.setSpacing(10)
        for widget in (
            self._version_label,
            self._version_separator,
            self.tools_icon,
            self.tools_status,
        ):
            left_layout.addWidget(widget)
        bar.addWidget(left)

        right = QWidget(self)
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 4, 10, 4)
        right_layout.setSpacing(10)
        right_layout.addWidget(self.activity_spinner)
        right_layout.addWidget(self.activity_status)
        right_layout.addSpacing(10)
        for widget in (
            self.cursor_status,
            self._cursor_separator,
            self.connection_icon,
            self.connection_dot,
            self.connection_status,
            self._problems_separator,
            self.problems_icon,
            self.problems_status,
        ):
            right_layout.addWidget(widget)
        bar.addPermanentWidget(right)
        self.set_connection(None)
        self._show_cursor_position(False)

    def _action(
        self,
        menu: QMenu,
        text: Text,
        slot: object,
        shortcut: QKeySequence | QKeySequence.StandardKey | str | None = None,
        role: QAction.MenuRole | None = None,
    ) -> QAction:
        action = QAction(self)
        live(action.setText, text)
        # macOS hides shortcuts in every menu except the system menu bar; ours is in the window.
        action.setShortcutVisibleInContextMenu(True)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if role is not None:
            action.setMenuRole(role)
        action.triggered.connect(slot)  # type: ignore[arg-type]
        menu.addAction(action)
        return action

    def _menu(self, parent: QMenuBar | QMenu, title: Text) -> QMenu:
        menu = parent.addMenu("")
        live(menu.setTitle, title)
        return menu

    def _build_menus(self) -> None:
        bar = self.menu_bar
        a: dict[str, QAction] = {}
        self.actions_by_name = a

        file_menu = self._menu(bar, N_("Файл"))
        a["new"] = self._action(
            file_menu, N_("Новий проєкт…"), self.new_project_requested, QKeySequence.StandardKey.New
        )
        a["open"] = self._action(
            file_menu,
            N_("Відкрити проєкт…"),
            self.open_project_requested,
            QKeySequence.StandardKey.Open,
        )
        self.recent_menu = self._menu(file_menu, N_("Недавні проєкти"))
        self.examples_menu = self._menu(file_menu, N_("Приклади"))
        self.examples_menu.setToolTipsVisible(True)  # descriptions of the examples
        self.set_examples([], None)
        file_menu.addSeparator()
        a["new_file"] = self._action(file_menu, N_("Новий файл…"), self.new_file_requested)
        a["quick_open"] = self._action(
            file_menu, N_("Перейти до файлу…"), lambda: self._command("quick_open"), "Ctrl+P"
        )
        a["save"] = self._action(
            file_menu, N_("Зберегти"), lambda: self._command("save"), QKeySequence.StandardKey.Save
        )
        a["save_all"] = self._action(
            file_menu, N_("Зберегти все"), lambda: self._command("save_all"), "Ctrl+Shift+S"
        )
        a["close_tab"] = self._action(
            file_menu, N_("Закрити вкладку"), lambda: self._command("close_tab"), "Ctrl+W"
        )
        a["close_project"] = self._action(
            file_menu, N_("Закрити проєкт"), self.close_project_requested
        )
        file_menu.addSeparator()
        a["settings"] = self._action(
            file_menu, N_("Налаштування…"), self.settings_requested, "Ctrl+,",
            QAction.MenuRole.PreferencesRole,
        )  # fmt: skip
        a["quit"] = self._action(
            file_menu,
            N_("Вихід"),
            self.close,
            QKeySequence.StandardKey.Quit,
            QAction.MenuRole.QuitRole,
        )

        self.edit_menu = self._menu(bar, N_("Правка"))
        # No shortcuts here: Monaco handles the editing keys itself when it has focus.
        for text, action in (
            (lambda: pgettext("undo", "Скасувати"), EditorAction.UNDO),
            (N_("Повторити"), EditorAction.REDO),
            (None, None),
            (N_("Вирізати"), EditorAction.CUT),
            (N_("Копіювати"), EditorAction.COPY),
            (N_("Вставити"), EditorAction.PASTE),
            (N_("Виділити все"), EditorAction.SELECT_ALL),
            (None, None),
            (N_("Знайти…"), EditorAction.FIND),
            (N_("Замінити…"), EditorAction.REPLACE),
            (N_("Перейти до рядка…"), EditorAction.GO_TO_LINE),
            (N_("Закоментувати рядок"), EditorAction.TOGGLE_COMMENT),
            (None, None),
            (N_("Палітра команд…"), EditorAction.COMMAND_PALETTE),
        ):
            if text is None or action is None:
                self.edit_menu.addSeparator()
                continue
            a[f"edit:{action.name.lower()}"] = self._action(
                self.edit_menu,
                text,
                lambda _=False, act=action: self.editor_tabs.trigger_action(act),
            )
        self.edit_menu.addSeparator()
        # rustfmt, not Monaco: Monaco has no Rust formatter. The key reaches us from Monaco too.
        a["format"] = self._action(
            self.edit_menu,
            N_("Форматувати документ"),
            lambda: self._command("format"),
            "Shift+Alt+F",
        )

        self.view_menu = self._menu(bar, N_("Перегляд"))
        a["home"] = self._action(self.view_menu, N_("Початковий екран"), self.home_requested)
        a["focus_tree"] = self._action(
            self.view_menu,
            N_("Перейти до дерева файлів"),
            lambda: self._command("focus_tree"),
            "Ctrl+1",
        )
        self.view_menu.addSeparator()
        for key, text in (
            ("toggle_project", N_("Панель «Проєкт»")),
            ("toggle_target", N_("Панель «Ціль»")),
            ("toggle_bottom", N_("Нижня панель")),
        ):
            a[key] = self._action(
                self.view_menu, text, lambda checked, k=key: self._toggle_panel(k, checked)
            )
            a[key].setCheckable(True)
            a[key].setChecked(True)
        a["toggle_bottom"].setShortcut(QKeySequence("Ctrl+J"))
        self.view_menu.addSeparator()
        a["next_tab"] = self._action(
            self.view_menu,
            N_("Наступна вкладка"),
            lambda: self._command("next_tab"),
            NEXT_TAB_SHORTCUT,
        )
        a["previous_tab"] = self._action(
            self.view_menu,
            N_("Попередня вкладка"),
            lambda: self._command("previous_tab"),
            f"Shift+{NEXT_TAB_SHORTCUT}",
        )
        self.view_menu.addSeparator()
        for key, text, tab in (("show_problems", N_("Проблеми"), PROBLEMS_TAB),
                               ("show_build", N_("Збірка"), BUILD_TAB),
                               ("show_flash", N_("Прошивання"), FLASH_TAB),
                               ("show_uart", "UART", UART_TAB),
                               ("show_rtt", "RTT", RTT_TAB),
                               ("show_terminal", N_("Термінал"), TERMINAL_TAB)):  # fmt: skip
            a[key] = self._action(
                self.view_menu, text, lambda _=False, i=tab: self.show_bottom_tab(i)
            )
        self.view_menu.addSeparator()
        a["next_problem"] = self._action(
            self.view_menu, N_("Наступна проблема"), lambda: self._command("next_problem"), "F8"
        )
        a["previous_problem"] = self._action(
            self.view_menu,
            N_("Попередня проблема"),
            lambda: self._command("previous_problem"),
            "Shift+F8",
        )
        self.view_menu.addSeparator()
        a["zoom_in"] = self._action(
            self.view_menu, N_("Збільшити шрифт"), lambda: self._command("zoom_in"), "Ctrl+="
        )
        # Cmd+= is what people press; Cmd++ (with Shift) and the keypad plus work too.
        a["zoom_in"].setShortcuts([QKeySequence("Ctrl+="), QKeySequence("Ctrl++")])
        a["zoom_out"] = self._action(
            self.view_menu, N_("Зменшити шрифт"), lambda: self._command("zoom_out"), "Ctrl+-"
        )
        a["zoom_reset"] = self._action(
            self.view_menu,
            N_("Звичайний розмір шрифту"),
            lambda: self._command("zoom_reset"),
            "Ctrl+0",
        )

        self.build_menu = self._menu(bar, N_("Збірка"))
        a["verify"] = self._action(
            self.build_menu, N_("Зібрати"), lambda: self._command("verify"), "Ctrl+B"
        )
        a["upload"] = self._action(
            self.build_menu, N_("Зібрати й прошити"), lambda: self._command("upload"), "Ctrl+U"
        )
        a["stop"] = self._action(self.build_menu, N_("Зупинити"), self.stop_requested, "Ctrl+.")
        a["stop"].setEnabled(False)
        self.build_menu.addSeparator()
        a["clean"] = self._action(self.build_menu, N_("Очистити збірку"), self.clean_requested)
        a["change_board"] = self._action(
            self.build_menu, N_("Змінити плату…"), self.change_board_requested
        )
        a["add_peripheral"] = self._action(
            self.build_menu, N_("Додати периферію…"), self.add_peripheral_requested
        )
        a["reveal"] = self._action(
            self.build_menu, N_("Відкрити теку проєкту"), self.reveal_project_requested
        )
        a["external_terminal"] = self._action(
            self.build_menu, N_("Відкрити в системному терміналі"), self.external_terminal_requested
        )

        self.debug_menu = self._menu(bar, N_("Налагодження"))
        self.debug_menu.setToolTipsVisible(True)
        a["debug_start"] = self._action(
            self.debug_menu, N_("Почати налагодження (згодом)"), lambda: None, "F5"
        )
        a["debug_start"].setEnabled(False)
        live(
            a["debug_start"].setToolTip,
            N_("Покрокове налагодження через probe-rs з'явиться після MVP"),
        )
        a["serial_monitor"] = self._action(
            self.debug_menu,
            N_("Монітор UART"),
            lambda: self._command("serial_monitor"),
            "Ctrl+Shift+M",
        )

        help_menu = self._menu(bar, N_("Довідка"))
        a["help_stlink"] = self._action(
            help_menu, N_("Як підключити ST-Link"), lambda: self.help_requested.emit("stlink")
        )
        a["help_usb_ttl"] = self._action(
            help_menu, N_("Як підключити USB-TTL"), lambda: self.help_requested.emit("usb_ttl")
        )
        a["help_usb_dfu"] = self._action(
            help_menu, N_("Як прошити через USB (DFU)"), lambda: self.help_requested.emit("usb_dfu")
        )
        a["shortcuts"] = self._action(help_menu, N_("Гарячі клавіші"), self.show_shortcuts)
        a["check_tools"] = self._action(
            help_menu, N_("Перевірити інструменти Rust…"), self.tools_dialog_requested
        )
        help_menu.addSeparator()
        a["about"] = self._action(
            help_menu, N_("Про програму"), self.about_requested, role=QAction.MenuRole.AboutRole
        )

    # --- commands --------------------------------------------------------------------------

    def _command(self, name: str) -> None:
        now = time.monotonic()
        last_name, last_time = self._last_command
        window = REPEATABLE_DEBOUNCE_S if name in REPEATABLE_COMMANDS else COMMAND_DEBOUNCE_S
        if name == last_name and now - last_time < window:
            return
        self._last_command = (name, now)
        handler = {
            "save": self.save_requested.emit,
            "save_all": self.save_all_requested.emit,
            "format": self.format_requested.emit,
            "verify": self.verify_requested.emit,
            "upload": self.upload_requested.emit,
            "serial_monitor": self.serial_monitor_requested.emit,
            "quick_open": self.quick_open_requested.emit,
            "close_tab": self._close_current_tab,
            "next_tab": lambda: self.editor_tabs.select_next(1),
            "previous_tab": lambda: self.editor_tabs.select_next(-1),
            "toggle_bottom": self.actions_by_name["toggle_bottom"].trigger,
            "focus_tree": self.focus_file_tree,
            "zoom_in": lambda: self.font_size_step_requested.emit(self.focused_font_area(), 1),
            "zoom_out": lambda: self.font_size_step_requested.emit(self.focused_font_area(), -1),
            "zoom_reset": lambda: self.font_size_step_requested.emit(self.focused_font_area(), 0),
            "find_in_panel": self.log_toolbar.open_search,  # Cmd+F inside the terminal
            "next_problem": lambda: self.go_to_problem(1),
            "previous_problem": lambda: self.go_to_problem(-1),
        }.get(name)
        if handler is not None:
            handler()

    def _close_current_tab(self) -> None:
        path = self.editor_tabs.current_path
        if path is not None and self.current_screen is Screen.EDITOR:
            self.editor_tabs.tab_close_requested.emit(path)

    def reveal_in_tree(self, path: Path) -> None:
        self.project_panel.setVisible(True)
        self.actions_by_name["toggle_project"].setChecked(True)
        self.file_tree.reveal_path(path)

    def show_quick_open(self, root: Path, files: Sequence[Path]) -> None:
        """Open the quick-open list over the editor; the choice comes back as file_chosen."""
        self.quick_open.open(root, files, self.editor_panel)

    # --- remembered layout (U8.1) -------------------------------------------------------------

    PANEL_KEYS = ("toggle_project", "toggle_target", "toggle_bottom")

    def layout_state(self) -> dict[str, object]:
        """What to remember between runs: panel sizes, hidden panels, the bottom tab."""
        main = self.main_splitter.sizes()
        if self.bottom_maximized:
            # Not the maximized state itself: the next run starts with the editor visible.
            before = self._main_sizes_before_maximize
            main = before if before and all(size > 0 for size in before) else None
        return {
            "version": LAYOUT_VERSION,
            "top": self.top_splitter.sizes(),
            "main": main,
            "panels": {key: self.actions_by_name[key].isChecked() for key in self.PANEL_KEYS},
            "bottom_tab": TAB_NAMES[self.bottom_tabs.currentIndex()],
        }

    def restore_layout(self, state: dict[str, object]) -> None:
        """Apply a layout_state(); anything missing or malformed keeps the default."""

        def sizes(value: object, count: int) -> list[int] | None:
            if (
                isinstance(value, list)
                and len(value) == count
                and all(isinstance(v, int) and v >= 0 for v in value)
                and sum(value) > 0
            ):
                return value
            return None

        # Sizes saved for an older default layout would bring its proportions back.
        current = state.get("version") == LAYOUT_VERSION
        if current and (top := sizes(state.get("top"), 3)) is not None:
            self.top_splitter.setSizes(top)
        if current and (main := sizes(state.get("main"), 2)) is not None:
            self.main_splitter.setSizes(main)
        panels = state.get("panels")
        if isinstance(panels, dict):
            for key in self.PANEL_KEYS:
                visible = panels.get(key)
                if isinstance(visible, bool) and visible != self.actions_by_name[key].isChecked():
                    self.actions_by_name[key].trigger()
        tab = state.get("bottom_tab")
        if isinstance(tab, str) and tab in TAB_NAMES:
            self.bottom_tabs.setCurrentIndex(TAB_NAMES.index(tab))
        elif isinstance(tab, int) and 0 <= tab < len(_LEGACY_TABS):
            self.bottom_tabs.setCurrentIndex(_LEGACY_TABS[tab])

    def _toggle_panel(self, key: str, visible: bool) -> None:
        panel = {
            "toggle_project": self.project_panel,
            "toggle_target": self.target_panel,
            "toggle_bottom": self.bottom_panel,
        }[key]
        panel.setVisible(visible)
        if key == "toggle_bottom" and not visible and self.bottom_maximized:
            self.set_bottom_maximized(False)  # never leave the window with nothing in it

    # --- state rendering -------------------------------------------------------------------

    def set_screen(self, screen: Screen) -> None:
        editor = screen is Screen.EDITOR
        self.stack.setCurrentWidget(self.editor_page if editor else self.welcome)
        self.header.set_above_sidebar(not editor)
        # The start screen has only the menus that do something there ("File", "Help"), and
        # a simpler status bar. A hidden menu's shortcuts are off too.
        for menu in (self.edit_menu, self.view_menu, self.build_menu, self.debug_menu):
            menu.menuAction().setVisible(editor)
        self._version_label.setVisible(not editor)
        self._version_separator.setVisible(not editor)
        for widget in (
            self.activity_spinner,
            self.activity_status,
            self.connection_icon,
            self.connection_dot,
            self.connection_status,
            self._problems_separator,
            self.problems_icon,
            self.problems_status,
        ):
            widget.setVisible(editor)
        self._show_cursor_position(editor and self.editor_tabs.is_editing)
        self._refresh_tools_icon()

    @property
    def current_screen(self) -> Screen:
        return Screen.EDITOR if self.stack.currentWidget() is self.editor_page else Screen.WELCOME

    def set_project(self, name: str | None, root: Path | None) -> None:
        if root is None and self.settings_dialog.isVisible():
            self.settings_dialog.reject()
        self.setWindowTitle(f"{name} — {self._app_name}" if name else self._app_name)
        self.file_tree.set_root(root)
        self.problems_panel.set_project_dir(root)
        self.editor_tabs.markdown_preview.set_project_root(root)
        self.target_view.set_last_upload(None)
        self._has_project = root is not None
        for key in ("save", "save_all", "close_project", "verify", "upload", "reveal", "clean",
                    "change_board", "add_peripheral", "external_terminal", "format",
                    "new_file", "quick_open", "close_tab", "next_tab", "previous_tab"):  # fmt: skip
            self.actions_by_name[key].setEnabled(root is not None)

    def set_board(self, board: BoardDefinition | None, chip: ChipDefinition | None) -> None:
        """Show the project's board in the toolbar and the "Target" panel."""
        self.toolbar.set_current_board(board.id if board else None)
        self.toolbar.set_targets(
            [chip.target_triple] if chip else [], chip.target_triple if chip else None
        )
        self.target_view.set_board(board, chip)

    def set_font_size(self, area: FontArea, size: int) -> None:
        """Show the text of ``area`` in ``size`` points, as the settings say."""
        if area is FontArea.EDITOR:
            self.editor_tabs.editor.set_font_size(size)
            self.editor_tabs.markdown_preview.set_font_size(size)
            return
        # Monospace fonts in xterm and Qt look a point larger than Monaco's at the same size,
        # so the same number in the settings looks the same in every area.
        size -= 1
        if area is FontArea.TERMINAL:
            self.terminal_panel.set_font_size(size)
            return
        logs = (self.output_panel, self.flash_panel, self.rtt_panel.output)
        for widget in (self.uart_panel.output,) if area is FontArea.UART else logs:
            font = widget.font()
            font.setPointSize(size)
            widget.setFont(font)

    def focused_font_area(self) -> FontArea:
        """Whose text the font-size keys act on: where the keyboard focus is, else the editor."""
        focus = QApplication.focusWidget()
        for area, owner in (
            (FontArea.TERMINAL, self.terminal_panel),
            (FontArea.UART, self.uart_panel),
            (FontArea.LOGS, self.output_panel),
            (FontArea.LOGS, self.flash_panel),
            (FontArea.LOGS, self.rtt_panel),
        ):
            if focus is not None and (focus is owner or owner.isAncestorOf(focus)):
                return area
        return FontArea.EDITOR

    def set_projects_directory(self, path: Path) -> None:
        self.welcome.set_projects_directory(path)
        self.settings_dialog.application.set_projects_directory(path)

    def set_recent_projects(self, projects: Iterable[RecentProject]) -> None:
        items = list(projects)
        self.welcome.set_recent_projects(items)
        current = self.file_tree.root
        listed = [(p.path, p.name) for p in items]
        if current is not None and all(path != current for path, _ in listed):
            listed.insert(0, (current, current.name))
        self.toolbar.set_projects(listed, current)
        self.recent_menu.clear()
        for project in items:
            action = self.recent_menu.addAction(f"{project.name}  —  {project.path.parent}")
            action.triggered.connect(
                lambda _=False, p=project.path: self.open_recent_requested.emit(p)
            )
        self.recent_menu.setEnabled(bool(items))

    @redo_on_switch
    def set_examples(
        self, examples: Iterable[ExampleDefinition], board: BoardDefinition | None
    ) -> None:
        """Fill "File → Examples"; ``board`` is set when the list is filtered by a board."""
        menu = self.examples_menu
        menu.clear()
        items = list(examples)
        if board is not None:
            text = _("Для плати: {board}").format(board=board.display_name)
            menu.addAction(text).setEnabled(False)
            menu.addSeparator()
        for example in items:
            action = menu.addAction(example.title + "…")
            action.setToolTip(example.description)
            action.setStatusTip(example.description)
            action.triggered.connect(
                lambda _=False, example_id=example.id: self.example_requested.emit(example_id)
            )
        if not items:
            menu.addAction(_("Для цієї плати ще немає прикладів")).setEnabled(False)

    def set_busy(self, busy: bool, operation: str | None = None) -> None:
        """A build or upload is running: block conflicting commands, allow Stop.

        ``operation`` ("verify" / "upload") names the toolbar button that becomes "Stop".
        """
        self.toolbar.set_busy(busy, operation)
        self.settings_dialog.profile_combo.setEnabled(not busy)
        if busy:
            self.toasts.clear()  # messages about the previous attempt are stale now
        else:
            self.activity_spinner.set_spinning(False)
            self.activity_bar.stop()
            if self.activity_status.property("state") == "busy":
                self._clear_activity()
        has_project = getattr(self, "_has_project", False)
        for key in ("verify", "upload", "clean", "change_board", "add_peripheral"):
            self.actions_by_name[key].setEnabled(has_project and not busy)
        self.actions_by_name["stop"].setEnabled(busy)

    def show_message(self, text: Text, timeout_ms: int = 5000) -> None:
        """Short activity note in the status bar; 0 keeps it until the next message."""
        self._set_activity(text, "", None, timeout_ms)

    def show_activity(self, text: Text, percent: int | None = None) -> None:
        """An operation is running: spinner, stage text and the progress strip."""
        if percent is None:
            self._set_activity(text, "busy", None, 0)
        else:
            self._set_activity(
                lambda: f"{resolve(text)} {formatting.percent(str(percent))}", "busy", None, 0
            )
        self.activity_spinner.set_spinning(True)
        self.activity_bar.start(percent)

    def show_result(
        self, text: Text, success: bool | None, tab: int | None = None, timeout_ms: int = 0
    ) -> None:
        """How the operation ended (None = stopped); a click opens ``tab`` of the bottom panel."""
        self.activity_spinner.set_spinning(False)
        self.activity_bar.stop()
        state = "" if success is None else "ok" if success else "error"
        self._set_activity(text, state, tab, timeout_ms)

    def _set_activity(self, text: Text, state: str, tab: int | None, timeout_ms: int) -> None:
        """``text``: a marked text, a function that builds it, or a text shown as it is."""
        live(self.activity_status.setText, text)
        self.activity_status.setProperty("state", state)
        self.activity_status.style().unpolish(self.activity_status)
        self.activity_status.style().polish(self.activity_status)
        self._activity_tab = tab
        self.activity_status.set_clickable(tab is not None)
        live(
            self.activity_status.setToolTip,
            lambda: _("Показати подробиці") if tab is not None else "",
        )
        if timeout_ms > 0:
            self._activity_timer.start(timeout_ms)
        else:
            self._activity_timer.stop()

    def _clear_activity(self) -> None:
        self._set_activity("", "", None, 0)

    def _on_activity_clicked(self) -> None:
        if self._activity_tab is not None:
            self.show_bottom_tab(self._activity_tab)

    # --- bottom tab badges -----------------------------------------------------------------

    def notify_tab(self, index: int, error: bool = False) -> None:
        """Mark a bottom tab the user is not looking at: something new is there."""
        if self.bottom_panel.isVisible() and self.bottom_tabs.currentIndex() == index:
            return
        self._tab_badges[index] = error
        self.bottom_tabs.setTabIcon(
            index,
            badged_icon(
                TAB_ICONS[index],
                palette.TEXT_MUTED,
                palette.ERROR if error else palette.ACCENT_BRIGHT,
            ),
        )

    def tab_badge(self, index: int) -> bool | None:
        """None = no badge, otherwise whether the badge marks an error."""
        return self._tab_badges.get(index)

    def _clear_tab_badge(self, index: int) -> None:
        if self._tab_badges.pop(index, None) is not None:
            self.bottom_tabs.setTabIcon(index, icon(TAB_ICONS[index], palette.TEXT_MUTED))

    def set_tools_status(self, text: Text, ok: bool) -> None:
        live(self.tools_status.setText, text)
        self.tools_status.setProperty("state", "ok" if ok else "error")
        self.tools_status.style().unpolish(self.tools_status)
        self.tools_status.style().polish(self.tools_status)
        self._tools_ok = ok
        self._refresh_tools_icon()

    def _refresh_tools_icon(self) -> None:
        ok = getattr(self, "_tools_ok", True)
        if self.current_screen is Screen.EDITOR:
            set_icon(self.tools_icon, "rust", 20, palette.TEXT if ok else palette.ERROR)
        else:
            set_icon(self.tools_icon, "circle-check" if ok else "circle-x", 20,
                     palette.SUCCESS if ok else palette.ERROR)  # fmt: skip

    def set_connection(self, name: str | None, method: UploadMethod | None = None) -> None:
        """Upload target shown in the status bar ("ST-Link"), or None; ``method`` is how it
        flashes, for the wiring shown in the "Target" panel."""
        connected = name is not None
        live(self.connection_status.setText, lambda: name or _("не підключено"))
        self.connection_dot.setProperty("connected", connected)
        self.connection_dot.style().unpolish(self.connection_dot)
        self.connection_dot.style().polish(self.connection_dot)
        set_icon(self.connection_icon, "link" if connected else "unlink", 18,
                 palette.SUCCESS if connected else palette.TEXT_MUTED)  # fmt: skip
        self.target_view.set_probe(name)
        self.target_view.set_upload_method(method if connected else None)

    def show_device_menu(self) -> None:
        """Open the device menu of the toolbar (deferred: the popup runs its own event loop)."""
        QTimer.singleShot(0, self.toolbar.show_port_menu)

    def set_problem_counts(self, errors: int, warnings: int) -> None:
        set_icon(self.problems_icon, "circle-x" if errors else "circle-check", 20,
                 palette.ERROR if errors else palette.SUCCESS)  # fmt: skip

        def counts() -> str:
            if errors:
                text = ngettext("{count} помилка", "{count} помилок", errors).format(count=errors)
            else:
                text = _("0 помилок")
            if warnings:
                text += ", " + ngettext(
                    "{count} попередження", "{count} попереджень", warnings
                ).format(count=warnings)
            return text

        live(self.problems_status.setText, counts)
        total = errors + warnings
        live(
            self.bottom_tabs.setTabText,
            lambda: _("Проблеми ({count})").format(count=total) if total else _("Проблеми"),
            PROBLEMS_TAB,
        )

    def show_bottom_tab(self, index: int) -> None:
        self.bottom_panel.setVisible(True)
        self.actions_by_name["toggle_bottom"].setChecked(True)
        self.bottom_tabs.setCurrentIndex(index)
        self._clear_tab_badge(index)

    def show_output(self) -> None:
        self.show_bottom_tab(BUILD_TAB)

    def show_flash_log(self) -> None:
        self.show_bottom_tab(FLASH_TAB)

    def show_uart(self) -> None:
        self.show_bottom_tab(UART_TAB)

    def show_problems(self) -> None:
        self.show_bottom_tab(PROBLEMS_TAB)

    def go_to_problem(self, step: int) -> None:
        """F8 / Shift+F8: open the next / previous problem location."""
        if not self.problems_panel.go_to(step):
            self.show_message(_("Проблем немає"))

    def focus_file_tree(self) -> None:
        if self.current_screen is not Screen.EDITOR:
            return
        if not self.project_panel.isVisible():
            self.actions_by_name["toggle_project"].trigger()
        self.file_tree.setFocus()

    def set_cursor_position(self, line: int, column: int) -> None:
        live(
            self.cursor_status.setText,
            lambda: _("Ряд {line}, Стовп {column}").format(line=line, column=column),
        )
        self._show_cursor_position(
            self.current_screen is Screen.EDITOR and self.editor_tabs.is_editing
        )

    def _show_cursor_position(self, visible: bool) -> None:
        visible = visible and bool(self.cursor_status.text())
        self.cursor_status.setVisible(visible)
        self._cursor_separator.setVisible(visible)

    def shortcut_groups(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """Every menu command with a shortcut, by menu, plus the editor and terminal keys."""
        groups: list[tuple[str, list[tuple[str, str]]]] = []
        for menu_action in self.menu_bar.actions():
            menu = menu_action.menu()
            if not isinstance(menu, QMenu):
                continue
            rows = [
                (action.text().replace("…", ""), native_shortcut(action.shortcut()))
                for action in menu.actions()
                if not action.isSeparator() and not action.shortcut().isEmpty()
            ]
            if rows:
                groups.append((menu.title(), rows))
        for title, keys in ((_("Редактор"), EDITOR_SHORTCUTS), (_("Термінал"), TERMINAL_SHORTCUTS)):
            groups.append((title, [(_(text), shortcut) for text, shortcut in keys]))
        return groups

    def start_first_run_tour(self) -> TipTour:
        """Three tips after the first project is created: device, upload, UART output."""

        def uart_tab() -> QRect:
            bar = self.bottom_tabs.tabBar()
            rect = bar.tabRect(UART_TAB)
            return QRect(bar.mapTo(self, rect.topLeft()), rect.size())

        tour = TipTour(
            self,
            [
                Tip(
                    N_("Виберіть пристрій"),
                    N_(
                        "Підключіть ST-Link або USB-TTL адаптер і виберіть його тут — "
                        "через нього піде прошивка."
                    ),
                    lambda: widget_rect(self.toolbar.connection_button, self),
                ),
                Tip(
                    N_("Зібрати й прошити"),
                    lambda: _(
                        "Одна кнопка: збірка, прошивання й запуск програми на платі ({shortcut})."
                    ).format(shortcut=native_shortcut("Ctrl+U")),
                    lambda: widget_rect(self.toolbar.upload_button, self),
                ),
                Tip(
                    N_("Вивід плати — у вкладці UART"),
                    N_(
                        "Те, що програма надсилає через UART (як приклад Serial Hello), "
                        "з'являється тут."
                    ),
                    uart_tab,
                    before=lambda: self.bottom_panel.setVisible(True),
                ),
            ],
        )
        self.tour = tour
        tour.start()
        return tour

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self.shortcut_groups(), self).open()

    def show_about(self) -> None:
        AboutDialog(self._app_name, self._version, self).open()

    # --- dialogs (thin wrappers so controllers stay testable) ------------------------------

    def ask_project_directory(self, start: Path | None = None) -> Path | None:
        chosen = QFileDialog.getExistingDirectory(
            self, _("Відкрити проєкт"), str(start or Path.home())
        )
        return Path(chosen) if chosen else None

    def ask_directory(self, title: str, start: Path) -> Path | None:
        parent = self.settings_dialog if self.settings_dialog.isVisible() else self
        chosen = QFileDialog.getExistingDirectory(parent, title, str(start))
        return Path(chosen) if chosen else None

    def ask_save_file(self, title: str, start: Path, file_filter: str = "") -> Path | None:
        chosen, _selected = QFileDialog.getSaveFileName(self, title, str(start), file_filter)
        return Path(chosen) if chosen else None

    def ask_text(self, title: str, label: str, text: str = "") -> str | None:
        value, ok = QInputDialog.getText(self, title, label, text=text)
        return value.strip() if ok and value.strip() else None

    def ask_confirm(self, title: str, text: str, confirm_text: str, details: str = "") -> bool:
        """Ask before something that cannot be undone from inside the app."""
        box = QMessageBox(QMessageBox.Icon.Warning, title, "", parent=self)
        box.setText(text)
        box.setInformativeText(details)
        confirm = box.addButton(confirm_text, QMessageBox.ButtonRole.DestructiveRole)
        palette.set_button_role(confirm, "danger")
        cancel = box.addButton(_("Скасувати"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is confirm

    def ask_board(
        self,
        message: str,
        boards: Iterable[BoardDefinition],
        chips: Mapping[str, ChipDefinition],
        search: SearchFunction | None = None,
    ) -> str | None:
        dialog = BoardChooserDialog(message, boards, chips, search, self)
        return dialog.board_id if dialog.exec() == BoardChooserDialog.DialogCode.Accepted else None

    def ask_save_changes(self, file_names: Iterable[str]) -> SaveChoice:
        names = "\n".join(f"• {name}" for name in file_names)
        box = QMessageBox(QMessageBox.Icon.Warning, _("Незбережені зміни"), "", parent=self)
        live(box.setText, N_("Зберегти зміни перед закриттям?"))
        box.setInformativeText(names)
        save = box.addButton(_("Зберегти"), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(_("Не зберігати"), QMessageBox.ButtonRole.DestructiveRole)
        palette.set_button_role(save, "primary")
        palette.set_button_role(discard, "danger")
        box.addButton(_("Скасувати"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return SaveChoice.SAVE
        if clicked is discard:
            return SaveChoice.DISCARD
        return SaveChoice.CANCEL

    def ask_boot_mode(self, port_text: str, tx_pin: str, rx_pin: str) -> tuple[bool, bool]:
        """Returns (start the upload, do not show this again)."""
        dialog = BootModeDialog(port_text, tx_pin, rx_pin, self)
        accepted = dialog.exec() == BootModeDialog.DialogCode.Accepted
        return accepted, dialog.dont_show.isChecked()

    def show_error(self, title: str, text: str) -> None:
        self.notify(title, text, ToastKind.ERROR)

    def notify(
        self,
        title: str,
        text: str = "",
        kind: ToastKind = ToastKind.INFO,
        action_text: str | None = None,
        action: Callable[[], None] | None = None,
    ) -> Toast:
        """Non-modal notification in the corner of the window, optionally with one action."""
        return self.toasts.show(title, text, kind, action_text, action)

    def reveal_in_file_manager(self, path: Path, select: bool = False) -> None:
        """Open the folder in the OS file manager; ``select`` highlights ``path`` inside its
        parent instead (where the platform can do that)."""
        if select and sys.platform == "darwin":
            QProcess.startDetached("open", ["-R", str(path)])
        elif select and sys.platform == "win32":
            QProcess.startDetached("explorer", [f"/select,{path}"])
        else:
            folder = path.parent if select and not path.is_dir() else path
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def open_terminal(self, folder: Path) -> None:
        """Open the operating system's terminal in ``folder``."""
        if sys.platform == "darwin":
            started = QProcess.startDetached("open", ["-a", "Terminal", str(folder)])
        elif sys.platform == "win32":
            started = QProcess.startDetached("cmd", ["/c", "start", "cmd"], str(folder))
        else:
            started = QProcess.startDetached("x-terminal-emulator", [], str(folder))
        if not (started[0] if isinstance(started, tuple) else started):
            self.show_error(_("Термінал"), _("Не вдалося відкрити системний термінал."))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.confirm_close is not None and not self.confirm_close():
            event.ignore()
            return
        super().closeEvent(event)

    @property
    def version_text(self) -> str:
        return self._version_label.text()
