import re
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout

from toystudio.app.theme.apply import apply_theme, set_variant
from toystudio.app.widgets.top_bar import TopBar

GENERATED = Path(__file__).resolve().parents[2] / "toystudio" / "app" / "theme" / "generated"


def test_language_switch_live(make_shell):
    w = make_shell()
    assert w.page_title() == "Проекты"
    w.set_language("ky")
    assert w.page_title() == "Долбоорлор"
    w.set_language("ru")
    assert w.page_title() == "Проекты"


def test_stepper_labels_ky(make_shell):
    w = make_shell()
    assert w.top_bar.step_labels() == ["Файлы", "Анализ", "Задание", "Редактор", "Экспорт"]
    w.set_language("ky")
    assert w.top_bar.step_labels() == ["Файлдар", "Талдоо", "Тапшырма", "Редактор", "Экспорт"]


def test_language_buttons_labels_untranslated(make_shell):
    w = make_shell()
    w.set_language("ky")
    assert w.top_bar.language_labels() == ("Русский", "Кыргызча")


def test_top_bar_signals_switch_shell(qtbot, make_shell):
    w = make_shell()
    with qtbot.waitSignal(w.top_bar.language_changed) as sig:
        w.top_bar.click_language("ky")
    assert sig.args == ["ky"]
    assert w.page_title() == "Долбоорлор"
    with qtbot.waitSignal(w.top_bar.theme_changed) as sig:
        w.top_bar.click_theme()
    assert sig.args == ["light"]
    assert "#e9eaed" in QApplication.instance().styleSheet()


def test_theme_switch(make_shell):
    make_shell()
    apply_theme(QApplication.instance(), "light")
    assert "#e9eaed" in QApplication.instance().styleSheet()
    apply_theme(QApplication.instance(), "dark")
    assert "#0f1012" in QApplication.instance().styleSheet()


def test_minimum_size(make_shell):
    w = make_shell()
    assert w.minimumSize() == QSize(1280, 720)


def test_top_bar_height_and_name(make_shell):
    w = make_shell()
    assert w.top_bar.objectName() == "TopBar"
    assert w.top_bar.height() == 48 or w.top_bar.minimumHeight() == 48


def test_set_step_reachability(make_shell):
    w = make_shell()
    w.top_bar.set_step(1, 2)
    assert w.top_bar.step_enabled() == [True, True, True, False, False]
    assert w.top_bar.current_step() == 1


def test_set_variant_updates_property(qtbot):
    lbl = QLabel("x")
    qtbot.addWidget(lbl)
    set_variant(lbl, "variant", "primary")
    assert lbl.property("variant") == "primary"


def _rule_pos(qss: str, selector_re: str) -> int:
    m = list(re.finditer(selector_re, qss))
    assert m, selector_re
    return m[-1].start()


def test_focus_rule_after_every_button_variant():
    for theme in ("dark", "light"):
        qss = (GENERATED / f"theme_{theme}.qss").read_text("utf-8")
        for sel in ('[variant="primary"]', '[variant="ghost"]', '[chip="true"]'):
            base = _rule_pos(qss, re.escape(f"QPushButton{sel} {{"))
            focus = _rule_pos(qss, re.escape(f"QPushButton{sel}:focus"))
            assert focus > base, sel


def test_no_blanket_widget_background():
    qss = (GENERATED / "theme_dark.qss").read_text("utf-8")
    assert not re.search(r"QMainWindow,\s*QWidget\s*\{", qss)


def test_label_inside_card_does_not_paint_bg000(qtbot):
    card = QFrame()
    card.setObjectName("Card")
    lay = QVBoxLayout(card)
    lbl = QLabel("hello")
    lay.addWidget(lbl)
    qtbot.addWidget(card)
    apply_theme(QApplication.instance(), "dark")
    card.resize(200, 100)
    card.show()
    img = card.grab().toImage()
    p = lbl.mapTo(card, lbl.rect().center())
    assert QColor(img.pixel(p.x() + 40, p.y())) != QColor("#0f1012")
    assert QColor(img.pixel(p.x() + 40, p.y())) == QColor("#1e2125")


def test_stepper_buttons_exclusive(qtbot):
    bar = TopBar()
    qtbot.addWidget(bar)
    bar.set_step(1, 2)
    bar._steps[1].click()  # clicking the current step must not uncheck it
    assert [b.isChecked() for b in bar._steps] == [False, True, False, False, False]
    bar._steps[2].click()
    assert [b.isChecked() for b in bar._steps] == [False, False, True, False, False]
