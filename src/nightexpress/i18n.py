"""Interface language: the text in the code is Ukrainian, catalogs translate it.

The language can change while the application runs. A text that stays on screen is set with
``live()``, a value computed from texts with ``on_change()``: ``switch()`` installs the new
language and runs them again.

Every layer may import this module: it depends on nothing but the standard library. The
catalogs are gettext ``.mo`` files in ``resources/locales/<code>/LC_MESSAGES``; a text without
a translation is shown as written in the code. ``scripts/update_translations.py`` collects the
texts wrapped in the functions below and compiles the catalogs.
"""

from __future__ import annotations

import functools
import gettext as _gettext
import logging
import weakref
from collections.abc import Callable, Iterator, Mapping
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any, TypeVar

DOMAIN = "nightexpress"
# The language of the texts in the code. Its catalog only carries the plural forms.
SOURCE_LANGUAGE = "uk"
# The language shown when the wanted one is missing and the reader may not know Ukrainian.
FALLBACK_LANGUAGE = "en"

# What views take for a text that follows the language: a text marked with N_ (translated
# when shown) or a function that builds the text. See live().
Text = str | Callable[[], str]

_language = SOURCE_LANGUAGE
_catalog: _gettext.NullTranslations = _gettext.NullTranslations()


def locales_directory() -> Traversable:
    return resources.files("nightexpress") / "resources" / "locales"


def install(language: str, directory: Traversable | None = None) -> None:
    """Make ``language`` the one the functions below translate into.

    A language without a catalog still becomes the current one: its texts stay as written.
    """
    global _language, _catalog
    root = directory if directory is not None else locales_directory()
    file = root / language / "LC_MESSAGES" / f"{DOMAIN}.mo"
    if file.is_file():
        with file.open("rb") as stream:
            _catalog = _gettext.GNUTranslations(stream)
    else:
        _catalog = _gettext.NullTranslations()
    _language = language


def current() -> str:
    """The code of the installed language, e.g. "uk"."""
    return _language


def _(text: str) -> str:
    # gettext answers "" with the header of the catalog; an empty text stays empty.
    return _catalog.gettext(text) if text else text


def ngettext(singular: str, plural: str, n: int) -> str:
    """The form for ``n`` things; the count itself is put in by the caller's ``format``."""
    return _catalog.ngettext(singular, plural, n)


def pgettext(context: str, text: str) -> str:
    """Like ``_`` for a word that needs different translations in different places."""
    return _catalog.pgettext(context, text)


def localized(texts: Mapping[str, str]) -> str:
    """The text of the current language out of ``{language: text}``, as the data files keep
    names and descriptions; without it the English one, then the Ukrainian one."""
    for language in (_language, _language.split("_")[0], FALLBACK_LANGUAGE, SOURCE_LANGUAGE):
        if language in texts:
            return texts[language]
    return next(iter(texts.values()), "")


def N_(text: str) -> str:  # noqa: N802 - the conventional gettext name
    """Mark a text of a module-level constant for translation; ``_`` translates it when shown."""
    return text


# Objects with texts to redo after a switch. Each keeps its own callbacks in an attribute, so
# the callbacks (which usually refer to the object) never keep it alive from here.
_owners: weakref.WeakSet[Any] = weakref.WeakSet()
_log = logging.getLogger(__name__)
_CALLBACKS = "_i18n_callbacks"


def _remember(owner: object, key: object, callback: Callable[[], object]) -> None:
    callbacks: dict[object, Callable[[], object]] | None = getattr(owner, _CALLBACKS, None)
    if callbacks is None:
        callbacks = {}
        setattr(owner, _CALLBACKS, callbacks)
    _owners.add(owner)  # again too: a switch drops an owner that looked deleted
    callbacks.pop(key, None)  # the latest state is redone last
    callbacks[key] = callback


def on_change(owner: object, callback: Callable[[], object], key: object = None) -> None:
    """Run ``callback`` now and again after every ``switch()``, as long as ``owner`` lives.

    A later callback with the same ``key`` replaces the earlier one: a view that shows a new
    state registers the text of that state under the same key. Without a key every callback
    is kept.
    """
    _remember(owner, callback if key is None else key, callback)
    callback()


def resolve(text: Text) -> str:
    """The text to show now: a marked text translated, or what the function builds."""
    return _(text) if isinstance(text, str) else text()


def live(setter: Callable[..., object], text: Text, *args: object) -> None:
    """``setter(*args, text)`` now and after every switch.

    ``text`` is either a text marked with ``N_`` (translated with ``_`` each time) or a
    function that builds the whole text: ``lambda: _(LINE_TEXT).format(line=line)``.
    ``setter`` is a bound method such as ``label.setText`` or ``tabs.setTabText`` with the
    tab index in ``args``; the next ``live()`` of the same setter and ``args`` replaces this one.
    """
    owner = setter.__self__  # type: ignore[attr-defined]

    def apply() -> None:
        setter(*args, resolve(text))

    on_change(owner, apply, (setter.__name__, *args))


Method = TypeVar("Method", bound=Callable[..., Any])


def redo_on_switch(method: Method) -> Method:
    """Decorate a method that shows a state with translated texts: after ``switch()`` it runs
    again with the arguments of its last call. Inside it plain ``_()`` is enough."""

    @functools.wraps(method)
    def wrapper(self: object, *args: object, **kwargs: object) -> object:
        # A generator would be empty the second time: keep its items.
        args = tuple(tuple(a) if isinstance(a, Iterator) else a for a in args)
        kwargs = {k: tuple(v) if isinstance(v, Iterator) else v for k, v in kwargs.items()}
        _remember(self, ("redo", method.__name__), lambda: method(self, *args, **kwargs))
        return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def forget(owner: object, key: object) -> None:
    """Stop redoing what ``key`` registered: the object now shows something not translated."""
    callbacks = getattr(owner, _CALLBACKS, None)
    if callbacks is not None:
        callbacks.pop(key, None)


def forget_text(setter: Callable[..., object], *args: object) -> None:
    """The ``live()`` text of ``setter`` is replaced by a text that is not translated."""
    forget(setter.__self__, (setter.__name__, *args))  # type: ignore[attr-defined]


def _phase(key: object) -> int:
    """Texts set by live() first, then the methods that show a state (they may set the same
    widgets and know better), then the rest, e.g. sizes measured from the new texts."""
    if isinstance(key, tuple) and key and key[0] == "redo":
        return 1
    if isinstance(key, tuple) and key and isinstance(key[0], str):
        return 0
    return 2


def _alive(owner: object) -> bool:
    """False for a Qt object whose C++ side is gone (any call on it raises RuntimeError)."""
    probe = getattr(owner, "objectName", None)
    if probe is None:
        return True
    try:
        probe()
    except RuntimeError:
        return False
    return True


def switch(language: str, directory: Traversable | None = None) -> None:
    """Install ``language`` and redo every text registered with ``live`` and ``on_change``.

    One failing callback does not stop the others: the rest of the window still changes, and
    the first error is raised at the end.
    """
    install(language, directory)
    owners = list(_owners)
    errors: list[Exception] = []
    for phase in range(3):
        for owner in owners:
            if not _alive(owner):
                _owners.discard(owner)
                continue
            callbacks = getattr(owner, _CALLBACKS, {})
            for key, callback in list(callbacks.items()):
                if _phase(key) != phase:
                    continue
                try:
                    callback()
                except Exception as exc:
                    _log.exception("could not redo %r of %r in %s", key, owner, language)
                    errors.append(exc)
    if errors:
        raise errors[0]
