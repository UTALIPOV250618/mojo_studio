from toystudio.app.widgets.elided import ElidedButton, ElidedLabel

TEXT = "Жаңы тапшырма менен кайра жасоо"


def test_elided_button_tooltip(qtbot):
    b = ElidedButton(TEXT)
    qtbot.addWidget(b)
    b.resize(120, 32)
    b.show()
    assert b.full_text() == TEXT
    assert b.toolTip() == TEXT
    b.resize(600, 32)
    assert b.toolTip() == ""


def test_elided_button_set_text(qtbot):
    b = ElidedButton("a")
    qtbot.addWidget(b)
    b.resize(120, 32)
    b.show()
    assert b.toolTip() == ""
    b.setText(TEXT)
    assert b.full_text() == TEXT and b.toolTip() == TEXT


def test_elided_label(qtbot):
    lbl = ElidedLabel(TEXT)
    qtbot.addWidget(lbl)
    lbl.resize(80, 24)
    lbl.show()
    assert lbl.full_text() == TEXT and lbl.toolTip() == TEXT
    lbl.resize(600, 24)
    assert lbl.toolTip() == ""


def test_elided_can_shrink(qtbot):
    b = ElidedButton(TEXT)
    qtbot.addWidget(b)
    assert b.minimumSizeHint().width() < b.sizeHint().width()
