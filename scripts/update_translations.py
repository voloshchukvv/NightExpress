#!/usr/bin/env python3
"""Collect the interface texts from the code and bring the translation catalogs up to date.

The texts wrapped in ``_``, ``N_``, ``ngettext`` and ``pgettext`` (see ``nightexpress/i18n.py``)
go to the template ``resources/locales/nightexpress.pot``; every language directory gets its
``.po`` merged with the template and compiled to the ``.mo`` the application reads.

    .venv/bin/python scripts/update_translations.py           # update the files
    .venv/bin/python scripts/update_translations.py --check   # fail if they are out of date
    .venv/bin/python scripts/update_translations.py --init pl # start a new language

A text that changed in the code keeps its old translation marked "fuzzy": it is not used
until someone reviews it and removes the mark.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "nightexpress"
LOCALES = PACKAGE / "resources" / "locales"
DOMAIN = "nightexpress"
TEMPLATE = LOCALES / f"{DOMAIN}.pot"
DESCRIPTION_FILE = "language.toml"
KEYWORDS = {"_": None, "N_": None, "ngettext": (1, 2), "pgettext": ((1, "c"), 2)}
# A comment that starts with this tag right above a text is shown to the translator.
COMMENT_TAGS = ("i18n:",)
WIDTH = 100


def _version() -> str:
    namespace: dict[str, str] = {}
    exec((PACKAGE / "__init__.py").read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


def _read(path: Path, locale: str | None = None) -> Catalog | None:
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return read_po(stream, locale=locale, domain=DOMAIN)


def build_template() -> Catalog:
    template = Catalog(project="NightExpress", version=_version(), charset="utf-8")
    template.header_comment = "# Interface texts of NightExpress. The source language is Ukrainian."
    old = _read(TEMPLATE)
    if old is not None:  # keep the date: a run that finds nothing new must change nothing
        template.creation_date = old.creation_date
    found = extract_from_dir(
        str(PACKAGE),
        method_map=[("resources/**", "ignore"), ("**.py", "python")],
        keywords=KEYWORDS,
        comment_tags=COMMENT_TAGS,
        strip_comment_tags=True,
    )
    for filename, lineno, message, comments, context in found:
        location = (f"src/nightexpress/{filename}", lineno)
        template.add(message, None, [location], auto_comments=comments, context=context)
    return template


def merged(code: str, template: Catalog) -> Catalog:
    """The catalog of one language with today's texts: translations kept, removed texts gone."""
    catalog = _read(LOCALES / code / "LC_MESSAGES" / f"{DOMAIN}.po", locale=code)
    if catalog is None:
        catalog = Catalog(
            locale=code, project=template.project, version=template.version, charset="utf-8"
        )
        catalog.header_comment = f"# {code}: translation of the NightExpress interface."
    catalog.update(template)
    catalog.project, catalog.version = template.project, template.version
    catalog.fuzzy = False  # Babel marks the header of a new catalog; it is complete as it is
    return catalog


def po_bytes(catalog: Catalog, *, template: bool = False) -> bytes:
    out = io.BytesIO()
    write_po(
        out,
        catalog,
        width=WIDTH,
        sort_by_file=True,
        include_lineno=False,  # line numbers would change the files on every edit of the code
        ignore_obsolete=not template,
    )
    return out.getvalue()


def mo_bytes(catalog: Catalog) -> bytes:
    out = io.BytesIO()
    write_mo(out, catalog)
    return out.getvalue()


def language_codes() -> list[str]:
    return sorted(d.name for d in LOCALES.iterdir() if (d / DESCRIPTION_FILE).is_file())


def outputs() -> dict[Path, bytes]:
    template = build_template()
    files = {TEMPLATE: po_bytes(template, template=True)}
    for code in language_codes():
        catalog = merged(code, template)
        directory = LOCALES / code / "LC_MESSAGES"
        files[directory / f"{DOMAIN}.po"] = po_bytes(catalog)
        files[directory / f"{DOMAIN}.mo"] = mo_bytes(catalog)
    return files


def init_language(code: str) -> None:
    directory = LOCALES / code
    description = directory / DESCRIPTION_FILE
    if description.exists():
        sys.exit(f"{code}: the language already exists")
    directory.mkdir(parents=True)
    description.write_text(
        "# The name shown in the list of languages, written in the language itself.\n"
        f'name = "{code}"\n',
        encoding="utf-8",
    )
    print(f"created {description.relative_to(ROOT)}: put the name of the language there")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="change nothing, fail if stale")
    parser.add_argument("--init", metavar="CODE", help="create the directory of a new language")
    args = parser.parse_args()
    if args.init:
        init_language(args.init)

    stale = []
    for path, content in outputs().items():
        if path.is_file() and path.read_bytes() == content:
            continue
        stale.append(path.relative_to(ROOT))
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    for path in stale:
        print(("stale: " if args.check else "updated: ") + str(path))
    if args.check and stale:
        print("run scripts/update_translations.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
