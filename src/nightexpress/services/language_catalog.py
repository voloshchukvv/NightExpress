"""Interface languages bundled with the application: one directory per language in
``resources/locales``, each with a ``language.toml`` (the name) and a gettext catalog.

Adding a language means adding a directory; no code changes.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from importlib.resources.abc import Traversable

from nightexpress.i18n import FALLBACK_LANGUAGE, SOURCE_LANGUAGE, locales_directory
from nightexpress.models.entities.language import Language, LanguageError

# Not a language but a rule: the language of the operating system.
SYSTEM_LANGUAGE = "system"
DESCRIPTION_FILE = "language.toml"


class LanguageCatalog:
    def __init__(self, languages: list[Language]) -> None:
        self._languages = {language.code: language for language in languages}
        if len(self._languages) != len(languages):
            raise LanguageError("two languages share one code")
        if SOURCE_LANGUAGE not in self._languages:
            raise LanguageError(f"the source language {SOURCE_LANGUAGE!r} is missing")

    @classmethod
    def load(cls, directory: Traversable | None = None) -> LanguageCatalog:
        root = directory if directory is not None else locales_directory()
        languages = []
        for entry in sorted(root.iterdir(), key=lambda e: e.name):
            description = entry / DESCRIPTION_FILE
            if not entry.is_dir() or not description.is_file():
                continue
            try:
                data = tomllib.loads(description.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise LanguageError(f"{entry.name}/{DESCRIPTION_FILE}: {exc}") from exc
            name = data.get("name")
            if not isinstance(name, str):
                raise LanguageError(f"{entry.name}/{DESCRIPTION_FILE}: 'name' must be a string")
            languages.append(Language(entry.name, name))
        return cls(languages)

    @property
    def languages(self) -> tuple[Language, ...]:
        """All languages: the source one first, the rest by name."""
        rest = sorted(
            (lang for lang in self._languages.values() if lang.code != SOURCE_LANGUAGE),
            key=lambda lang: lang.name.casefold(),
        )
        return (self._languages[SOURCE_LANGUAGE], *rest)

    def resolve(self, choice: str, system_languages: Iterable[str] = ()) -> str:
        """The code of the language to show for ``choice`` (a code or SYSTEM_LANGUAGE).

        ``system_languages`` are the user's languages in the order of preference, as
        ``QLocale.system().uiLanguages()`` gives them ("uk-UA", "en"). A choice that names a
        language that no longer exists is treated as the system rule.
        """
        if choice in self._languages:
            return choice
        for tag in system_languages:
            normalized = tag.replace("-", "_")
            for code in (normalized, normalized.split("_")[0].lower()):
                if code in self._languages:
                    return code
        return FALLBACK_LANGUAGE if FALLBACK_LANGUAGE in self._languages else SOURCE_LANGUAGE
