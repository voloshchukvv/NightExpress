"""The look of the application built from the active colour theme: QPalette, style sheet, style.

The colours themselves live in ``resources/themes/*.toml``; here they are read as
``palette.TEXT`` or ``palette.color("text")``, always from the theme that is active now.
"""

from __future__ import annotations

import tempfile
from importlib import resources
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QMenu,
    QProxyStyle,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from nightexpress.models.entities.theme import COLOR_TOKENS, Theme


class ThemeColor(str):
    """A colour of the theme ("#rrggbb") that remembers which token it is.

    It is an ordinary string everywhere a colour is expected. Whatever keeps it (an icon, a
    text format) can later ask the new theme for the same token: see ``views/theme/retint``.
    """

    __slots__ = ("token",)
    token: str

    def __new__(cls, value: str, token: str) -> ThemeColor:
        color = super().__new__(cls, value)
        color.token = token
        return color


_current: Theme | None = None


class _Signals(QObject):
    changed = Signal(object)  # Theme: the active theme was replaced


signals = _Signals()  # widgets that cache colours listen to ``signals.changed``


def current() -> Theme:
    """The active theme. The composition root sets it before any view is created (``app.py``
    through ``apply_theme``; the tests through ``set_current`` in ``conftest.py``)."""
    if _current is None:
        raise RuntimeError("no theme is active: call apply_theme() or set_current() first")
    return _current


def set_current(theme: Theme) -> None:
    """Make ``theme`` the source of every ``palette.NAME`` (no repainting; see apply_theme)."""
    global _current
    _current = theme


def color(token: str) -> ThemeColor:
    """A colour of the active theme by its token name, e.g. ``color("accent")``."""
    return ThemeColor(current().colors[token], token)


def __getattr__(name: str) -> ThemeColor:
    # ``palette.TEXT`` and friends: read from the active theme at the moment of use, so the
    # views keep their short spelling and follow a theme change.
    token = name.lower()
    if name.isupper() and token in COLOR_TOKENS:
        return color(token)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_palette(theme: Theme | None = None) -> QPalette:
    c = (theme or current()).color
    p = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    p.setColor(role.Window, QColor(c("background")))
    p.setColor(role.WindowText, QColor(c("text")))
    p.setColor(role.Base, QColor(c("surface")))
    p.setColor(role.AlternateBase, QColor(c("surface_alt")))
    p.setColor(role.Text, QColor(c("text")))
    p.setColor(role.PlaceholderText, QColor(c("text_muted")))
    p.setColor(role.Button, QColor(c("surface_alt")))
    p.setColor(role.ButtonText, QColor(c("text")))
    p.setColor(role.ToolTipBase, QColor(c("surface_alt")))
    p.setColor(role.ToolTipText, QColor(c("text")))
    p.setColor(role.Highlight, QColor(c("accent")))
    p.setColor(role.HighlightedText, QColor(c("accent_text")))
    p.setColor(role.Link, QColor(c("accent")))
    p.setColor(role.Mid, QColor(c("border")))
    p.setColor(role.Dark, QColor(c("background")))

    for r in (role.Text, role.ButtonText, role.WindowText):
        p.setColor(group.Disabled, r, QColor(c("disabled_text")))
    return p


def toast_fill(strip: str, theme: Theme | None = None) -> str:
    """Toast background with a 4 px colour strip on the left (toasts are 400 px wide)."""
    fill = (theme or current()).color("surface_alt")
    return (
        "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
        f"stop:0 {strip}, stop:0.008 {strip}, stop:0.0085 {fill}, stop:1 {fill})"
    )


_icon_directory: tempfile.TemporaryDirectory[str] | None = None


def themed_icons(theme: Theme) -> str:
    """Folder with the few icons the style sheet refers to by URL, drawn in ``theme``'s colours
    (a style sheet cannot recolour an image)."""
    global _icon_directory
    if _icon_directory is None:
        _icon_directory = tempfile.TemporaryDirectory(prefix="nightexpress-icons-")
    folder = Path(_icon_directory.name) / theme.id
    folder.mkdir(exist_ok=True)
    source = resources.files("nightexpress") / "resources" / "icons"
    for name, (source_name, token) in STYLE_SHEET_ICONS.items():
        text = (source / f"{source_name}.svg").read_text(encoding="utf-8")
        (folder / f"{name}.svg").write_text(
            text.replace("currentColor", theme.color(token)), encoding="utf-8"
        )
    return folder.as_posix()


# Icons used as ``image: url(...)`` in the style sheet -> the bundled icon and the colour token
# it is drawn in.
STYLE_SHEET_ICONS = {
    "check": ("check", "accent_text"),
    "chevron_down": ("chevron_down", "arrow"),
    "chevron_up": ("chevron_up", "arrow"),
    "chevron_up_accent": ("chevron_up", "accent"),  # an open combo box
}


def build_stylesheet(theme: Theme | None = None) -> str:
    theme = theme or current()
    values = {token.upper(): value for token, value in theme.colors.items()}
    values["ICONS"] = themed_icons(theme)
    for token in ("accent_bright", "success", "warning", "error"):
        values[f"TOAST_FILL_{token.upper()}"] = toast_fill(theme.color(token), theme)
    return _STYLE_SHEET.format_map(values)


# Every colour is a {TOKEN} of the theme; literal braces of the style sheet are doubled.
_STYLE_SHEET = """
    QMainWindow, #editorPage, QStackedWidget {{ background: {BACKGROUND}; }}
    QWidget {{ font-size: 14px; }}

    /* --- header (logo + menus) --- */
    /* Above the start screen's sidebar the name block is the top of that column. */
    #headerBrand[column="true"] {{
        background: {SURFACE}; border-right: 1px solid {BORDER};
    }}
    QToolButton#headerHome {{ background: transparent; border: none; }}
    QLabel#appTitle {{ font-size: 19px; font-weight: 700; color: {TEXT}; }}
    QMenuBar {{ background: transparent; color: {TEXT}; font-size: 15px; }}
    QMenuBar::item {{ padding: 6px 12px; border-radius: 6px; background: transparent; }}
    QMenuBar::item:selected {{ background: {SURFACE_ALT}; }}
    QMenu {{
        background: {SURFACE}; border: 1px solid {OUTLINE}; border-radius: 10px; padding: 6px;
    }}
    QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
    QMenu::item:disabled {{ color: {DISABLED_TEXT}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
    QFrame#separator {{ background: {BORDER}; border: none; }}

    /* --- buttons and selectors --- */
    QPushButton, QToolButton#connectionButton, QComboBox {{
        background: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {CONTROL_BORDER};
        border-radius: 7px; min-height: 20px; padding: 6px 12px;
    }}
    QPushButton:hover, QToolButton#connectionButton:hover, QComboBox:hover {{
        background: {CONTROL_HOVER}; border-color: {TEXT_MUTED};
    }}
    QPushButton:pressed, QToolButton#connectionButton:pressed {{
        background: {CONTROL_PRESSED}; border-color: {ACCENT};
    }}
    QPushButton[buttonRole="primary"] {{
        background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_TEXT}; font-weight: 600;
    }}
    QPushButton[buttonRole="primary"]:hover {{
        background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER};
    }}
    QPushButton[buttonRole="primary"]:pressed {{
        background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED};
    }}
    QPushButton[buttonRole="accent"], QPushButton#connectButton:checked,
    QToolButton#connectionButton[connected="true"] {{
        background: {ACCENT_SOFT}; border-color: {ACCENT_BORDER}; color: {TEXT};
    }}
    QPushButton[buttonRole="accent"]:hover, QPushButton#connectButton:checked:hover,
    QToolButton#connectionButton[connected="true"]:hover {{
        background: {ACCENT_SOFT_HOVER}; border-color: {ACCENT};
    }}
    QPushButton[buttonRole="accent"]:pressed, QPushButton#connectButton:checked:pressed,
    QToolButton#connectionButton[connected="true"]:pressed {{ background: {ACCENT_SOFT_PRESSED}; }}
    QPushButton[buttonRole="danger"], QPushButton[running="true"] {{
        background: {ERROR_SOFT}; border-color: {ERROR_BORDER}; color: {ERROR_TEXT};
    }}
    QPushButton[buttonRole="danger"]:hover, QPushButton[running="true"]:hover {{
        background: {ERROR_SOFT_HOVER}; border-color: {ERROR};
    }}
    QPushButton[buttonRole="danger"]:pressed, QPushButton[running="true"]:pressed {{
        background: {ERROR_SOFT_PRESSED};
    }}
    QPushButton[buttonRole="primary"]:disabled, QPushButton[buttonRole="accent"]:disabled,
    QPushButton[buttonRole="danger"]:disabled, QPushButton:disabled, QComboBox:disabled {{
        background: {SURFACE}; border-color: {BORDER}; color: {CONTROL_DISABLED_TEXT};
    }}
    QPushButton:focus, QComboBox:focus, QToolButton#connectionButton:focus {{
        border-color: {FOCUS};
    }}
    QDialogButtonBox QPushButton, QWizard QPushButton {{ min-width: 76px; }}
    QLineEdit {{
        background: {EDITOR}; color: {TEXT}; border: 1px solid {CONTROL_BORDER};
        border-radius: 7px; min-height: 20px; padding: 6px 10px;
    }}
    QLineEdit:focus {{ border-color: {FOCUS}; }}
    QLineEdit:disabled {{ border-color: {BORDER}; color: {CONTROL_DISABLED_TEXT}; }}
    QCheckBox {{ spacing: 8px; padding: 4px; color: {TEXT}; }}
    QCheckBox:focus {{ color: {FOCUS}; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border: 1px solid {CHECKBOX_BORDER}; border-radius: 4px;
        background: {SURFACE_ALT};
    }}
    QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
    QCheckBox::indicator:checked {{
        background: {ACCENT}; border-color: {ACCENT}; image: url("{ICONS}/check.svg");
    }}
    QCheckBox::indicator:disabled {{ border-color: {BORDER}; background: transparent; }}
    /* combobox-popup: 0 = a list below the box (not the menu-style popup laid over the box,
       which also ignores the ::item styles); PopupStyler makes its window corners round. */
    QComboBox {{ padding-left: 10px; padding-right: 5px; combobox-popup: 0; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{
        image: url("{ICONS}/chevron_down.svg"); width: 14px; height: 14px;
    }}
    QComboBox::down-arrow:on {{ image: url("{ICONS}/chevron_up_accent.svg"); }}
    #ToolbarView QComboBox[readOnly="true"] {{
        background: transparent; border-color: transparent; color: {TEXT_MUTED};
        padding-right: 8px;
    }}
    #ToolbarView QComboBox[readOnly="true"]:focus {{ border-color: {FOCUS}; }}
    #ToolbarView QComboBox[readOnly="true"]::drop-down {{ width: 0; }}
    #ToolbarView QComboBox[readOnly="true"]::down-arrow {{ image: none; }}
    QComboBox QFrame {{ background: transparent; border: none; }}  /* the popup window */
    QComboBox QAbstractItemView {{
        background: {SURFACE_ALT}; border: 1px solid {OUTLINE}; border-radius: 7px;
        selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
        padding: 6px 4px; outline: none;
    }}
    /* Same geometry as the box, so the list is exactly as wide and the texts line up:
       1 border + 4 + 3 + the 3px text margin Qt adds in a row = the box's 11px to its text
       on the left; on the right the tick (CurrentItemDelegate) sits under the box's arrow,
       in the 24px (21 + that margin) the text keeps clear. */
    QComboBox QAbstractItemView::item {{
        min-height: 28px; padding: 0 21px 0 3px; border-radius: 6px; color: {TEXT};
    }}
    QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{
        background: {ACCENT_SOFT}; color: {TEXT};
    }}
    QComboBox QAbstractItemView::item:disabled {{
        background: transparent; color: {DISABLED_TEXT};
    }}
    /* An open combo box and its list are one shape (ComboPopupFitter sets popupDirection):
       the same background and quiet outline, square corners where they meet. The seam is
       the list's own edge, so a list wider than the box is still outlined all the way. */
    QComboBox:on {{ background: {SURFACE_ALT}; border-color: {OUTLINE}; }}
    QComboBox[popupDirection="down"]:on {{
        border-bottom-color: {SURFACE_ALT};
        border-bottom-left-radius: 0; border-bottom-right-radius: 0;
    }}
    QComboBox[popupDirection="up"]:on {{
        border-top-color: {SURFACE_ALT};
        border-top-left-radius: 0; border-top-right-radius: 0;
    }}
    QComboBox QAbstractItemView[popupDirection="down"] {{
        border-top-left-radius: 0; border-top-right-radius: 0;
    }}
    QComboBox QAbstractItemView[popupDirection="up"] {{
        border-bottom-left-radius: 0; border-bottom-right-radius: 0;
    }}
    /* A list wider than its box (a long row in a narrow box) sticks out on the right:
       that corner meets nothing and stays round. */
    QComboBox QAbstractItemView[popupDirection="down"][popupWider="true"] {{
        border-top-right-radius: 7px;
    }}
    QComboBox QAbstractItemView[popupDirection="up"][popupWider="true"] {{
        border-bottom-right-radius: 7px;
    }}
    QToolButton#connectionButton {{ padding-left: 10px; padding-right: 24px; }}
    QToolButton#connectionButton::menu-indicator {{
        image: url("{ICONS}/chevron_down.svg"); width: 14px; height: 14px;
        subcontrol-origin: padding; subcontrol-position: right center; right: 7px;
    }}

    /* --- panels --- */
    QFrame#panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; }}
    #panelHeader {{ background: transparent; }}
    QLabel#panelTitle {{ font-size: 17px; font-weight: 700; color: {TEXT}; }}
    QToolButton#panelTool, QToolButton#tabClose {{
        background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 0;
    }}
    QToolButton#panelTool:hover, QToolButton#tabClose:hover {{ background: {CONTROL_HOVER}; }}
    QToolButton#panelTool:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT_BORDER}; }}
    QToolButton#panelTool:checked:hover {{ background: {ACCENT_SOFT_HOVER}; }}
    QToolButton#panelTool:pressed, QToolButton#tabClose:pressed {{
        background: {CONTROL_PRESSED}; border-color: {CONTROL_BORDER};
    }}
    QToolButton#panelTool:focus, QToolButton#tabClose:focus {{ border-color: {FOCUS}; }}
    QToolButton#panelTool:disabled {{ background: transparent; border-color: transparent; }}
    QToolButton#panelTool::menu-indicator {{ image: none; width: 0; }}
    QSplitter::handle {{ background: {BACKGROUND}; }}
    QTreeView, QTableView {{ background: {SURFACE}; border: none; font-size: 15px; }}
    QTreeView::item {{ padding: 5px 2px; border-radius: 4px; }}
    QTreeView::item:hover {{ background: {SURFACE_ALT}; }}
    QTreeView::item:selected, QTableView::item:selected {{
        background: {SELECTION}; color: {TEXT};
    }}
    /* The file tree draws its own highlight (FileTreeView's delegate): the rules above would
       also paint the indentation in front of the row as a second, separate shape. */
    QTreeView#fileTree {{ selection-background-color: transparent; }}
    QTreeView#fileTree::item:selected, QTreeView#fileTree::item:hover {{
        background: transparent; color: {TEXT};
    }}
    QHeaderView::section {{
        background: {SURFACE}; color: {TEXT_MUTED}; border: none;
        border-bottom: 1px solid {BORDER}; padding: 6px 10px;
    }}
    QLabel#chipTitle {{ font-size: 20px; font-weight: 600; color: {TEXT}; }}
    QLabel#fieldCaption {{ color: {TEXT_MUTED}; }}
    #TargetPanel QLabel {{ font-size: 15px; }}
    QLabel#placeholder, QLabel#mutedText {{ color: {TEXT_MUTED}; }}
    #TargetPanel, #targetContent {{ background: transparent; }}
    #TargetPanel QLabel#targetSection {{ font-size: 16px; font-weight: 700; color: {TEXT}; }}
    #TargetPanel QLabel#wiringPin {{ color: {ACCENT_BRIGHT}; font-weight: 600; }}
    #TargetPanel QLabel#mutedText {{ font-size: 14px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {SCROLLBAR}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {SCROLLBAR_HOVER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    /* --- editor tabs --- */
    #editorTabRow, QTabBar#editorTabs {{ background: {SURFACE}; }}
    QTabBar#editorTabs::tab {{
        background: {SURFACE}; color: {TEXT}; padding: 9px 16px; font-size: 15px;
        border: none; border-right: 1px solid {BORDER}; border-bottom: 2px solid transparent;
    }}
    QTabBar#editorTabs::tab:selected {{ background: {EDITOR}; border-bottom: 2px solid {ACCENT}; }}
    QTabBar#editorTabs::tab {{ padding-right: 8px; }}
    QTabBar#editorTabs::tab:!selected:hover {{ background: {CONTROL_HOVER}; }}
    QTabBar#editorTabs::tab:focus {{ border-bottom-color: {FOCUS}; }}
    #emptyEditor {{ background: {EDITOR}; }}
    QLabel#emptyHint {{ color: {TEXT_MUTED}; font-size: 15px; }}
    /* --- shortcuts page --- */
    QLabel#shortcutGroup {{
        color: {TEXT}; font-size: 15px; font-weight: 500;
        border: none; border-bottom: 1px solid {BORDER};
        margin: 10px 10px 2px; padding: 2px 0 5px;
    }}
    #shortcutRow {{ border-radius: 6px; }}
    #shortcutRow:hover {{ background: {CONTROL_HOVER}; }}
    #shortcutRow:focus {{ background: {CONTROL_HOVER}; }}
    QLabel#aboutHolder {{ color: {TEXT_MUTED}; font-size: 13px; }}
    QLabel#keyCap {{
        color: {TEXT}; font-size: 12px; background: {SURFACE_ALT};
        border: 1px solid {CONTROL_BORDER}; border-radius: 5px; padding: 0 6px; min-width: 10px;
    }}
    QLabel#emptyKey {{
        color: {TEXT}; font-size: 14px; background: {SURFACE_ALT};
        border: 1px solid {BORDER}; border-radius: 4px; padding: 2px 8px;
    }}

    /* --- lists in dialogs (boards, templates): rows and highlight as in the file tree --- */
    QListWidget {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
        padding: 4px; font-size: 15px; outline: none;
    }}
    QListWidget::item {{ padding: 5px 8px; border-radius: 4px; }}
    QListWidget::item:hover {{ background: {SURFACE_ALT}; }}
    QListWidget::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
    QListWidget#previewFiles::item {{ padding: 2px 8px; color: {TEXT_MUTED}; }}
    QListWidget#previewFiles::item:hover {{ background: transparent; }}
    QLabel#wizardTitle {{ font-size: 21px; font-weight: 700; color: {TEXT}; }}

    /* --- quick open --- */
    QFrame#quickOpen {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
    QLineEdit#quickOpenQuery {{ font-size: 15px; padding: 7px 10px; }}
    QListWidget#quickOpenResults {{
        background: transparent; border: none; font-size: 15px; outline: none;
    }}
    QListWidget#quickOpenResults::item {{ padding: 4px 8px; border-radius: 4px; }}
    QListWidget#quickOpenResults::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

    /* --- bottom panel and settings tabs --- */
    QTabWidget#bottomTabs::pane, QTabWidget#settingsTabs::pane {{
        border: none; border-top: 1px solid {BORDER};
    }}
    QTabWidget#bottomTabs QTabBar::tab, QTabWidget#settingsTabs QTabBar::tab {{
        background: transparent; color: {TEXT_MUTED}; padding: 7px 13px;
        border: 1px solid transparent; border-radius: 6px; margin: 3px 1px;
    }}
    QTabWidget#bottomTabs QTabBar::tab:hover, QTabWidget#settingsTabs QTabBar::tab:hover {{
        background: {CONTROL_HOVER}; color: {TEXT};
    }}
    QTabWidget#bottomTabs QTabBar::tab:selected, QTabWidget#settingsTabs QTabBar::tab:selected {{
        color: {TEXT}; background: {ACCENT_SOFT}; border-color: {ACCENT_BORDER};
    }}
    QTabWidget#bottomTabs QTabBar::tab:focus, QTabWidget#settingsTabs QTabBar::tab:focus {{
        border-color: {FOCUS};
    }}
    QTabWidget#bottomTabs QTabBar QToolButton {{
        background: {SURFACE}; border: none; border-radius: 4px;
    }}
    QTabWidget#bottomTabs QTabBar QToolButton:hover {{ background: {CONTROL_HOVER}; }}
    QTabWidget#bottomTabs QTabBar QToolButton:pressed {{ background: {CONTROL_PRESSED}; }}
    QTextEdit#OutputPanel {{ background: {SURFACE}; border: none; padding: 8px 12px; }}

    QLabel#emptyTitle {{ font-size: 16px; font-weight: 600; color: {TEXT}; }}
    QTreeView#problemsTree {{ font-size: 14px; }}
    QLineEdit#logSearch {{ min-height: 18px; padding: 4px 9px; font-size: 13px; }}
    QLabel#uartDot {{ color: {SUCCESS}; }}

    /* --- status bar --- */
    QStatusBar {{ background: {HEADER}; color: {TEXT}; border-top: 1px solid {BORDER}; }}
    QStatusBar QLabel {{ font-size: 15px; }}
    QStatusBar QLabel:focus, #TargetPanel ClickableLabel:focus {{
        color: {FOCUS}; text-decoration: underline;
    }}
    QStatusBar QLabel[state="error"] {{ color: {ERROR}; }}
    QLabel#connectionDot {{ color: {DISABLED_TEXT}; font-size: 12px; }}
    QLabel#connectionDot[connected="true"] {{ color: {SUCCESS}; }}
    QLabel#activityStatus {{ color: {TEXT_MUTED}; }}
    QLabel#connectionStatus:hover {{ color: {ACCENT_BRIGHT}; }}
    QLabel#activityStatus[state="busy"] {{ color: {TEXT}; }}
    QLabel#activityStatus[state="ok"] {{ color: {SUCCESS}; }}
    QLabel#activityStatus[state="error"] {{ color: {ERROR}; }}

    /* --- notifications --- */
    /* The colour strip is part of the fill, as on the wizard's current step: a thick left
       border would be cut off where it meets the rounded corners. */
    QFrame#toast {{
        border: 1px solid {TOAST_BORDER}; border-radius: 8px;
        border-left-color: {ACCENT_BRIGHT}; background: {TOAST_FILL_ACCENT_BRIGHT};
    }}
    QFrame#toast[kind="success"] {{
        border-left-color: {SUCCESS}; background: {TOAST_FILL_SUCCESS};
    }}
    QFrame#toast[kind="warning"] {{
        border-left-color: {WARNING}; background: {TOAST_FILL_WARNING};
    }}
    QFrame#toast[kind="error"] {{
        border-left-color: {ERROR}; background: {TOAST_FILL_ERROR};
    }}
    QLabel#toastTitle {{ font-size: 15px; font-weight: 700; color: {TEXT}; }}
    QLabel#toastText {{ font-size: 14px; color: {TOAST_TEXT}; }}
    QToolTip {{ background: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER}; }}

    /* --- start screen --- */
    QFrame#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
    QLabel#sidebarTitle {{ font-size: 21px; font-weight: 700; color: {TEXT}; }}
    QPushButton#navButton {{
        text-align: left; padding: 9px 14px; border-color: transparent;
        font-size: 16px; color: {TEXT_MUTED}; background: transparent;
    }}
    QPushButton#navButton:hover {{ background: {CONTROL_HOVER}; color: {TEXT}; }}
    /* The strip is part of the fill (as on the wizard's current step and on toasts), so it
       follows the rounded corners. */
    QPushButton#navButton:checked {{
        color: {TEXT}; border-color: {ACCENT_BORDER}; border-left-color: {ACCENT_BRIGHT};
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {ACCENT_BRIGHT}, stop:0.014 {ACCENT_BRIGHT},
            stop:0.017 {ACCENT_SOFT}, stop:1 {ACCENT_SOFT});
    }}
    QPushButton#navButton:pressed {{ background: {CONTROL_PRESSED}; }}
    QPushButton#navButton:focus {{ border-color: {FOCUS}; }}
    QLabel#welcomeTitle {{ font-size: 39px; font-weight: 700; color: {TEXT}; }}
    QLabel#welcomeSubtitle {{ font-size: 17px; color: {TEXT_MUTED}; }}
    QLabel#sectionTitle {{ font-size: 23px; font-weight: 700; color: {TEXT}; }}
    QFrame#card, QFrame#primaryCard, QFrame#recentCard {{
        background: {SURFACE}; border: 1px solid {CONTROL_BORDER}; border-radius: 10px;
    }}
    QFrame#primaryCard {{ background: {ACCENT_SOFT}; border-color: {ACCENT_BORDER}; }}
    QFrame#card:hover, QFrame#recentCard:hover {{ background: {SURFACE_ALT}; }}
    QFrame#primaryCard:hover {{ background: {ACCENT_SOFT_HOVER}; border-color: {ACCENT}; }}
    QFrame#card[pressed="true"], QFrame#recentCard[pressed="true"],
    QFrame#primaryCard[pressed="true"] {{ background: {CONTROL_PRESSED}; }}
    QFrame#card:focus, QFrame#recentCard:focus, QFrame#primaryCard:focus {{
        border-color: {FOCUS};
    }}
    QLabel#cardIcon {{ background: {SURFACE_ALT}; border-radius: 9px; }}
    QFrame#primaryCard QLabel#cardIcon {{ background: {ACCENT_ICON_BACKGROUND}; }}
    QLabel#cardTitle {{ font-size: 18px; font-weight: 700; color: {TEXT}; }}
    QLabel#cardSubtitle, QLabel#cardRight {{ font-size: 14px; color: {TEXT_MUTED}; }}
    QFrame#recentCard[missing="true"] QLabel {{ color: {TEXT_MUTED}; }}
    QLabel#exampleTitle {{ font-size: 18px; font-weight: 700; color: {TEXT}; }}
    QLabel#tipStep {{ color: {TEXT_MUTED}; font-size: 13px; }}
    QLabel#tipTitle {{ color: {TEXT}; font-size: 17px; font-weight: 700; }}
    QLabel#tipText {{ color: {TEXT}; font-size: 15px; }}
    QFrame#wizardSteps {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
    QLabel#wizardStep {{
        color: {TEXT_MUTED}; font-size: 15px; padding: 9px 11px; border-radius: 6px;
    }}
    QLabel#wizardStep[state="done"] {{ color: {TEXT}; }}
    /* Paint the marker and the fill together so they share the outer curve. */
    QLabel#wizardStep[state="current"] {{
        color: {TEXT}; font-weight: 700;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {ACCENT_BRIGHT}, stop:0.019 {ACCENT_BRIGHT},
            stop:0.022 {ACCENT_SOFT}, stop:1 {ACCENT_GRADIENT_END});
    }}
    QFrame#envCard {{ background: {SURFACE}; border: 1px solid {OUTLINE}; border-radius: 8px; }}
    QLabel#envTitle {{ font-size: 21px; font-weight: 700; color: {TEXT}; }}
    QLabel#envRow {{ font-size: 16px; color: {TEXT}; }}
    QPushButton#linkButton {{
        border-color: transparent; background: transparent; color: {ACCENT_BRIGHT};
        text-align: left; min-height: 18px; padding: 4px 7px;
    }}
    QPushButton#linkButton:hover {{ background: {ACCENT_SOFT}; }}
    QPushButton#linkButton:pressed {{ background: {CONTROL_PRESSED}; }}
    QPushButton#linkButton:focus {{ border-color: {FOCUS}; }}
    QPushButton#linkButton:disabled {{ color: {DISABLED_TEXT}; }}
    QLabel#errorLabel {{ color: {ERROR}; }}
    QSpinBox {{
        background: {SURFACE_ALT}; border: 1px solid {CONTROL_BORDER}; border-radius: 7px;
        min-height: 20px; padding: 6px 24px 6px 9px;
    }}
    QSpinBox:focus {{ border-color: {FOCUS}; }}
    QSpinBox::up-button, QSpinBox::down-button {{
        subcontrol-origin: padding; width: 22px; border-left: 1px solid {CONTROL_BORDER};
    }}
    QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 6px; }}
    QSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 6px; }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {CONTROL_HOVER}; }}
    QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{ background: {CONTROL_PRESSED}; }}
    QSpinBox::up-arrow {{ image: url("{ICONS}/chevron_up.svg"); width: 12px; height: 12px; }}
    QSpinBox::down-arrow {{ image: url("{ICONS}/chevron_down.svg"); width: 12px; height: 12px; }}
    QSpinBox::up-arrow:off, QSpinBox::down-arrow:off {{ image: none; }}
    QScrollArea {{ background: transparent; }}
    """


class AppStyle(QProxyStyle):
    """Fusion plus round popup windows (menus and combo box lists).

    A popup is a top-level window, opaque and rectangular by default, so a stylesheet
    border-radius would leave square corners behind it. A translucent window background lets
    the rounded frame drawn by the stylesheet be the visible shape (macOS adds the shadow).
    The attribute has to be set before the native window exists, i.e. when the widget is
    polished. (An application-wide event filter would do the same but receives events from
    QtWebEngine's threads too, which crashes PySide.)
    """

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self._fitter = ComboPopupFitter(self)

    def polish(self, target):  # type: ignore[no-untyped-def, override]  # QWidget / QPalette / QApplication
        if isinstance(target, QWidget) and is_popup(target):
            target.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            if not isinstance(target, QMenu):
                # The list continues the box: the system shadow would outline it a second
                # time, darker than the frame the style sheet draws.
                target.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
                # A filter on this one window (GUI thread only), not on the application.
                target.installEventFilter(self._fitter)
        if isinstance(target, QAbstractButton | QComboBox) and (
            target.focusPolicy() & Qt.FocusPolicy.ClickFocus
        ):
            # The focus outline is for the keyboard: a control that took focus on a click
            # would keep the outline after the mouse is gone.
            target.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return super().polish(target)


class ComboPopupFitter(QObject):
    """Makes an open combo box and its list one shape.

    When the list window appears it is moved flush against the box (above it when Qt opened
    it upwards for lack of room), made at least as wide, and both get ``popupDirection`` so
    the stylesheet squares the corners where they meet. Cleared again when the list closes.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.Show, QEvent.Type.Hide) and isinstance(watched, QWidget):
            combo = watched.parentWidget()
            view = watched.findChild(QAbstractItemView)
            if isinstance(combo, QComboBox) and view is not None:
                if kind is QEvent.Type.Show:
                    if not isinstance(view.itemDelegate(), CurrentItemDelegate):
                        view.setItemDelegate(CurrentItemDelegate(combo, view))
                    attach_popup(combo, watched, view)
                else:
                    set_popup_direction(combo, view, None)
        return False


class CurrentItemDelegate(QStyledItemDelegate):
    """Draws the rows of a combo box list and ticks the one that is chosen now, so the
    highlight is free to mean "under the pointer"."""

    TICK = 14
    INSET = 1  # from the row's right edge: puts the tick right under the arrow of the box

    def __init__(self, combo: QComboBox, parent: QObject) -> None:
        super().__init__(parent)
        self._combo = combo

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().paint(painter, option, index)
        if index.row() == self._combo.currentIndex():
            from nightexpress.views.icons import icon  # icons import this module

            box = option.rect
            tick = QRect(0, 0, self.TICK, self.TICK)
            tick.moveCenter(QPoint(box.right() - self.INSET - self.TICK // 2, box.center().y()))
            icon("check", color("accent")).paint(painter, tick)


def attach_popup(combo: QComboBox, popup: QWidget, view: QWidget) -> str:
    """Place ``popup`` against ``combo``; returns the direction ("down" or "up")."""
    if popup.layout() is not None:
        popup.layout().setContentsMargins(0, 0, 0, 0)
    box = QRect(combo.mapToGlobal(QPoint(0, 0)), combo.size())
    direction = "up" if popup.geometry().center().y() < box.center().y() else "down"
    # Wide enough for the longest row: the tick's room must not cut the texts short.
    frame = popup.width() - view.viewport().width()
    width = max(popup.width(), box.width(), view.sizeHintForColumn(0) + frame)
    y = box.bottom() + 1 if direction == "down" else box.top() - popup.height()
    popup.setGeometry(box.left(), y, width, popup.height())
    wider = width > box.width()
    if bool(view.property("popupWider")) != wider:
        view.setProperty("popupWider", wider)  # set_popup_direction re-polishes
        view.setProperty("popupDirection", None)
    set_popup_direction(combo, view, direction)
    return direction


def set_popup_direction(combo: QComboBox, view: QWidget, direction: str | None) -> None:
    for widget in (combo, view):
        if widget.property("popupDirection") != direction:
            widget.setProperty("popupDirection", direction)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()


def is_popup(widget: QWidget) -> bool:
    return isinstance(widget, QMenu) or widget.metaObject().className() == (
        "QComboBoxPrivateContainer"
    )


def apply_theme(app: QApplication, theme: Theme | None = None) -> None:
    """Make ``theme`` (the current one by default) the look of the whole application."""
    if theme is not None:
        set_current(theme)
    theme = current()
    if not isinstance(app.style(), AppStyle):
        app.setStyle(AppStyle("Fusion"))
    app.setPalette(build_palette(theme))
    app.setStyleSheet(build_stylesheet(theme))
    # Colours the widgets took earlier (icons, coloured text) do not follow a style sheet.
    from nightexpress.views.theme.retint import retint

    retint(app)
    signals.changed.emit(theme)


def set_button_role(button: QPushButton, role: str) -> None:
    button.setProperty("buttonRole", role)
    # Qt may have already styled buttons managed by a dialog or wizard.
    if button.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
        button.style().unpolish(button)
        button.style().polish(button)
        button.updateGeometry()
