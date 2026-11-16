"""Bring an already built interface to the theme that has just become active.

The style sheet and the QPalette repaint most of it. What they cannot reach are colours a
widget took once and kept: icon pictures and coloured text. Icons are re-made generically
(they remember the colour token they were drawn in); a widget with anything else of that kind
defines ``refresh_theme()`` and is called from here.
"""

from __future__ import annotations

from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLabel, QTabBar, QWidget

from nightexpress.views import icons
from nightexpress.views.theme import palette
from nightexpress.views.widgets import THEMED_ICON_PROPERTY, set_icon


def retint(app: QApplication) -> None:
    for widget in app.allWidgets():
        retint_widget(widget)


def retint_widget(widget: QWidget) -> None:
    if isinstance(widget, QAbstractButton):
        new = icons.retinted(widget.icon())
        if new is not None:
            widget.setIcon(new)
    elif isinstance(widget, QLabel):
        note = widget.property(THEMED_ICON_PROPERTY)
        if note:
            name, size, token = note
            set_icon(widget, name, int(size), palette.color(token))
    elif isinstance(widget, QTabBar):
        for index in range(widget.count()):
            new = icons.retinted(widget.tabIcon(index))
            if new is not None:
                widget.setTabIcon(index, new)
    elif isinstance(widget, QComboBox):
        for index in range(widget.count()):
            new = icons.retinted(widget.itemIcon(index))
            if new is not None:
                widget.setItemIcon(index, new)
    refresh = getattr(widget, "refresh_theme", None)
    if callable(refresh):
        refresh()
    widget.update()  # custom-painted widgets read the theme while painting
