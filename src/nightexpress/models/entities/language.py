"""An interface language the user can choose."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from nightexpress.i18n import FALLBACK_LANGUAGE, SOURCE_LANGUAGE

_CODE_RE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")


class LanguageError(ValueError):
    pass


@dataclass(frozen=True)
class Language:
    code: str  # "uk", "en", "pt_BR": also the name of its directory in resources/locales
    name: str  # in the language itself: "English", "Español"

    def __post_init__(self) -> None:
        if not _CODE_RE.fullmatch(self.code):
            raise LanguageError(f"{self.code!r} is not a language code")
        if not self.name.strip():
            raise LanguageError(f"{self.code}: the language has no name")


def localized_texts(value: object) -> dict[str, str]:
    """A name or description from a data file as ``{language: text}``.

    The file gives either one text (the same in every language) or a table with a text per
    language, where the Ukrainian one is required. Raises ValueError for anything else.
    """
    if isinstance(value, str):
        # The same text in every language: in particular it is the English one too (the
        # English name of a board goes into the README of generated projects).
        value = {SOURCE_LANGUAGE: value, FALLBACK_LANGUAGE: value}
    if not isinstance(value, Mapping) or not value:
        raise ValueError("expected a text or a table of texts by language")
    for language, text in value.items():
        if not isinstance(language, str) or not _CODE_RE.fullmatch(language):
            raise ValueError(f"{language!r} is not a language code")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"the text for {language!r} is empty")
    if SOURCE_LANGUAGE not in value:
        raise ValueError(f"the text for {SOURCE_LANGUAGE!r} is missing")
    return dict(value)
