"""First-run tips: bubbles that point at controls, one after another."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QWidget

from nightexpress.models.state import Screen
from nightexpress.views.main_window import UART_TAB, MainWindow
from nightexpress.views.tips import Tip, TipTour

pytestmark = pytest.mark.ui


@pytest.fixture
def host(qtbot) -> QWidget:
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.resize(1000, 700)
    widget.show()
    return widget


def tips(*targets: QRect) -> list[Tip]:
    return [Tip(f"tip {i}", "some text " * 8, lambda t=t: t) for i, t in enumerate(targets)]


def test_tour_walks_through_the_tips(qtbot, host: QWidget) -> None:
    called: list[int] = []
    steps = tips(QRect(100, 50, 80, 30), QRect(500, 640, 80, 30))
    steps.append(Tip("last", "x", lambda: QRect(900, 50, 60, 30), before=lambda: called.append(1)))
    tour = TipTour(host, steps)
    tour.start()
    bubble = tour.bubble
    assert tour.index == 0 and bubble.isVisible()
    assert bubble.step.text() == "1 з 3" and bubble.next_button.text() == "Далі"
    assert bubble.arrow_up and bubble.y() >= 80  # below the target near the top
    assert bubble.height() >= bubble._layout.totalHeightForWidth(bubble.width())  # no clipping

    tour.next()
    assert not bubble.arrow_up and bubble.geometry().bottom() <= 640  # no room below: above

    tour.next()
    assert called == [1]  # "before" ran (e.g. showing the bottom panel)
    assert bubble.next_button.text() == "Зрозуміло" and not bubble.skip_button.isVisible()
    assert bubble.geometry().right() <= host.width()  # kept inside the window near an edge

    with qtbot.waitSignal(tour.finished, timeout=1000):
        bubble.next_button.click()
    assert tour.index == -1


def test_tour_can_be_skipped(qtbot, host: QWidget) -> None:
    tour = TipTour(host, tips(QRect(10, 10, 50, 20), QRect(10, 10, 50, 20)))
    tour.start()
    with qtbot.waitSignal(tour.finished, timeout=1000):
        tour.bubble.skip_button.click()
    assert tour.index == -1


def test_first_run_tour_points_at_device_upload_and_uart(qtbot, dispose) -> None:
    window = MainWindow("NightExpress", "0.1.0")
    dispose(window)
    window.resize(1400, 900)
    window.show()
    window.set_screen(Screen.EDITOR)
    window.bottom_panel.hide()
    tour = window.start_first_run_tour()
    assert window.tour is tour
    titles = []
    for step in range(3):
        if step:
            tour.next()
        titles.append(tour.bubble.title.text())
        center = tour._tips[tour.index].target().center().x()
        assert tour.bubble.x() <= center <= tour.bubble.geometry().right()  # aimed at it
    assert titles == ["Виберіть пристрій", "Зібрати й прошити", "Вивід плати — у вкладці UART"]
    assert window.bottom_panel.isVisible()  # the UART tip shows the panel first
    tab = window.bottom_tabs.tabBar().tabRect(UART_TAB)
    assert tab.width() > 0
