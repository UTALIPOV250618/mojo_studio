from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

from toystudio.app.i18n import install_language
from toystudio.app.widgets.analysis_stage import AnalysisStage
from toystudio.app.widgets.banner import Banner
from toystudio.app.widgets.drop_zone import DropZone, FileRow
from toystudio.app.widgets.project_card import ProjectCard
from toystudio.app.widgets.status_badge import StatusBadge
from toystudio.core.models import MediaKind

GIB = 1024**3
MIN_US = 60 * 1_000_000


def _drop(zone: DropZone, paths: list[Path]) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    zone.dropEvent(ev)


def test_status_badge_has_icon_and_word(qtbot):
    for kind in ("analysis", "ready", "exported", "error", "offline"):
        b = StatusBadge()
        qtbot.addWidget(b)
        b.set_status(kind, 64 if kind == "analysis" else None)
        assert b.text().strip()
        assert b.has_icon()
    b.set_status("analysis", 64)
    assert b.text() == "Анализ · 64%"


def test_banner_support_link_hidden_without_detail(qtbot):
    b = Banner("error")
    qtbot.addWidget(b)
    b.set_content("Нет места", "Освободите диск", [("retry", "Повторить")])
    b.show()
    assert not b.support_link.isVisible()
    b.set_content("Нет места", "Освободите диск", [], support_detail="ENOSPC")
    assert b.support_link.isVisible()
    assert not b.support_detail_label.isVisible()
    b.support_link.click()
    assert b.support_detail_label.isVisible()
    assert b.support_detail_label.text() == "ENOSPC"


def test_banner_action_signal(qtbot):
    b = Banner("info")
    qtbot.addWidget(b)
    b.set_content("t", "b", [("open", "Открыть"), ("close", "Закрыть")])
    with qtbot.waitSignal(b.action) as sig:
        b.action_buttons()[1].click()
    assert sig.args == ["close"]


def test_drop_zone_accepts_folder_recursively(qtbot, tmp_path):
    (tmp_path / "a.MP4").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.mov").write_bytes(b"x")
    zone = DropZone(MediaKind.VIDEO)
    qtbot.addWidget(zone)
    with qtbot.waitSignal(zone.files_dropped) as sig:
        _drop(zone, [tmp_path])
    assert sorted(p.name for p in sig.args[0]) == ["a.MP4", "c.mov"]


def test_drop_zone_music_filters_extensions(qtbot, tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    zone = DropZone(MediaKind.MUSIC)
    qtbot.addWidget(zone)
    with qtbot.waitSignal(zone.files_dropped) as sig:
        _drop(zone, [tmp_path])
    assert [p.name for p in sig.args[0]] == ["a.mp3"]


def test_drop_zone_summary_text(qtbot):
    zone = DropZone(MediaKind.VIDEO)
    qtbot.addWidget(zone)
    rows = [
        FileRow("1", "a.mp4", 6 * GIB, 110 * MIN_US, "ready", None),
        FileRow("2", "b.mp4", int(5.8 * GIB), 110 * MIN_US, "copying", 42),
    ]
    zone.set_rows(rows)
    assert zone.summary_text() == "2 файла · 11,8 ГБ · 03:40:00"
    assert zone.row_status_texts() == ["Готово ✓", "Копируем… 42%"]
    zone.set_rows([])
    assert zone.summary_text() == ""


def test_drop_zone_russian_plurals(qtbot):
    zone = DropZone(MediaKind.VIDEO)
    qtbot.addWidget(zone)

    def summary(n: int) -> str:
        zone.set_rows([FileRow(str(i), "a", GIB, None, "ready", None) for i in range(n)])
        return zone.summary_text()

    assert summary(1).startswith("1 файл ·")
    assert summary(5).startswith("5 файлов ·")
    assert summary(11).startswith("11 файлов ·")
    assert summary(22).startswith("22 файла ·")
    assert summary(21).startswith("21 файл ·")


def test_drop_zone_corrupt_row_has_replace(qtbot):
    zone = DropZone(MediaKind.VIDEO)
    qtbot.addWidget(zone)
    zone.set_rows([FileRow("7", "a.mp4", GIB, None, "corrupt", None)])
    with qtbot.waitSignal(zone.replace_requested) as sig:
        zone.replace_buttons()[0].click()
    assert sig.args == ["7"]


def test_project_card_elides_long_name(qtbot):
    card = ProjectCard()
    qtbot.addWidget(card)
    card.set_data("Той " * 70, "Той", "8 ноября", 3 * MIN_US, "analysis", 64, "ещё ~12 мин", None)
    card.show()
    lines = card.name_lines()
    assert len(lines) == 2
    assert lines[-1].endswith("…")
    assert card.progress_text() == "Анализ · 64% · ещё ~12 мин"


def test_project_card_signals(qtbot):
    card = ProjectCard()
    qtbot.addWidget(card)
    card.set_data("Той", "Той", "8 ноября", None, "ready", None, None, None)
    card.show()
    with qtbot.waitSignal(card.opened):
        qtbot.mouseClick(card, Qt.MouseButton.LeftButton)


def test_analysis_stage_states(qtbot):
    s = AnalysisStage("Находим смены сцен")
    qtbot.addWidget(s)
    s.set_state("later")
    assert s.found_label.text() == "Появится позже"
    s.set_state("done", "Найдено 46 сцен")
    assert s.found_label.text() == "Найдено 46 сцен"
    s.set_state("running", None, 0.4)
    assert s.progress_value() == 40


def test_widgets_retranslate_to_ky(qtbot, qapp):
    badge = StatusBadge()
    banner = Banner("error")
    zone = DropZone(MediaKind.VIDEO)
    stage = AnalysisStage("x")
    for w in (badge, banner, zone, stage):
        qtbot.addWidget(w)
    badge.set_status("ready")
    banner.set_content("t", "b", [], support_detail="d")
    zone.set_rows([FileRow("1", "a", GIB, None, "ready", None)])
    stage.set_state("later")
    before = (badge.text(), banner.support_link.text(), zone.row_status_texts()[0])
    before_zone = (zone.drop_text(), zone.choose_button.text(), stage.found_label.text())
    assert install_language(qapp, "ky")
    qapp.processEvents()  # installTranslator only posts LanguageChange
    after = (badge.text(), banner.support_link.text(), zone.row_status_texts()[0])
    after_zone = (zone.drop_text(), zone.choose_button.text(), stage.found_label.text())
    assert all(a != b for a, b in zip(before, after, strict=True))
    assert all(a != b for a, b in zip(before_zone, after_zone, strict=True))
    assert zone.summary_text() == "1 файл · 1,0 ГБ"
