"""Top row of the window: app logo, name and the menu bar."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenuBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import _, live
from nightexpress.views.theme import palette
from nightexpress.views.welcome_view import PAGE_MARGINS, SIDEBAR_WIDTH
from nightexpress.views.widgets import Separator, icon_label

DIVIDER_HEIGHT = 18  # a short stroke between the name and the menus
MARGIN = 6  # above and below the name and the menus
# QMenuBar starts its first title this far from its own left edge (item padding + style margin).
MENU_TEXT_INSET = 18


class HeaderBar(QWidget):
    """Logo, name and menus on the window's own colour, with no line under them.

    Above the start screen's sidebar the name block becomes the top of that column
    (``set_above_sidebar``); in the editor a short divider stands between the name and the menus.
    """

    def __init__(self, app_name: str, menu_bar: QMenuBar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("headerBar")
        # The menus live inside the window on every platform, so the window looks the same
        # on macOS, Windows and Linux.
        menu_bar.setNativeMenuBar(False)
        self.menu_bar = menu_bar
        # Logo and name are as wide as the start screen's sidebar below them; the block's last
        # pixel column (the divider, or the column's edge) is right above the sidebar's edge.
        self.brand = QWidget(self)
        self.brand.setObjectName("headerBrand")
        # Only then does a plain widget draw its style-sheet border, not just the background.
        self.brand.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.brand.setMinimumWidth(SIDEBAR_WIDTH)
        self.home_button = QToolButton(self.brand)
        self.home_button.setObjectName("headerHome")
        live(
            self.home_button.setAccessibleName,
            lambda: _("{app} — Початковий екран").format(app=app_name),
        )
        self.home_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        title = QLabel(app_name, self.home_button)
        title.setObjectName("appTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        home_layout = QHBoxLayout(self.home_button)
        home_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        home_layout.setContentsMargins(6, 0, 6, 0)
        home_layout.setSpacing(12)
        home_layout.addWidget(icon_label("cpu", 26, palette.ACCENT_BRIGHT, self.home_button))
        home_layout.addWidget(title)

        self.divider = Separator(parent=self.brand)
        self.divider.setFixedHeight(DIVIDER_HEIGHT)
        brand_layout = QHBoxLayout(self.brand)
        brand_layout.setContentsMargins(10, MARGIN, 0, MARGIN)
        brand_layout.setSpacing(12)
        brand_layout.addWidget(self.home_button)
        brand_layout.addStretch(1)
        brand_layout.addWidget(self.divider, 0, Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 16, 0)
        layout.setSpacing(0)
        layout.addWidget(self.brand)
        # The first menu's text starts where the text of the pages below starts.
        layout.addSpacing(PAGE_MARGINS[0] - MENU_TEXT_INSET)
        # The menus set the height of the bar.
        menus = QVBoxLayout()
        menus.setContentsMargins(0, MARGIN, 0, MARGIN)
        menus.addWidget(menu_bar)
        layout.addLayout(menus)
        layout.addStretch(1)
        self.set_above_sidebar(False)

    def set_above_sidebar(self, above: bool) -> None:
        """Whether the sidebar is below (the start screen): the name block then takes its fill
        and its edge, so the column runs from the top of the window to the bottom."""
        self.brand.setProperty("column", above)
        self.home_button.setEnabled(not above)
        cursor = Qt.CursorShape.ArrowCursor if above else Qt.CursorShape.PointingHandCursor
        self.home_button.setCursor(cursor)
        live(self.home_button.setToolTip, lambda: "" if above else _("Початковий екран"))
        self.divider.setVisible(not above)
        style = self.brand.style()
        style.unpolish(self.brand)
        style.polish(self.brand)
        self.brand.update()
