from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QPushButton

from toystudio.app.screens import new_project as screen_mod
from toystudio.app.screens.new_project import NewProjectScreen
from toystudio.app.theme.apply import apply_theme
from toystudio.app.viewmodels import new_project_vm as vm_mod
from toystudio.app.viewmodels.new_project_vm import NewProjectVM, drive_of
from toystudio.core.models import MediaInfo, MediaKind, MediaStatus, ToiKind
from toystudio.jobs.protocol import Event
from toystudio.storage.app_store import AppStore
from toystudio.storage.project_store import ProjectStore

GIB = 1024**3


class FakeJobs(QObject):
    progress = Signal(str, object)
    event = Signal(str, object)
    finished = Signal(str)
    failed = Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.started: list[tuple[str, list[str]]] = []
        self.cancelled: list[str] = []

    def start(self, job_id: str, argv: list[str]) -> None:
        self.started.append((job_id, argv))

    def cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)


def info(**kw) -> MediaInfo:
    base = {
        "duration_us": 60_000_000,
        "width": 1920,
        "height": 1080,
        "rotation": 0,
        "fps": 25.0,
        "codec": "h264",
        "hdr": False,
        "has_audio": True,
        "has_video": True,
        "bit_rate": 8_000_000,
        "creation_time": None,
    }
    base.update(kw)
    return MediaInfo(**base)


@pytest.fixture
def app_store(tmp_path):
    s = AppStore(tmp_path / "app.db")
    yield s
    s.close()


@pytest.fixture
def jobs():
    return FakeJobs()


@pytest.fixture
def vm(app_store, jobs, tmp_path, monkeypatch):
    monkeypatch.setattr(vm_mod, "free_bytes", lambda p: 500 * GIB)
    monkeypatch.setattr(vm_mod, "is_removable", lambda p: False)
    v = NewProjectVM(app_store, jobs, tmp_path / "projects")
    yield v
    v.close()


def make_file(tmp_path: Path, name: str, size: int = 1000) -> Path:
    p = tmp_path / "src" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


def import_id(vm: NewProjectVM) -> str:
    return f"import:{vm.root}"


def media_done(vm, jobs, name_or_id, status=MediaStatus.OK, **info_kw):
    """Simulate the import worker finishing one file."""
    row = next(m for m in vm._store.list_media() if m.id == name_or_id or m.path.name == name_or_id)
    if status is MediaStatus.OK:
        vm._store.set_media_info(row.id, info(**info_kw))
    vm._store.set_media_status(row.id, status)
    payload = {"media_id": row.id, "status": status.value}
    jobs.event.emit(import_id(vm), Event("media", payload))
    return row.id


def finish_import(vm, jobs):
    jobs.finished.emit(import_id(vm))


def video_rows(vm):
    return vm.rows(MediaKind.VIDEO)


def test_start_disabled_without_video(vm, jobs, tmp_path):
    assert vm.can_start is False
    vm.add_files(MediaKind.MUSIC, [make_file(tmp_path, "song.mp3")])
    media_done(vm, jobs, "song.mp3")
    finish_import(vm, jobs)
    assert vm.can_start is False


def test_can_start_after_video_is_checked(vm, jobs, tmp_path):
    seen = []
    vm.can_start_changed.connect(seen.append)
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    assert vm.can_start is False  # import still running
    media_done(vm, jobs, "a.mp4")
    assert vm.can_start is False  # job not finished yet
    finish_import(vm, jobs)
    assert vm.can_start is True
    assert seen == [True]


def test_add_files_creates_project_and_starts_import(vm, jobs, app_store, tmp_path):
    vm.name = "Свадьба Айбек"
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    root = tmp_path / "projects" / "Свадьба Айбек"
    assert vm.root == root.resolve() or vm.root == root
    assert (root / "project.db").is_file()
    assert [r.name for r in app_store.list_projects()] == ["Свадьба Айбек"]
    assert [j for j, _ in jobs.started] == [f"import:{vm.root}"]
    argv = jobs.started[0][1]
    assert argv[argv.index("--job") + 1] == "import"
    assert video_rows(vm)[0].state == "checking"


@pytest.mark.parametrize(
    ("name", "taken", "expected"),
    [
        ("", [], "Новый проект 1"),
        ("", ["Новый проект 1", "Новый проект 2"], "Новый проект 3"),
        ('Той: "Айбек"/Нурайым?. ', [], "Той АйбекНурайым"),
        ("Той", ["Той"], "Той (2)"),
        ("Той", ["Той", "Той (2)"], "Той (3)"),
        ("CON", [], "CON_"),
    ],
)
def test_project_folder_naming(vm, jobs, tmp_path, name, taken, expected):
    for t in taken:
        (tmp_path / "projects" / t).mkdir(parents=True)
    vm.name = name
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    assert vm.root.name == expected


def test_duplicate_file_row(vm, jobs, tmp_path):
    f = make_file(tmp_path, "a.mp4")
    vm.add_files(MediaKind.VIDEO, [f])
    vm.add_files(MediaKind.VIDEO, [f])
    states = [r.state for r in video_rows(vm)]
    assert states.count("duplicate") == 1
    assert len(vm._store.list_media()) == 1


def test_second_add_while_import_running_sets_rerun(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "b.mp4")])
    assert len(jobs.started) == 1
    assert len(vm._store.list_media()) == 2
    finish_import(vm, jobs)
    assert len(jobs.started) == 2
    assert jobs.started[1][0] == import_id(vm)
    assert vm.can_start is False  # second import now running
    finish_import(vm, jobs)
    assert len(jobs.started) == 2


def test_disk_shortage_banner_and_disabled_start(vm, jobs, tmp_path, monkeypatch):
    banners = []
    vm.banner.connect(banners.append)
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(vm, jobs, "a.mp4")
    finish_import(vm, jobs)
    assert vm.can_start is True
    monkeypatch.setattr(vm_mod, "free_bytes", lambda p: 1 * GIB)
    vm.recheck()
    check = vm.disk_check()
    assert check.ok is False
    assert check.free == GIB and check.shortage == check.needed - GIB
    assert vm.banner_key == "disk"
    assert banners[-1] == "disk"
    assert vm.can_start is False


def test_disk_check_counts_copy_and_recovers(vm, jobs, tmp_path, monkeypatch):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4", 3 * GIB // 1000)])
    media_done(vm, jobs, "a.mp4")
    finish_import(vm, jobs)
    without = vm.disk_check().needed
    vm.copy = True  # already imported in place: nothing more to copy
    assert vm.disk_check().needed == without
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "b.mp4", 5000)])
    assert vm.disk_check().needed > without + 5000 - 1


def test_removable_turns_copy_on(vm, jobs, tmp_path, monkeypatch):
    monkeypatch.setattr(vm_mod, "is_removable", lambda p: True)
    assert vm.copy is False
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    assert vm.copy is True
    assert vm.removable_hint is True
    assert vm._store.list_media()[0].copied is True


def test_copy_toggle_applies_to_unprocessed_rows_only(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(vm, jobs, "a.mp4")
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "b.mp4")])
    vm.copy = True
    flags = {m.path.name: m.copied for m in vm._store.list_media()}
    assert flags == {"a.mp4": False, "b.mp4": True}
    vm.copy = False
    flags = {m.path.name: m.copied for m in vm._store.list_media()}
    assert flags == {"a.mp4": False, "b.mp4": False}


def test_copy_progress_event_shows_percent(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    mid = vm._store.list_media()[0].id
    ev = Event("copy", {"media_id": mid, "done_bytes": 420, "total_bytes": 1000})
    jobs.event.emit(import_id(vm), ev)
    row = video_rows(vm)[0]
    assert (row.state, row.percent) == ("copying", 42)


def test_events_of_other_jobs_are_ignored(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    mid = vm._store.list_media()[0].id
    jobs.event.emit(
        "import:/elsewhere", Event("copy", {"media_id": mid, "done_bytes": 1, "total_bytes": 2})
    )
    assert video_rows(vm)[0].state == "checking"


def test_corrupt_file_banner(vm, jobs, tmp_path):
    vm.add_files(
        MediaKind.VIDEO, [make_file(tmp_path, "IMG_0419.MP4"), make_file(tmp_path, "ok.mp4")]
    )
    bad = media_done(vm, jobs, "IMG_0419.MP4", MediaStatus.CORRUPT)
    media_done(vm, jobs, "ok.mp4")
    finish_import(vm, jobs)
    assert vm.banner_key == "corrupt"
    assert vm.corrupt_name == "IMG_0419.MP4"
    assert vm.first_bad_id() == bad
    assert vm.can_start is True  # the other file is fine
    vm.exclude_bad()
    assert vm.banner_key is None
    assert [r.state for r in video_rows(vm)] == ["corrupt", "ready"]  # row stays
    assert vm.can_start is True


def test_only_corrupt_file_blocks_start_even_after_exclusion(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "bad.mp4")])
    media_done(vm, jobs, "bad.mp4", MediaStatus.CORRUPT)
    finish_import(vm, jobs)
    vm.exclude_bad()
    assert vm.can_start is False


def test_replace_file_removes_old_and_reimports(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "bad.mp4")])
    bad = media_done(vm, jobs, "bad.mp4", MediaStatus.CORRUPT)
    finish_import(vm, jobs)
    vm.replace_file(bad, make_file(tmp_path, "good.mp4"))
    assert [m.path.name for m in vm._store.list_media()] == ["good.mp4"]
    assert len(jobs.started) == 2


def test_remove_file(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    vm.remove_file(vm._store.list_media()[0].id)
    assert video_rows(vm) == []


def test_default_date_from_creation_time(vm, jobs, tmp_path):
    dates = []
    vm.date_changed.connect(dates.append)
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(vm, jobs, "a.mp4", creation_time="2026-10-12T14:03:11.000000Z")
    assert vm.event_date == date(2026, 10, 12)
    assert dates == [date(2026, 10, 12)]
    assert ProjectStore.peek(vm.root).event_date == date(2026, 10, 12)


def test_manual_date_is_not_overwritten(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    vm.event_date = date(2025, 1, 2)
    media_done(vm, jobs, "a.mp4", creation_time="2026-10-12T14:03:11Z")
    assert vm.event_date == date(2025, 1, 2)


def test_default_date_is_today_without_creation_time(vm):
    assert vm.event_date == date.today()


def test_name_kind_and_date_edits_reach_the_db(vm, jobs, app_store, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    root = vm.root
    vm.name = "Юбилей Бекзат"
    vm.kind = ToiKind.JUBILEE
    vm.event_date = date(2026, 5, 1)
    s = ProjectStore.peek(root)
    assert (s.name, s.kind, s.event_date) == ("Юбилей Бекзат", ToiKind.JUBILEE, date(2026, 5, 1))
    assert app_store.list_projects()[0].name == "Юбилей Бекзат"
    assert root.exists() and root.name != "Юбилей Бекзат"  # folder is not renamed


def test_4k_warning_banner(vm, jobs, tmp_path):
    flags = []
    vm.warning_4k.connect(flags.append)
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(
        vm,
        jobs,
        "a.mp4",
        width=3840,
        height=2160,
        codec="hevc",
        hdr=True,
        duration_us=3600 * 1_000_000,
    )
    assert vm.warning_4k_active is True
    assert flags == [True]
    # 1 h of media at 0.7x realtime
    assert vm.eta_4k_s == pytest.approx(3600 / 0.7)


def test_no_4k_warning_for_full_hd(vm, jobs, tmp_path):
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(vm, jobs, "a.mp4")
    assert vm.warning_4k_active is False


def test_start_analysis(vm, jobs, app_store, tmp_path):
    started = []
    vm.analysis_started.connect(started.append)
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(vm, jobs, "a.mp4")
    finish_import(vm, jobs)
    vm.start_analysis()
    job_id, argv = jobs.started[-1]
    assert job_id == f"analyze:{vm.root}"
    assert argv[argv.index("--job") + 1] == "analyze"
    assert started == [vm.root]
    assert app_store.list_projects()[0].status == "analysis"


def test_start_analysis_refused_when_not_allowed(vm, jobs):
    vm.start_analysis()
    assert jobs.started == []


def test_set_projects_dir_only_before_project_exists(vm, jobs, tmp_path):
    other = tmp_path / "other"
    assert vm.set_projects_dir(other) is True
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    assert vm.root.parent.resolve() == other.resolve()
    assert vm.set_projects_dir(tmp_path / "third") is False


def test_drive_of():
    assert drive_of(Path("D:/Toi"), "win32") == "D:"
    assert drive_of(Path("/Volumes/X"), "darwin") == ""


# ---- screen


def new_screen(qtbot, vm):
    apply_theme(
        qtbot.app
        if hasattr(qtbot, "app")
        else __import__("PySide6.QtWidgets").QtWidgets.QApplication.instance(),
        "dark",
    )
    s = NewProjectScreen(vm)
    qtbot.addWidget(s)
    return s


def test_screen_shows_files_disk_line_and_enables_start(qtbot, vm, jobs, tmp_path):
    s = new_screen(qtbot, vm)
    s.resize(1280, 640)
    s.show()
    assert not s.start_button.isEnabled()
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(vm, jobs, "a.mp4")
    finish_import(vm, jobs)
    assert s.start_button.isEnabled()
    assert "свободно 500,0 ГБ" in s.disk_text() and "нужно ~" in s.disk_text()
    assert s.video_zone.summary_text().startswith("1 файл")


def test_screen_disk_banner_text_and_actions(qtbot, vm, jobs, tmp_path, monkeypatch):
    s = new_screen(qtbot, vm)
    s.resize(1280, 640)
    s.show()
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(vm, jobs, "a.mp4")
    finish_import(vm, jobs)
    monkeypatch.setattr(vm_mod, "free_bytes", lambda p: 1 * GIB)
    vm.recheck()
    assert s.error_banner.isVisible()
    assert "не хватает места — нужно ещё" in s.error_banner._title.text()
    assert [b.text() for b in s.error_banner.action_buttons()] == [
        "Выбрать папку",
        "Проверить снова",
    ]
    assert not s.start_button.isEnabled()


def test_screen_corrupt_banner_actions(qtbot, vm, jobs, tmp_path, monkeypatch):
    s = new_screen(qtbot, vm)
    s.resize(1280, 640)
    s.show()
    vm.add_files(
        MediaKind.VIDEO, [make_file(tmp_path, "IMG_0419.MP4"), make_file(tmp_path, "ok.mp4")]
    )
    media_done(vm, jobs, "IMG_0419.MP4", MediaStatus.CORRUPT)
    media_done(vm, jobs, "ok.mp4")
    finish_import(vm, jobs)
    banner = s.error_banner
    assert banner.isVisible()
    assert banner._title.text() == (
        "Файл IMG_0419.MP4 не читается — возможно, он повреждён или скопирован не до конца."
    )
    assert banner._body.text() == "Остальные файлы проанализируем без него."
    assert [b.text() for b in banner.action_buttons()] == ["Продолжить без него", "Заменить файл"]
    replacement = make_file(tmp_path, "new.mp4")
    monkeypatch.setattr(
        screen_mod.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(replacement), "")),
    )
    banner.action_buttons()[1].click()
    assert "new.mp4" in [m.path.name for m in vm._store.list_media()]
    assert "IMG_0419.MP4" not in [m.path.name for m in vm._store.list_media()]


def test_screen_continue_without_hides_banner(qtbot, vm, jobs, tmp_path):
    s = new_screen(qtbot, vm)
    s.resize(1280, 640)
    s.show()
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "bad.mp4"), make_file(tmp_path, "ok.mp4")])
    media_done(vm, jobs, "bad.mp4", MediaStatus.CORRUPT)
    media_done(vm, jobs, "ok.mp4")
    finish_import(vm, jobs)
    s.error_banner.action_buttons()[0].click()
    assert not s.error_banner.isVisible()


def test_screen_4k_banner_text(qtbot, vm, jobs, tmp_path):
    s = new_screen(qtbot, vm)
    s.resize(1280, 640)
    s.show()
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    media_done(
        vm,
        jobs,
        "a.mp4",
        width=3840,
        height=2160,
        codec="hevc",
        hdr=True,
        duration_us=3600 * 1_000_000,
    )
    assert s.info_banner.isVisible()
    assert s.info_banner._title.text() == (
        "Видео 4K анализируется долго — около 1 ч 26 мин на этом компьютере. "
        "Можно уйти: мы пришлём уведомление."
    )


def test_screen_removable_hint(qtbot, vm, jobs, tmp_path, monkeypatch):
    s = new_screen(qtbot, vm)
    s.resize(1280, 640)
    s.show()
    assert not s.removable_label.isVisible()
    monkeypatch.setattr(vm_mod, "is_removable", lambda p: True)
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, "a.mp4")])
    assert s.copy_check.isChecked()
    assert s.removable_label.isVisible()
    assert s.removable_label.text() == "Файлы на флешке — лучше скопировать"


def test_screen_widgets_drive_the_vm(qtbot, vm):
    s = new_screen(qtbot, vm)
    s.name_edit.setText("Той Айбек")
    assert vm.name == "Той Айбек"
    s.chips[ToiKind.JUBILEE].click()
    assert vm.kind is ToiKind.JUBILEE
    s.copy_check.setChecked(True)
    assert vm.copy is True
    assert s.name_edit.placeholderText() == "Свадьба Айбек и Нурайым"
    assert [c.text() for c in s.chips.values()] == [
        "Свадьба", "Той", "Юбилей", "Беш-бармак", "Сүннөт той", "Кыз узатуу", "Другое",
    ]  # fmt: skip


def test_screen_dropped_files_go_to_vm(qtbot, vm, jobs, tmp_path):
    s = new_screen(qtbot, vm)
    s.video_zone.files_dropped.emit([make_file(tmp_path, "a.mp4")])
    s.music_zone.files_dropped.emit([make_file(tmp_path, "m.mp3")])
    kinds = {m.kind for m in vm._store.list_media()}
    assert kinds == {MediaKind.VIDEO, MediaKind.MUSIC}


def test_layout_fits_1280x720(qtbot, vm, jobs, tmp_path):
    s = new_screen(qtbot, vm)
    s.resize(1280, 648)  # 720 minus the top bar and status area
    s.show()
    vm.add_files(MediaKind.VIDEO, [make_file(tmp_path, f"IMG_{i:04d}.MP4") for i in range(3)])
    vm.add_files(MediaKind.MUSIC, [make_file(tmp_path, "song.mp3")])

    def inside(w):
        assert w.isVisible(), w
        top_left = w.mapTo(s, w.rect().topLeft())
        bottom_right = w.mapTo(s, w.rect().bottomRight())
        assert top_left.x() >= 0 and top_left.y() >= 0, w
        assert bottom_right.x() < s.width() and bottom_right.y() < s.height(), w
        hint = w.sizeHint()
        assert w.width() >= min(hint.width(), 60) and w.height() >= 1

    for w in (s.name_edit, s.date_edit, *s.chips.values(), s.video_zone, s.music_zone,
              s.copy_check, s.start_button):  # fmt: skip
        inside(w)
    assert s.start_button.height() >= 40
    for b in s.findChildren(QPushButton):
        if b.isVisible():
            assert b.width() >= b.sizeHint().width() - 2, b.text()
            assert b.height() >= 24, b.text()
    # columns are 40/60
    left = s.name_edit.mapTo(s, s.name_edit.rect().topRight()).x()
    assert left < s.width() * 0.45
    assert s.video_zone.height() > s.music_zone.height()
