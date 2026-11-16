"""User-facing numbers, counts and dates in the language of the interface."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QDate, QLocale

from nightexpress import i18n
from nightexpress.i18n import _, ngettext
from nightexpress.models.entities import MemoryUsage

NBSP = "\u202f"  # narrow no-break space as the thousands separator: 32 768


def _locale() -> QLocale:
    return QLocale(i18n.current())


def group_digits(value: int) -> str:
    """32 768 in Ukrainian, 32,768 in English: thousands as the language groups them."""
    separator = _locale().groupSeparator()
    return f"{value:,}".replace(",", NBSP if separator.isspace() else separator)


def decimal(value: float, digits: int) -> str:
    """A number with ``digits`` after the decimal sign of the language: 9,6 or 9.6."""
    return f"{value:.{digits}f}".replace(".", _locale().decimalPoint())


def percent(value: str) -> str:
    """3 % in Ukrainian, 3% in English: the language decides about the space."""
    # i18n: a share in per cent; {value} is a number or "<1". Ukrainian puts a space before %.
    return _("{value} %").format(value=value)


def byte_count(n: int) -> str:
    return ngettext("{count} байт", "{count} байтів", n).format(count=group_digits(n))


def memory_report(usage: MemoryUsage) -> list[str]:
    """Plain-language size lines shown after a successful build."""
    return [
        _("Програма займає {used} ({percent}%) flash. Максимум — {total}.").format(
            used=byte_count(usage.flash_used),
            percent=f"{usage.flash_percent:.0f}",
            total=byte_count(usage.flash_total),
        ),
        _("Змінні займають {used} ({percent}%) RAM, для стека лишається {free}.").format(
            used=byte_count(usage.ram_used),
            percent=f"{usage.ram_percent:.0f}",
            free=byte_count(max(usage.ram_total - usage.ram_used, 0)),
        ),
    ]


def relative_day(moment: datetime | None, today: date | None = None) -> str:
    """ "opened today", "yesterday", "12 November", "3 March 2025" (in English)."""
    if moment is None:
        return ""
    today = today or date.today()
    day = moment.date()
    if day == today:
        return _("відкрито сьогодні")
    if (today - day).days == 1:
        return _("учора")
    if day.year == today.year:  # noqa: SIM108 - each format has a note for translators
        # i18n: a Qt date format: d is the day, MMMM the name of the month.
        pattern = _("d MMMM")
    else:
        # i18n: a Qt date format: d is the day, MMMM the name of the month, yyyy the year.
        pattern = _("d MMMM yyyy")
    return _locale().toString(QDate(day.year, day.month, day.day), pattern)


def home_relative(path: Path, home: Path | None = None, max_length: int = 48) -> str:
    """/Users/dev/Projects/Blink -> ~/Projects/Blink; long paths are shortened in the middle."""
    home = home or Path.home()
    try:
        text = "~/" + path.relative_to(home).as_posix()
    except ValueError:
        text = path.as_posix()
    if len(text) <= max_length:
        return text
    keep = max_length - 1
    return text[: keep // 2] + "…" + text[-(keep - keep // 2) :]
