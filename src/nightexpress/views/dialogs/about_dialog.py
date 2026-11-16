"""Help → About: what the application is, its license, and the third-party components
it is built on with their licenses (the full texts are in THIRD-PARTY-NOTICES.md and next to
the bundled files). Laid out like the shortcuts page: groups of rows, licenses as key caps.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nightexpress.i18n import N_, _, live
from nightexpress.views.theme import palette
from nightexpress.views.widgets import ElidedLabel, icon_label, tool_button

KEY_CAP_HEIGHT = 22  # as on the shortcuts page

DESCRIPTION = N_("Проста IDE для зручної роботи з Rust на STM32.")
COPYRIGHT = N_("© 2026 Vadym Voloshchuk. Поширюється за ліцензією MIT.")
TEMPLATES_NOTE = N_(
    "Код, який NightExpress створює з шаблонів для ваших проєктів, поширюється за ліцензією "
    "0BSD: використовуйте його у своїх проєктах за будь-якою ліцензією, не зберігаючи наш "
    "копірайт."
)
SOURCE_NOTE = N_(
    "Повні тексти ліцензій — у файлах LICENSE і THIRD-PARTY-NOTICES.md та поруч із "
    "вбудованими компонентами."
)

# (section, [(name, URL, licenses, copyright holder or note)]) — keep in step with
# THIRD-PARTY-NOTICES.md. Several licenses are alternatives or cover different parts.
Component = tuple[str, str, tuple[str, ...], str]
COMPONENTS: tuple[tuple[str, tuple[Component, ...]], ...] = (
    (
        N_("Входять до складу програми"),
        (
            (
                "Qt for Python (PySide6), Qt WebEngine",
                "https://doc.qt.io/qtforpython-6/",
                ("LGPL-3.0",),
                N_("The Qt Company Ltd. та інші; Qt WebEngine містить Chromium"),
            ),
            (
                "Monaco Editor",
                "https://github.com/microsoft/monaco-editor",
                ("MIT",),
                "Microsoft Corporation",
            ),
            (
                "xterm.js, fit and search addons",
                "https://github.com/xtermjs/xterm.js",
                ("MIT",),
                "The xterm.js authors; SourceLair; Christopher Jeffrey",
            ),
            (
                N_("Lucide (іконки)"),
                "https://lucide.dev",
                ("ISC", "MIT"),
                N_("Lucide Icons and Contributors; Cole Bemis (Feather, частина іконок — MIT)"),
            ),
            (
                N_("Логотип Rust із Simple Icons"),
                "https://simpleicons.org",
                ("CC0 1.0",),
                N_("торговельна марка Rust Foundation"),
            ),
            (
                "pyserial",
                "https://github.com/pyserial/pyserial",
                ("BSD-3-Clause",),
                "Chris Liechti",
            ),
            (
                "pyelftools",
                "https://github.com/eliben/pyelftools",
                ("Unlicense",),
                N_("суспільне надбання"),
            ),
            ("tomlkit", "https://github.com/python-poetry/tomlkit", ("MIT",), "Sébastien Eustace"),
            ("Mistune", "https://github.com/lepture/mistune", ("BSD-3-Clause",), "Hsiaoming Yang"),
            ("Pygments", "https://pygments.org", ("BSD-2-Clause",), "Georg Brandl and others"),
            (
                "Jinja2, MarkupSafe",
                "https://github.com/pallets/jinja",
                ("BSD-3-Clause",),
                "Pallets",
            ),
            (
                N_("pywinpty (лише Windows)"),
                "https://github.com/andfoy/pywinpty",
                ("MIT",),
                "Spyder Project Contributors",
            ),
        ),
    ),
    (
        N_("Викликаються окремо, до складу не входять"),
        (
            (
                "Rust: rustup, cargo, rustc, rustfmt",
                "https://www.rust-lang.org",
                ("MIT", "Apache-2.0"),
                "The Rust Project Contributors",
            ),
            ("probe-rs", "https://probe.rs", ("MIT", "Apache-2.0"), "probe-rs contributors"),
            (
                "dfu-util",
                "https://dfu-util.sourceforge.net",
                ("GPL-2.0-or-later",),
                "dfu-util contributors",
            ),
            (
                N_("embassy-stm32, cortex-m та інші крейти"),
                "https://embassy.dev",
                ("MIT", "Apache-2.0"),
                N_("автори крейтів, які завантажує Cargo для ваших проєктів"),
            ),
        ),
    ),
)

QT_NOTE = N_(
    "Qt використовується як окрема бібліотека з динамічним підключенням, без змін, за "
    "умовами LGPL-3.0. Вихідний код Qt: "
    '<a href="https://code.qt.io">code.qt.io</a>, текст ліцензії: '
    '<a href="https://www.gnu.org/licenses/lgpl-3.0.html">gnu.org/licenses/lgpl-3.0</a>.'
)
TRADEMARKS = N_(
    "NightExpress — незалежний проєкт. Він не пов'язаний зі STMicroelectronics, Rust "
    "Foundation, Microsoft чи The Qt Company і не схвалений ними. STM32 і ST-LINK — "
    "торговельні марки STMicroelectronics; Rust, Cargo і логотип Rust — торговельні марки "
    "Rust Foundation; Qt — торговельна марка The Qt Company Ltd. Інші назви належать їхнім "
    "власникам."
)


class ComponentRow(QWidget):
    """One component in the look of a shortcut row: the name and its copyright holder on the
    left, the licenses as caps on the right. A click opens the component's page."""

    def __init__(self, component: Component, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        name, self.url, self.licenses, holder = component
        self.name, self.holder = _(name), _(holder)  # proper names have no translation
        self.setObjectName("shortcutRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{self.holder}\n{self.url}")  # the holder may be cut short
        self.setAccessibleName(f"{self.name}, {' / '.join(self.licenses)}")
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        name = QLabel(self.name, self)
        holder = ElidedLabel(self.holder, self)
        holder.setObjectName("aboutHolder")
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(1)
        texts.addWidget(name)
        texts.addWidget(holder)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(4)
        layout.addLayout(texts, 1)
        layout.addSpacing(8)
        for number, license_name in enumerate(self.licenses):
            if number:
                separator = QLabel("/", self)
                separator.setObjectName("mutedText")
                layout.addWidget(separator)
            cap = QLabel(license_name, self)
            cap.setObjectName("keyCap")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setFixedHeight(KEY_CAP_HEIGHT)
            layout.addWidget(cap)

    def open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self.url))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.open_page()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.open_page()
        else:
            super().keyPressEvent(event)


class AboutDialog(QDialog):
    def __init__(self, app_name: str, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        live(self.setWindowTitle, N_("Про програму"))
        self.resize(520, 640)

        # On macOS the dialog is a sheet without a title bar: the title and the close button
        # are its own.
        title = QLabel(_("Про програму"), self)
        title.setObjectName("panelTitle")
        self.close_button = tool_button("x", N_("Закрити"), self)
        self.close_button.clicked.connect(self.reject)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.close_button)

        content = QWidget(self)
        content.setObjectName("shortcutsList")
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 6, 0)
        rows.setSpacing(0)

        # The application itself: logo and name as in the window header, then the facts.
        name = QLabel(app_name, content)
        name.setObjectName("appTitle")
        self.version = QLabel(_("Версія {version}").format(version=version), content)
        self.version.setObjectName("mutedText")
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(name)
        names.addWidget(self.version)
        brand = QHBoxLayout()
        brand.setContentsMargins(10, 4, 10, 10)
        brand.setSpacing(14)
        brand.addWidget(icon_label("cpu", 40, palette.ACCENT_BRIGHT, content))
        brand.addLayout(names, 1)
        rows.addLayout(brand)
        self.description = self._text(_(DESCRIPTION), content)
        self.copyright = self._text(_(COPYRIGHT), content, muted=True)
        rows.addWidget(self.description)
        rows.addSpacing(4)
        rows.addWidget(self.copyright)

        self.rows: list[ComponentRow] = []
        for section, components in COMPONENTS:
            rows.addWidget(self._group(_(section), content))
            for component in components:
                row = ComponentRow(component, content)
                rows.addWidget(row)
                self.rows.append(row)

        rows.addWidget(self._group(_("Ліцензії й торговельні марки"), content))
        self.templates_note = self._text(_(TEMPLATES_NOTE), content, muted=True)
        self.qt_note = self._text(_(QT_NOTE), content, muted=True)
        self.source_note = self._text(_(SOURCE_NOTE), content, muted=True)
        self.trademarks = self._text(_(TRADEMARKS), content, muted=True)
        notes = (self.templates_note, self.qt_note, self.source_note, self.trademarks)
        for note in notes:
            rows.addSpacing(8)
            rows.addWidget(note)
        rows.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 14, 16)
        layout.setSpacing(12)
        layout.addLayout(head)
        layout.addWidget(scroll, 1)
        # Otherwise the close button would open with a keyboard focus outline.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

    @staticmethod
    def _group(title: str, parent: QWidget) -> QLabel:
        header = QLabel(title, parent)
        header.setObjectName("shortcutGroup")
        header.setIndent(0)  # A styled frame adds an implicit text indent by default.
        return header

    @staticmethod
    def _text(text: str, parent: QWidget, muted: bool = False) -> QLabel:
        label = QLabel(text, parent)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        label.setContentsMargins(10, 0, 10, 0)  # in line with the text of the rows
        if muted:
            label.setObjectName("mutedText")
        return label
