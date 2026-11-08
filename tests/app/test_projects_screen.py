import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QInputDialog, QMessageBox

from toystudio.app.i18n import install_language
from toystudio.app.screens import projects as projects_mod
from toystudio.app.screens.projects import ProjectsScreen
from toystudio.app.viewmodels import projects_vm as vm_mod
from toystudio.app.viewmodels.projects_vm import ProjectsVM
from toystudio.core.models import (
    STAGE_ORDER,
    MediaKind,
    StageName,
    StageState,
    ToiKind,
)
from toystudio.storage.app_store import AppStore
from toystudio.storage.project_store import ProjectStore


@pytest.fixture
def app_store(tmp_path):
    s = AppStore(tmp_path / "app.db")
    yield s
    s.close()


def make_project(
    tmp_path: Path,
    app: AppStore,
    name: str,
    *,
    status: str = "files",
    updated: datetime = datetime(2026, 11, 1),
    video: Path | None = None,
    stage_state: StageState | None = None,
) -> Path:
    root = tmp_path / "projects" / name
    s = ProjectStore.create(root, name, ToiKind.WEDDING, date(2026, 10, 12))
    if video is not None:
        mid, _ = s.add_media(video, MediaKind.VIDEO, video.stat().st_size, copied=False)
        if stage_state is not None:
            for st in STAGE_ORDER:
                s.set_stage(mid, st, stage_state)
            s.set_stage(0, StageName.SYNC, stage_state)
    s.close()
    app.register_project(root, name, status, updated)
    return root


def new_screen(qtbot, app_store, is_busy=None):
    vm = ProjectsVM(app_store, is_busy) if is_busy else ProjectsVM(app_store)
    screen = ProjectsScreen(vm)
    qtbot.addWidget(screen)
    vm.reload()
    return vm, screen


def drop(screen, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    screen.dropEvent(ev)


def test_empty_state_when_no_projects(qtbot, app_store):
    _, screen = new_screen(qtbot, app_store)
    screen.show()
    assert screen.empty_state.isVisible()
    assert screen.card_roots() == []


def test_cards_sorted_analysis_first(qtbot, app_store, tmp_path):
    a = make_project(tmp_path, app_store, "A", updated=datetime(2026, 11, 5))
    b = make_project(tmp_path, app_store, "B", updated=datetime(2026, 11, 4))
    c = make_project(tmp_path, app_store, "C", status="analysis", updated=datetime(2026, 10, 1))
    _, screen = new_screen(qtbot, app_store)
    screen.show()
    assert not screen.empty_state.isVisible()
    assert screen.card_roots() == [c.resolve(), a.resolve(), b.resolve()]


def test_drop_folder_on_empty_window_emits_new_requested(qtbot, app_store, tmp_path):
    vm, screen = new_screen(qtbot, app_store)
    folder = tmp_path / "Свадьба видео"
    folder.mkdir()
    (folder / "a.MP4").write_bytes(b"x")
    (folder / "notes.txt").write_text("x")
    sub = folder / "cam2"
    sub.mkdir()
    (sub / "b.mov").write_bytes(b"x")
    with qtbot.waitSignal(vm.new_requested) as sig:
        drop(screen, [folder])
    assert sorted(p.name for p in sig.args[0]) == ["a.MP4", "b.mov"]


def test_card_click_opens_saved_step(qtbot, app_store, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    root = make_project(
        tmp_path,
        app_store,
        "P",
        status="analysis",
        video=video,
        stage_state=StageState.PENDING,
    )
    vm, screen = new_screen(qtbot, app_store)
    screen.show()
    qtbot.waitUntil(lambda: screen.card_at(0).height() > 0)
    with qtbot.waitSignal(vm.open_requested) as sig:
        qtbot.mouseClick(screen.card_at(0), Qt.MouseButton.LeftButton)
    assert sig.args == [root.resolve(), "analysis"]


def test_listing_does_not_touch_running_stage(qtbot, app_store, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    root = make_project(
        tmp_path, app_store, "P", status="analysis", video=video, stage_state=StageState.RUNNING
    )
    _, screen = new_screen(qtbot, app_store)
    assert len(screen.card_roots()) == 1
    con = sqlite3.connect(root / "project.db")
    states = {r[0] for r in con.execute("SELECT state FROM stage_run")}
    con.close()
    assert states == {StageState.RUNNING.value}


def test_screen_retranslates_to_ky(qtbot, qapp, app_store, tmp_path):
    make_project(tmp_path, app_store, "P")
    _, screen = new_screen(qtbot, app_store)
    before = (screen.new_button.text(), screen.filter_box.itemText(0))
    assert install_language(qapp, "ky")
    qapp.sendEvent(screen, QEvent(QEvent.Type.LanguageChange))
    assert (screen.new_button.text(), screen.filter_box.itemText(0)) != before


def test_delete_project_keeps_source_video(qtbot, app_store, tmp_path, monkeypatch):
    video = tmp_path / "source" / "v.mp4"
    video.parent.mkdir()
    video.write_bytes(b"x")
    root = make_project(tmp_path, app_store, "P", video=video)
    _vm, screen = new_screen(qtbot, app_store)
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes,
    )
    screen.delete_project(root)
    assert video.exists()
    assert not root.exists()
    assert app_store.list_projects() == []
    assert screen.card_roots() == []
    assert "не удаляются" in asked[0]  # says that videos are kept


def test_delete_copied_project_warns_about_copies(qtbot, app_store, tmp_path, monkeypatch):
    root = tmp_path / "projects" / "C"
    s = ProjectStore.create(root, "C", ToiKind.TOI, date(2026, 10, 12))
    copy = root / "media" / "v.mp4"
    copy.parent.mkdir()
    copy.write_bytes(b"x")
    s.add_media(copy, MediaKind.VIDEO, 1, copied=True)
    s.close()
    app_store.register_project(root, "C", "files", datetime(2026, 11, 1))
    _, screen = new_screen(qtbot, app_store)
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No,
    )
    screen.delete_project(root)
    assert root.exists()
    assert "Копии видео" in asked[0]


def test_delete_refused_when_busy(qtbot, app_store, tmp_path, monkeypatch):
    root = make_project(tmp_path, app_store, "P", status="analysis")
    _, screen = new_screen(qtbot, app_store, is_busy=lambda r: True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    screen.delete_project(root)
    assert root.exists()
    assert len(app_store.list_projects()) == 1


def test_open_folder_uses_desktop_services(qtbot, app_store, tmp_path, monkeypatch):
    root = make_project(tmp_path, app_store, "P")
    _, screen = new_screen(qtbot, app_store)
    opened = []
    monkeypatch.setattr(
        projects_mod.QDesktopServices, "openUrl", lambda url: opened.append(url) or True
    )
    screen.open_folder(root)
    assert opened == [QUrl.fromLocalFile(str(root))]


def test_rename_updates_both_databases(qtbot, app_store, tmp_path, monkeypatch):
    root = make_project(tmp_path, app_store, "Old")
    _, screen = new_screen(qtbot, app_store)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Новое имя", True))
    screen.rename_project(root)
    assert app_store.list_projects()[0].name == "Новое имя"
    assert ProjectStore.peek(root).name == "Новое имя"
    assert screen.card_at(0).name_lines() == ["Новое имя"]


def test_context_menu_rename_disabled_when_busy(qtbot, app_store, tmp_path):
    root = make_project(tmp_path, app_store, "P", status="analysis")
    _, screen = new_screen(qtbot, app_store, is_busy=lambda r: True)
    texts = {a.text(): a.isEnabled() for a in screen.build_menu(root).actions()}
    assert list(texts) == [
        "Переименовать",
        "Открыть папку",
        "Удалить проект (видео не удаляется)",
    ]
    assert texts["Переименовать"] is False
    assert texts["Открыть папку"] is True


def test_progress_and_badges(qtbot, app_store, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    done = make_project(
        tmp_path, app_store, "D", status="analysis_done", video=video, stage_state=StageState.DONE
    )
    run = make_project(tmp_path, app_store, "R", status="analysis", updated=datetime(2026, 1, 1))
    plain = make_project(tmp_path, app_store, "F", updated=datetime(2026, 1, 2))
    vm, screen = new_screen(qtbot, app_store)
    screen.show()
    vm.set_progress(run, 64, 720.0)
    cards = dict(zip(screen.card_roots(), [screen.card_at(i) for i in range(3)], strict=True))
    assert cards[run.resolve()].progress_text() == "Анализ · 64% · ещё ~12 мин"
    assert cards[done.resolve()]._badge.text() == "Анализ завершён"
    assert not cards[plain.resolve()]._badge.isVisible()


def test_search_and_filter(qtbot, app_store, tmp_path):
    make_project(tmp_path, app_store, "Свадьба Айбек")
    make_project(tmp_path, app_store, "Юбилей", status="analysis")
    vm, screen = new_screen(qtbot, app_store)
    vm.search("айбек")
    assert len(screen.card_roots()) == 1
    vm.search("")
    vm.filter_status("analysis")
    assert [i.name for i in vm.items] == ["Юбилей"]


@pytest.mark.parametrize(("width", "cols"), [(1280, 2), (1349, 2), (1350, 3), (1799, 3), (1920, 4)])
def test_grid_columns_at_1280_and_1920(qtbot, app_store, tmp_path, width, cols):
    for n in "ABCDE":
        make_project(tmp_path, app_store, n)
    _, screen = new_screen(qtbot, app_store)
    screen.resize(width, 900)
    screen.show()
    assert screen.columns() == cols
    xs = {screen.card_at(i).mapTo(screen, screen.card_at(i).rect().topLeft()).x() for i in range(5)}
    assert len(xs) == cols


def test_delete_failure_keeps_project_and_shows_message(qtbot, app_store, tmp_path, monkeypatch):
    root = make_project(tmp_path, app_store, "P")
    _vm, screen = new_screen(qtbot, app_store)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    def boom(_p):
        raise PermissionError("locked")

    monkeypatch.setattr(vm_mod.shutil, "rmtree", boom)
    shown = []
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: shown.append((self.text(), self.detailedText()))
    )
    screen.delete_project(root)
    assert len(app_store.list_projects()) == 1
    assert screen.card_roots() == [root.resolve()]
    assert shown[0][0].startswith("Не удалось удалить проект")
    assert "locked" in shown[0][1]


def test_fallback_project_with_media_folder_gets_copies_warning(
    qtbot, app_store, tmp_path, monkeypatch
):
    root = tmp_path / "projects" / "Old"
    (root / "media").mkdir(parents=True)
    (root / "media" / "v.mp4").write_bytes(b"x")
    con = sqlite3.connect(root / "project.db")
    con.execute("PRAGMA user_version=1")
    con.commit()
    con.close()
    app_store.register_project(root, "Old", "files", datetime(2026, 11, 1))
    _vm, screen = new_screen(qtbot, app_store)
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No,
    )
    screen.delete_project(root)
    assert "Копии видео" in asked[0]
