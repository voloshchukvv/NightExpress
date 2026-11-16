from datetime import datetime

from nightexpress.models.state import DisplayMode
from nightexpress.views.panels.uart_render import MonitorRenderer

NOW = datetime(2026, 11, 15, 10, 24, 7, 123000)


def test_text_lines_and_crlf() -> None:
    r = MonitorRenderer()
    assert r.render(b"Hello #1\r\nHello #2\r", DisplayMode.TEXT, False) == "Hello #1\nHello #2\n"
    assert r.render(b"\nnext", DisplayMode.TEXT, False) == "next"  # "\n" of a split "\r\n"


def test_utf8_split_across_reads() -> None:
    r = MonitorRenderer()
    data = "привіт".encode()
    assert r.render(data[:3], DisplayMode.TEXT, False) == "п"
    assert r.render(data[3:], DisplayMode.TEXT, False) == "ривіт"


def test_invalid_bytes_do_not_crash() -> None:
    assert MonitorRenderer().render(b"\xff\xfeok", DisplayMode.TEXT, False).endswith("ok")


def test_timestamps_at_line_starts_only() -> None:
    r = MonitorRenderer()
    out = r.render(b"a\nb", DisplayMode.TEXT, True, NOW)
    assert out == "[10:24:07.123] a\n[10:24:07.123] b"
    assert r.render(b"c\n", DisplayMode.TEXT, True, NOW) == "c\n"  # same line continues


def test_hex_mode() -> None:
    r = MonitorRenderer()
    assert r.render(b"Hi\n", DisplayMode.HEX, False) == "48 69 0A \n"
    out = r.render(bytes(range(0x20, 0x34)), DisplayMode.HEX, True, NOW)  # no 0x0A inside
    first, second = out.split("\n")
    assert first.startswith("[10:24:07.123] 20 21") and first.endswith("2F ")  # 16 per line
    assert second == "[10:24:07.123] 30 31 32 33 "
