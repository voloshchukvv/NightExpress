"""Render Markdown in an isolated, read-only Qt WebEngine page."""

from __future__ import annotations

import json
import re
from html import escape, unescape
from importlib import resources
from pathlib import Path
from typing import Any

from mistune import HTMLRenderer, create_markdown
from mistune.util import striptags
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMenu, QWidget

from nightexpress.i18n import _
from nightexpress.models.entities.font import DEFAULT_FONT_SIZE
from nightexpress.views.theme import palette

_PREVIEW_URL = QUrl("file:///nightexpress-markdown-preview.html")
_SCRIPT_WORLD = int(QWebEngineScript.ScriptWorldId.ApplicationWorld)
_HIGHLIGHTER = HtmlFormatter(nowrap=True)
_COLORS = (
    "editor", "text", "text_muted", "border", "surface", "surface_alt", "scrollbar",
    "accent", "accent_bright", "accent_hover", "accent_border", "accent_text",
    "focus", "info", "warning",
)  # fmt: skip


def _project_file(url: QUrl, source: Path, root: Path) -> Path | None:
    """Resolve Markdown-relative and project-root-relative paths inside this project."""
    if url.host() or not url.path():
        return None
    if url.isLocalFile():
        candidate = Path(url.toLocalFile())
    elif url.isRelative():
        name = url.path()
        candidate = root / name.lstrip("/") if name.startswith("/") else source.parent / name
    else:
        return None
    try:
        target = candidate.resolve()
        return target if target.is_relative_to(root) and target.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


class _Renderer(HTMLRenderer):
    def __init__(self, source: Path, root: Path) -> None:
        super().__init__(escape=True)
        self._source = source
        self._root = root
        self._anchors: set[str] = set()

    def heading(self, text: str, level: int, **attrs: Any) -> str:
        slug = re.sub(r"[^\w\s-]", "", unescape(striptags(text)).strip().lower())
        base = re.sub(r"\s", "-", slug)
        anchor = base
        ordinal = 1
        while anchor in self._anchors:
            anchor = f"{base}-{ordinal}"
            ordinal += 1
        self._anchors.add(anchor)
        return super().heading(text, level, **{**attrs, "id": anchor})

    def block_code(self, code: str, info: str | None = None) -> str:
        highlighted = escape(code, quote=False)
        if info and info.strip():
            language = info.split()[0].lower()
            try:
                lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
            except ClassNotFound:
                pass
            else:
                highlighted = highlight(code, lexer, _HIGHLIGHTER)
        return f'<pre tabindex="0"><code>{highlighted}</code></pre>\n'

    def link(self, text: str, url: str, title: str | None = None) -> str:
        link = QUrl(url)
        if link.scheme() in {"http", "https", "mailto"} or url.startswith("#"):
            return super().link(text, url, title)
        path = _project_file(link, self._source, self._root)
        if path is None:
            return text
        local = QUrl.fromLocalFile(str(path))
        local.setFragment(link.fragment())
        return "<a" + self.render_attrs({"href": local.toString(), "title": title}) + f">{text}</a>"

    def image(self, text: str, url: str, title: str | None = None) -> str:
        image = QUrl(url)
        attrs: dict[str, object] = {
            "alt": unescape(striptags(text)),
            "title": title,
            "loading": "lazy",
        }
        if image.scheme() == "https" and image.host():
            attrs["src"] = image.toString()
        elif (path := _project_file(image, self._source, self._root)) is not None:
            attrs["src"] = QUrl.fromLocalFile(str(path)).toString()
        return "<img" + self.render_attrs(attrs) + " />"

    def table(self, content: str) -> str:
        return f'<div class="table-scroll" tabindex="0"><table>\n{content}</table></div>\n'


def _markdown_html(text: str, source: Path, root: Path) -> str:
    parser = create_markdown(
        renderer=_Renderer(source, root),
        plugins=["table", "task_lists", "strikethrough", "footnotes", "url"],
    )
    return parser(text)


class _PreviewPage(QWebEnginePage):
    link_clicked = Signal(QUrl)

    def acceptNavigationRequest(  # noqa: N802
        self, url: QUrl, kind: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        if not is_main_frame:
            return False
        if url.isLocalFile() and not url.host() and url.path() == _PREVIEW_URL.path():
            return True
        if kind == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            self.link_clicked.emit(url)
            return False
        return url.scheme() == "data"  # The fixed HTML shell supplied by setHtml().


class MarkdownPreview(QWebEngineView):
    file_requested = Signal(object, str)  # local project file, optional heading anchor

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page: _PreviewPage | None = None
        self._ready = False
        self._source: tuple[Path, str] | None = None
        self._root: Path | None = None
        self._font_size = DEFAULT_FONT_SIZE + 1

    def set_project_root(self, root: Path | None) -> None:
        resolved = root.resolve() if root is not None else None
        if resolved != self._root:
            self._root = resolved
            self._source = None
            if self._ready:
                self._run("document.querySelector('main').replaceChildren()")

    def set_font_size(self, size: int) -> None:
        self._font_size = size + 1
        self.refresh_theme()

    def show_markdown(self, path: Path, text: str) -> None:
        if (path, text) == self._source:
            return
        reset = self._source is None or self._source[0] != path
        self._source = (path, text)
        if self._page is None:
            self._load_shell()
        elif self._ready:
            self._render(reset_scroll=reset)

    def copy(self) -> None:
        self.triggerPageAction(QWebEnginePage.WebAction.Copy)

    def selectAll(self) -> None:  # noqa: N802 - matches Qt text widgets
        self.triggerPageAction(QWebEnginePage.WebAction.SelectAll)

    def scroll_to_anchor(self, anchor: str) -> None:
        if self._ready and anchor:
            self._run(f"document.getElementById({json.dumps(anchor)})?.scrollIntoView()")

    def refresh_theme(self) -> None:
        if self._page is not None:
            self._page.setBackgroundColor(QColor(palette.EDITOR))
        if self._ready:
            theme = palette.current()
            properties = [f"--{key.replace('_', '-')}:{theme.color(key)}" for key in _COLORS]
            properties.extend(
                (
                    f"--scheme:{'dark' if theme.dark else 'light'}",
                    f"--font-size:{self._font_size}px",
                )
            )
            style = json.dumps(";".join(properties))
            self._run(f"document.documentElement.style.cssText = {style}")

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = QMenu(self)
        copy = menu.addAction(_("Копіювати"), self.copy)
        copy.setEnabled(self.hasSelection())
        menu.addAction(_("Виділити все"), self.selectAll)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _load_shell(self) -> None:
        self._page = _PreviewPage(self)
        self.setPage(self._page)
        settings = self._page.settings()
        attributes = QWebEngineSettings.WebAttribute
        settings.setAttribute(attributes.JavascriptEnabled, False)
        settings.setAttribute(attributes.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(attributes.LocalContentCanAccessRemoteUrls, True)
        self._page.setBackgroundColor(QColor(palette.EDITOR))
        self._page.link_clicked.connect(self._open_link)
        self._page.loadFinished.connect(self._on_loaded)
        css = (resources.files("nightexpress") / "resources/editor/markdown.css").read_text("utf-8")
        shell = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src 'unsafe-inline'; img-src file: https:">
<style>{css}</style>
<main></main>
"""
        # Only this small shell goes through setHtml's data URL. Document content is inserted
        # into it from the isolated application world, including buffers larger than that limit.
        self._page.setHtml(shell, _PREVIEW_URL)

    def _on_loaded(self, ok: bool) -> None:
        if ok and not self._ready:
            self._ready = True
            self.refresh_theme()
            self._render(reset_scroll=True)

    def _render(self, reset_scroll: bool) -> None:
        if self._source is None:
            return
        path, text = self._source
        html = _markdown_html(text, path, self._root or path.parent.resolve())
        scroll = "window.scrollTo(0, 0);" if reset_scroll else ""
        self._run(f"document.querySelector('main').innerHTML = {json.dumps(html)}; {scroll}")

    def _run(self, script: str) -> None:
        if self._page is not None:
            self._page.runJavaScript(script, _SCRIPT_WORLD)

    def _open_link(self, url: QUrl) -> None:
        if url.scheme() in {"http", "https", "mailto"}:
            QDesktopServices.openUrl(url)
        elif url.isLocalFile() and self._source is not None:
            source = self._source[0]
            path = _project_file(url, source, self._root or source.parent.resolve())
            if path is not None:
                self.file_requested.emit(path, url.fragment())
