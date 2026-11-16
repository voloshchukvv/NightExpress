from nightexpress.views.panels.ansi import PLAIN, AnsiStyle, parse_ansi


def test_plain_text() -> None:
    assert parse_ansi("hello") == ([("hello", PLAIN)], PLAIN)


def test_bold_bright_colour_and_reset() -> None:
    segments, end = parse_ansi("\x1b[1m\x1b[91merror\x1b[0m: oops")
    assert segments == [("error", AnsiStyle(9, True)), (": oops", PLAIN)]
    assert end == PLAIN


def test_combined_params_and_partial_resets() -> None:
    segments, _ = parse_ansi("\x1b[1;33mwarn\x1b[22mthin\x1b[39mdefault")
    assert segments == [
        ("warn", AnsiStyle(3, True)),
        ("thin", AnsiStyle(3, False)),
        ("default", PLAIN),
    ]


def test_style_carries_over_lines() -> None:
    _, style = parse_ansi("\x1b[32mgreen without reset")
    segments, _ = parse_ansi("next line", style)
    assert segments == [("next line", AnsiStyle(2))]


def test_non_colour_escapes_are_dropped() -> None:
    segments, _ = parse_ansi("\x1b[2Kprogress\x1b[1A")
    assert segments == [("progress", PLAIN)]


def test_empty_sgr_means_reset() -> None:
    segments, _ = parse_ansi("\x1b[31mred\x1b[mplain")
    assert segments == [("red", AnsiStyle(1)), ("plain", PLAIN)]


def test_extended_colours_do_not_leak_into_other_attributes() -> None:
    # "38;5;1" is palette colour 1, not "bold"; a true colour is skipped whole.
    segments, _ = parse_ansi("\x1b[38;5;1mred\x1b[0m\x1b[38;2;1;31;4mrgb\x1b[48;5;1mback")
    assert segments == [("red", AnsiStyle(1, False)), ("rgb", PLAIN), ("back", PLAIN)]
