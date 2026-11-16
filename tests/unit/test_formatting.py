from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo

from nightexpress import i18n
from nightexpress.models.entities import MemoryUsage
from nightexpress.views.formatting import (
    NBSP,
    byte_count,
    decimal,
    group_digits,
    memory_report,
    relative_day,
)


@pytest.fixture
def english(tmp_path: Path) -> None:
    """Install a small English catalog: the bundled one comes with a later stage."""
    catalog = Catalog(locale="en")
    catalog.add(("{count} байт", "{count} байтів"), ("{count} byte", "{count} bytes"))
    catalog.add("учора", "yesterday")
    catalog.add("d MMMM", "MMMM d")
    catalog.add("d MMMM yyyy", "MMMM d, yyyy")
    directory = tmp_path / "en" / "LC_MESSAGES"
    directory.mkdir(parents=True)
    with (directory / "nightexpress.mo").open("wb") as stream:
        write_mo(stream, catalog)
    i18n.install("en", tmp_path)


@pytest.mark.parametrize(
    ("n", "word"),
    [(1, "байт"), (2, "байти"), (4, "байти"), (5, "байтів"), (11, "байтів"), (12, "байтів"),
     (21, "байт"), (22, "байти"), (111, "байтів"), (1064, "байти"), (0, "байтів")],
)  # fmt: skip
def test_plural(n: int, word: str) -> None:
    assert byte_count(n).split(" ")[-1] == word


def test_group_digits() -> None:
    assert group_digits(32768) == f"32{NBSP}768"
    assert group_digits(999) == "999"
    assert byte_count(1064) == f"1{NBSP}064 байти"


def test_memory_report() -> None:
    lines = memory_report(MemoryUsage(1064, 32768, 4, 10240))
    assert (
        lines[0] == f"Програма займає 1{NBSP}064 байти (3%) flash. Максимум — 32{NBSP}768 байтів."
    )
    assert lines[1] == f"Змінні займають 4 байти (0%) RAM, для стека лишається 10{NBSP}236 байтів."


def test_relative_day() -> None:
    from datetime import date, datetime

    today = date(2026, 11, 15)
    assert relative_day(datetime(2026, 11, 15, 9, 0), today) == "відкрито сьогодні"
    assert relative_day(datetime(2026, 11, 14, 23, 0), today) == "учора"
    assert relative_day(datetime(2026, 11, 12, 10, 0), today) == "12 листопада"
    assert relative_day(datetime(2025, 3, 3, 10, 0), today) == "3 березня 2025"
    assert relative_day(None, today) == ""


def test_home_relative() -> None:
    from nightexpress.views.formatting import home_relative

    home = Path("/Users/dev")
    assert home_relative(Path("/Users/dev/Projects/Blink"), home) == "~/Projects/Blink"
    assert home_relative(Path("/opt/blink"), home) == "/opt/blink"
    long = home_relative(Path("/private/tmp/" + "x" * 80 + "/Blink"), home, max_length=30)
    assert len(long) == 30 and "…" in long and long.endswith("/Blink")


def test_decimal_sign_follows_the_language(english: None) -> None:
    assert decimal(9.63, 1) == "9.6"
    assert decimal(0.5, 2) == "0.50"
    i18n.install("uk")
    assert decimal(9.63, 1) == "9,6"
    assert decimal(12, 2) == "12,00"


def test_numbers_and_dates_in_another_language(english: None) -> None:
    from datetime import date, datetime

    assert group_digits(32768) == "32,768"
    assert byte_count(1) == "1 byte"
    assert byte_count(1064) == "1,064 bytes"
    today = date(2026, 11, 15)
    assert relative_day(datetime(2026, 11, 14, 23, 0), today) == "yesterday"
    assert relative_day(datetime(2026, 11, 12, 10, 0), today) == "November 12"
    assert relative_day(datetime(2025, 3, 3, 10, 0), today) == "March 3, 2025"
    # Not translated in this catalog: the text of the code is the fallback.
    assert relative_day(datetime(2026, 11, 15, 9, 0), today) == "відкрито сьогодні"
