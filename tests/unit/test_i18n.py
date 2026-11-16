"""Interface languages: the translation functions, the bundled catalogs and the choice."""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from nightexpress import i18n
from nightexpress.i18n import N_, _, ngettext, pgettext
from nightexpress.models.entities.language import Language, LanguageError
from nightexpress.services.language_catalog import SYSTEM_LANGUAGE, LanguageCatalog

ROOT = Path(__file__).resolve().parents[2]


def make_locale(root: Path, code: str, name: str, messages: dict | None = None) -> None:
    """A language directory as the application ships it; ``messages`` maps id -> translation,
    a (singular, plural) id -> the tuple of forms, a (context, id) id -> translation."""
    directory = root / code
    (directory / "LC_MESSAGES").mkdir(parents=True)
    (directory / "language.toml").write_text(f'name = "{name}"\n', encoding="utf-8")
    if messages is None:
        return
    catalog = Catalog(locale=code)
    for key, text in messages.items():
        if isinstance(text, tuple):
            catalog.add(key, text)
        elif isinstance(key, tuple):
            catalog.add(key[1], text, context=key[0])
        else:
            catalog.add(key, text)
    with (directory / "LC_MESSAGES" / "nightexpress.mo").open("wb") as stream:
        write_mo(stream, catalog)


@pytest.fixture
def locales(tmp_path: Path) -> Path:
    make_locale(
        tmp_path,
        "uk",
        "Українська",
        {("{n} байт", "{n} байтів"): ("{n} байт", "{n} байти", "{n} байтів")},
    )
    make_locale(
        tmp_path,
        "en",
        "English",
        {
            "Зібрати": "Build",
            ("{n} байт", "{n} байтів"): ("{n} byte", "{n} bytes"),
            ("вкладка", "Термінал"): "Terminal",
            ("шрифт", "Термінал"): "Terminal font",
        },
    )
    return tmp_path


def test_without_a_catalog_texts_stay_as_written(tmp_path: Path) -> None:
    i18n.install("pl", tmp_path)
    assert i18n.current() == "pl"
    assert _("Зібрати") == "Зібрати"
    assert pgettext("вкладка", "Термінал") == "Термінал"
    assert ngettext("{n} байт", "{n} байтів", 1) == "{n} байт"
    assert ngettext("{n} байт", "{n} байтів", 5) == "{n} байтів"


def test_install_switches_the_language(locales: Path) -> None:
    i18n.install("en", locales)
    assert i18n.current() == "en"
    assert _("Зібрати") == "Build"
    assert _("Очистити") == "Очистити"  # not translated yet: shown as written
    assert pgettext("вкладка", "Термінал") == "Terminal"
    assert pgettext("шрифт", "Термінал") == "Terminal font"

    i18n.install("uk", locales)
    assert i18n.current() == "uk"
    assert _("Зібрати") == "Зібрати"


def test_a_marked_constant_is_translated_when_shown(locales: Path) -> None:
    title = N_("Зібрати")  # as in a module-level constant: evaluated before the language is known
    assert title == "Зібрати"
    i18n.install("en", locales)
    assert _(title) == "Build"


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("uk", {1: "1 байт", 2: "2 байти", 5: "5 байтів", 11: "11 байтів", 21: "21 байт"}),
        ("en", {1: "1 byte", 2: "2 bytes", 5: "5 bytes", 11: "11 bytes", 21: "21 bytes"}),
    ],
)
def test_plural_forms(locales: Path, language: str, expected: dict[int, str]) -> None:
    i18n.install(language, locales)
    got = {n: ngettext("{n} байт", "{n} байтів", n).format(n=n) for n in expected}
    assert got == expected


def test_bundled_languages() -> None:
    catalog = LanguageCatalog.load()
    assert catalog.languages[0] == Language("uk", "Українська")
    assert len({language.code for language in catalog.languages}) == len(catalog.languages)
    for language in catalog.languages:  # every language ships its compiled catalog
        assert (i18n.locales_directory() / language.code / "LC_MESSAGES/nightexpress.mo").is_file()


def test_languages_are_listed_source_first_then_by_name(locales: Path) -> None:
    make_locale(locales, "pl", "Polski")
    (locales / "notes").mkdir()  # not a language: no language.toml
    catalog = LanguageCatalog.load(locales)
    assert [language.name for language in catalog.languages] == ["Українська", "English", "Polski"]


def test_choice_of_language(locales: Path) -> None:
    catalog = LanguageCatalog.load(locales)
    assert catalog.resolve("en", ["uk-UA"]) == "en"  # an explicit choice wins
    assert catalog.resolve(SYSTEM_LANGUAGE, ["uk-UA", "en-US"]) == "uk"
    assert catalog.resolve(SYSTEM_LANGUAGE, ["de-DE", "uk"]) == "uk"  # the first one we speak
    assert catalog.resolve(SYSTEM_LANGUAGE, ["de-DE", "fr"]) == "en"  # none: English
    assert catalog.resolve(SYSTEM_LANGUAGE) == "en"
    assert catalog.resolve("a-language-that-was-removed", ["uk-UA"]) == "uk"


def test_system_choice_without_english_is_the_source_language(tmp_path: Path) -> None:
    make_locale(tmp_path, "uk", "Українська")
    assert LanguageCatalog.load(tmp_path).resolve(SYSTEM_LANGUAGE, ["de-DE"]) == "uk"


def test_broken_language_directories_fail_loudly(tmp_path: Path) -> None:
    make_locale(tmp_path, "en", "English")
    with pytest.raises(LanguageError, match="source language"):
        LanguageCatalog.load(tmp_path)

    make_locale(tmp_path, "uk", "Українська")
    (tmp_path / "en" / "language.toml").write_text("name = 5\n", encoding="utf-8")
    with pytest.raises(LanguageError, match="'name' must be a string"):
        LanguageCatalog.load(tmp_path)

    (tmp_path / "en" / "language.toml").write_text('name = " "\n', encoding="utf-8")
    with pytest.raises(LanguageError, match="no name"):
        LanguageCatalog.load(tmp_path)

    with pytest.raises(LanguageError, match="not a language code"):
        Language("English", "English")


def test_translation_files_are_up_to_date() -> None:
    """The template, the .po and the .mo files match the texts in the code."""
    spec = importlib.util.spec_from_file_location(
        "update_translations", ROOT / "scripts" / "update_translations.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    stale = [
        str(path.relative_to(ROOT))
        for path, content in script.outputs().items()
        if not path.is_file() or path.read_bytes() != content
    ]
    assert not stale, "run scripts/update_translations.py"


CYRILLIC = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")
TRANSLATION_FUNCTIONS = {"_", "N_", "ngettext", "pgettext"}


def _untranslated_texts(path: Path) -> list[tuple[int, str]]:
    """Ukrainian string literals of a module that are not handed to a translation function."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):  # an f-string cannot be looked up in a catalog
            parts = [v.value for v in node.values if isinstance(v, ast.Constant)]
            if any(CYRILLIC.search(str(part)) for part in parts):
                found.append((node.lineno, ast.get_source_segment(source, node) or ""))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            parent = parents.get(node)
            if not CYRILLIC.search(node.value) or isinstance(parent, ast.Expr | ast.JoinedStr):
                continue  # no Ukrainian, a docstring, or a part of an f-string reported above
            wrapped = (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Name)
                and parent.func.id in TRANSLATION_FUNCTIONS
            )
            if not wrapped:
                enclosing = parent
                while enclosing is not None and not isinstance(enclosing, ast.stmt):
                    enclosing = parents.get(enclosing)
                statement = ast.get_source_segment(source, enclosing) if enclosing else ""
                found.append((node.lineno, statement or node.value))
    return found


def test_no_text_bypasses_the_translation_functions() -> None:
    package = ROOT / "src" / "nightexpress"
    problems = []
    for path in sorted(package.rglob("*.py")):
        name = path.relative_to(package).as_posix()
        for line, text in _untranslated_texts(path):
            problems.append(f"{name}:{line}: {' '.join(text.split())[:90]}")
    assert not problems, "wrap these texts in _() or N_():\n" + "\n".join(problems)


def test_the_check_of_untranslated_texts_sees_them(tmp_path: Path) -> None:
    module = tmp_path / "module.py"
    module.write_text(
        '"""Докстрінг не рахується."""\n'
        "from nightexpress.i18n import N_, _\n"
        'TITLE = N_("Позначено")\n'
        "def texts(n):\n"
        '    return [_("Обгорнуто"), "Голий текст", f"Усього {n}"]\n',
        encoding="utf-8",
    )
    assert [line for line, _text in _untranslated_texts(module)] == [5, 5]


def _bundled_catalog(code: str) -> Catalog:
    po = Path(str(i18n.locales_directory())) / code / "LC_MESSAGES" / "nightexpress.po"
    with po.open("rb") as stream:
        return read_po(stream, locale=code)


BUNDLED = [language.code for language in LanguageCatalog.load().languages]
PLACEHOLDER = re.compile(r"\{[^{}]*\}")
MARKUP = re.compile(r"<[^>]+>")


def test_english_is_bundled() -> None:
    languages = LanguageCatalog.load().languages
    assert languages[0] == Language("uk", "Українська")  # the source language comes first
    assert Language("en", "English") in languages


@pytest.mark.parametrize("code", BUNDLED)
def test_a_bundled_language_has_every_plural_form(code: str) -> None:
    """Ukrainian needs three forms where the code gives two: one, few and many."""
    catalog = _bundled_catalog(code)
    plurals = [message for message in catalog if isinstance(message.id, tuple)]
    assert plurals, "the code has texts with a count"
    for message in plurals:
        assert len(message.string) == catalog.num_plurals, message.id[0]
        assert all(message.string), message.id[0]
    if code == i18n.SOURCE_LANGUAGE:
        # Some forms are alike in other languages ("1 Fehler", "5 Fehler"), never in Ukrainian.
        assert all(len(set(message.string)) > 1 for message in plurals)


@pytest.mark.parametrize("code", [code for code in BUNDLED if code != i18n.SOURCE_LANGUAGE])
def test_a_bundled_translation_is_complete(code: str) -> None:
    for message in _bundled_catalog(code):
        if not message.id:
            continue
        source = message.id[0] if isinstance(message.id, tuple) else message.id
        strings = message.string if isinstance(message.string, tuple) else (message.string,)
        assert all(strings), f"{code}: not translated: {source}"
        assert not message.fuzzy, f"{code}: the translation needs a review: {source}"
        assert not CYRILLIC.search("".join(strings)) or code != "en", source


@pytest.mark.parametrize("code", BUNDLED)
def test_a_translation_keeps_the_placeholders_and_markup(code: str) -> None:
    """``{name}``, ``{dev_id:03X}`` and ``<b>`` are for the program: they stay as written."""
    for message in _bundled_catalog(code):
        if not message.id:
            continue
        # A form for one thing may leave the count out, the form for many has everything.
        source = message.id[-1] if isinstance(message.id, tuple) else message.id
        strings = message.string if isinstance(message.string, tuple) else (message.string,)
        for text in filter(None, strings):
            for pattern in (PLACEHOLDER, MARKUP):
                assert sorted(pattern.findall(text)) == sorted(pattern.findall(source)), (
                    f"{code}: {source!r} -> {text!r}"
                )


def test_texts_of_data_files_follow_the_language(locales: Path) -> None:
    texts = {"uk": "Графіт", "en": "Graphite"}
    assert i18n.localized(texts) == "Графіт"
    i18n.install("en", locales)
    assert i18n.localized(texts) == "Graphite"
    assert i18n.localized({"uk": "Графіт"}) == "Графіт"  # no English: the text of the code
    i18n.install("de", locales)
    assert i18n.localized(texts) == "Graphite"  # neither German nor a reason to assume Ukrainian
    i18n.install("pt_BR", locales)
    assert i18n.localized({"uk": "Тема", "pt": "Tema"}) == "Tema"  # the language without region


def test_text_tables_of_data_files_are_checked() -> None:
    from nightexpress.models.entities.language import localized_texts

    assert localized_texts("Blink") == {"uk": "Blink", "en": "Blink"}  # one for every language
    assert localized_texts({"uk": "Світла", "en": "Light"}) == {"uk": "Світла", "en": "Light"}
    for broken, message in (
        ({"en": "Light"}, "'uk' is missing"),
        ({"uk": "Світла", "en": " "}, "'en' is empty"),
        ({"uk": "Світла", "English": "Light"}, "not a language code"),
        ({}, "expected a text"),
        (5, "expected a text"),
    ):
        with pytest.raises(ValueError, match=message):
            localized_texts(broken)


def test_bundled_data_has_english_texts(locales: Path) -> None:
    """Themes, boards and examples are named in English as well: it is the fallback."""
    from nightexpress.services.board_catalog import BoardCatalog
    from nightexpress.services.theme_catalog import ThemeCatalog

    ukrainian = ThemeCatalog.load()
    assert ukrainian.themes[0].name == "Графіт"
    boards = BoardCatalog.load()
    assert boards.boards[0].display_name == "STM32F103C6 (Blue Pill)"  # chip (nickname)
    assert boards.boards[0].display_name_en == "STM32F103C6 (Blue Pill)"

    i18n.install("en", locales)
    themes = ThemeCatalog.load().themes
    assert themes[0].name == "Graphite"  # the default theme stays first
    assert [theme.name for theme in themes[1:]] == ["High Contrast", "Light", "Ocean"]
    boards = BoardCatalog.load()
    texts = [board.display_name for board in boards.boards]
    texts += [example.description for example in boards.examples]
    assert texts and not any(CYRILLIC.search(text) for text in texts)


def test_a_failing_text_does_not_stop_the_switch(locales: Path) -> None:
    class Owner:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def set_text(self, text: str) -> None:
            self.texts.append(text)

        @i18n.redo_on_switch
        def show_state(self, fail: bool) -> None:
            if fail and i18n.current() == "en":
                raise RuntimeError("Internal C++ object (QLabel) already deleted.")

    owner = Owner()
    owner.show_state(True)
    i18n.live(owner.set_text, N_("Зібрати"))
    with pytest.raises(RuntimeError, match="already deleted"):
        i18n.switch("en", locales)
    assert owner.texts[-1] == "Build"  # the other text of the same owner changed anyway
    owner.show_state(False)  # the owner is still registered
    i18n.switch("uk", locales)
    assert owner.texts[-1] == "Зібрати"


def test_switching_redoes_only_live_objects(locales: Path) -> None:
    class Gone:
        def objectName(self) -> str:  # noqa: N802 - as a deleted Qt object answers
            raise RuntimeError("Internal C++ object (QLabel) already deleted.")

        def set_text(self, text: str) -> None:
            raise AssertionError("a deleted object is not asked")

    gone = Gone()
    i18n.on_change(gone, lambda: None, "nothing")  # registered while it was alive
    gone._i18n_callbacks = {("set_text",): lambda: gone.set_text("x")}
    i18n.switch("en", locales)
