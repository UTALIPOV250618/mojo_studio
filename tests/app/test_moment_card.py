from PySide6.QtGui import QPixmap

from toystudio.app.i18n import install_language
from toystudio.app.theme.apply import apply_theme
from toystudio.app.widgets.moment_card import MomentCard
from toystudio.core.models import Mark, MomentType, QualityLabel


def _card(qtbot, **kw):
    from PySide6.QtWidgets import QApplication

    apply_theme(QApplication.instance(), "dark")
    card = MomentCard()
    qtbot.addWidget(card)
    args = {
        "type": MomentType.TOAST,
        "start_us": 754_000_000,
        "duration_us": 48_000_000,
        "quality": QualityLabel.CLEAR,
        "preview": None,
        "reasons": ["Звучит тост, зал слушает"],
        "mark": Mark.NONE,
    }
    args.update(kw)
    card.set_data(**args)
    return card


def test_moment_card_texts_and_neutral_badge(qtbot):
    card = _card(qtbot)
    assert card.type_text() == "Тост"
    assert card.time_text() == "12:34 · 00:48"
    assert card.reason_text() == "Звучит тост, зал слушает"
    assert card.quality_text() == "чётко"
    assert card.mark_text() == ""
    assert card.badge_is_neutral()
    names = {
        MomentType.DANCE: "Танец",
        MomentType.ENTRANCE: "Выход молодых",
        MomentType.GIFTS: "Подарки",
        MomentType.APPLAUSE: "Аплодисменты",
        MomentType.MUSIC: "Музыка",
    }
    for t, text in names.items():
        card.set_data(t, 0, 5_000_000, QualityLabel.DARK, None, [], Mark.NONE)
        assert card.type_text() == text
        assert card.reason_text() == ""
    assert card.quality_text() == "темновато"
    card.set_data(MomentType.TOAST, 0, 5_000_000, QualityLabel.SHAKY, None, ["a", "b"], Mark.MUST)
    assert card.quality_text() == "трясётся"
    assert card.reason_text() == "a"  # one reason line
    assert card.mark_text() == "★"
    card.set_data(MomentType.TOAST, 0, 5_000_000, QualityLabel.CLEAR, None, [], Mark.EXCLUDE)
    assert card.mark_text() == "⊖"


def test_moment_card_missing_preview_shows_placeholder(qtbot):
    card = _card(qtbot, preview=None)
    assert not card.has_preview()
    pm = QPixmap(32, 18)
    pm.fill()
    card.set_data(MomentType.TOAST, 0, 1_000_000, QualityLabel.CLEAR, pm, [], Mark.NONE)
    assert card.has_preview()
    card.set_data(MomentType.TOAST, 0, 1_000_000, QualityLabel.CLEAR, QPixmap(), [], Mark.NONE)
    assert not card.has_preview()


def test_moment_card_retranslates_to_ky(qtbot, qapp):
    card = _card(qtbot, type=MomentType.DANCE, quality=QualityLabel.DARK)
    assert install_language(qapp, "ky")
    qapp.processEvents()  # installTranslator only posts LanguageChange
    try:
        assert card.type_text() != "Танец"
        assert card.quality_text() != "темновато"
    finally:
        install_language(qapp, "ru")
    qapp.processEvents()
    assert card.type_text() == "Танец"
    assert card.quality_text() == "темновато"
