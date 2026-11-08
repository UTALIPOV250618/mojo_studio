from datetime import date

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from toystudio.app.screens.analysis import AnalysisScreen
from toystudio.app.theme.apply import apply_theme
from toystudio.app.viewmodels.analysis_vm import AnalysisVM
from toystudio.core.models import (
    MediaInfo,
    MediaKind,
    MediaStatus,
    QualityInterval,
    QualityLabel,
    StageName,
    StageState,
    ToiKind,
)
from toystudio.jobs.launch import stop_file_for
from toystudio.jobs.protocol import Event, Progress
from toystudio.storage.app_store import AppStore
from toystudio.storage.project_store import ProjectStore


class FakeJobs(QObject):
    progress = Signal(str, object)
    event = Signal(str, object)
    finished = Signal(str)
    failed = Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.started: list[tuple[str, list[str]]] = []
        self.cancelled: list[str] = []
        self.active: list[str] = []
        self.queued: list[str] = []

    def start(self, job_id, argv):
        self.started.append((job_id, argv))
        self.active.append(job_id)

    def cancel(self, job_id):
        self.cancelled.append(job_id)

    def running(self):
        return list(self.active)

    def pending(self):
        return list(self.queued)

    def is_active(self, job_id):
        return job_id in self.active or job_id in self.queued


class FakeNotifier:
    def __init__(self) -> None:
        self.shown: list[tuple[str, str]] = []
        self.minimized = 0
        self.click = None

    def notify(self, title, body, on_click):
        self.shown.append((title, body))
        self.click = on_click

    def minimize_to_tray(self, window):
        self.minimized += 1


def info(duration_s=60) -> MediaInfo:
    return MediaInfo(
        duration_us=duration_s * 1_000_000,
        width=1920,
        height=1080,
        rotation=0,
        fps=25.0,
        codec="h264",
        hdr=False,
        has_audio=True,
        has_video=True,
        bit_rate=8_000_000,
        creation_time=None,
    )


@pytest.fixture
def app_store(tmp_path):
    s = AppStore(tmp_path / "app.db")
    yield s
    s.close()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Той Студия" / "Свадьба Айбек и Нурайым"
    store = ProjectStore.create(
        root, "Свадьба Айбек и Нурайым", ToiKind.WEDDING, date(2026, 10, 12)
    )
    ids = []
    for name in ("IMG_0001.MP4", "IMG_0419.MP4"):
        mid, _ = store.add_media(tmp_path / name, MediaKind.VIDEO, 1000, False)
        store.set_media_info(mid, info())
        ids.append(mid)
    yield root, store, ids
    store.close()


@pytest.fixture
def jobs():
    return FakeJobs()


def make_vm(project, jobs, app_store, running=True, delay=10_000):
    root, _, _ = project
    app_store.register_project(root, "Свадьба", "analysis", __import__("datetime").datetime.now())
    if running:
        jobs.active.append(f"analyze:{root}")
    vm = AnalysisVM(root, jobs, app_store, stop_delay_ms=delay)
    return vm


def job_id(project):
    return f"analyze:{project[0]}"


def set_all(store, ids, stage, state, found=None):
    for mid in ids:
        store.set_stage(mid, stage, state)


def finish_stage(store, ids, stage):
    set_all(store, ids, stage, StageState.DONE)


def emit_stage(jobs, project, mid, stage, state, found=None):
    jobs.event.emit(
        job_id(project),
        Event(
            "stage",
            {"media_id": mid, "stage": stage.value, "state": state.value, "found": found},
        ),
    )


def test_stage_texts_and_later_states(qtbot, project, jobs, app_store):
    apply_theme(QApplication.instance(), "dark")
    vm = make_vm(project, jobs, app_store)
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    titles = [s.title for s in vm.stages]
    assert titles == [
        "Готовим копию для анализа",
        "Слушаем речь и ищем тосты",
        "Находим смены сцен",
        "Убираем размытые и тёмные кадры",
        "Ищем танцы, аплодисменты, музыку",
    ]
    assert vm.stages[1].state == "later"
    assert vm.stages[4].state == "later"
    assert vm.stages[0].state == "waiting"
    assert screen.stage_widgets[1].found_label.text() == "Появится позже"
    assert screen.stage_widgets[4].found_label.text() == "Появится позже"


def test_found_texts_after_stages_done(qtbot, project, jobs, app_store):
    _root, store, ids = project
    for st in (StageName.PROBE, StageName.PROXY, StageName.SCENES, StageName.QUALITY):
        finish_stage(store, ids, st)
    store.set_stage(0, StageName.SYNC, StageState.DONE)
    store.replace_scene_cuts(ids[0], [1, 2, 3], [0.5, 0.5, 0.5])
    store.replace_scene_cuts(ids[1], [4], [0.5])
    store.replace_quality(ids[1], [QualityInterval(ids[1], 0, 40_000_000, QualityLabel.DARK)])
    vm = make_vm(project, jobs, app_store)
    texts = [s.found_text for s in vm.stages]
    assert texts[0] == "Готово: 2 файла"
    assert texts[2] == "Найдено 4 смены сцен"
    assert texts[3] == "Чётко: 1 файл · темновато: 1"
    assert [s.state for s in vm.stages] == ["done", "later", "done", "done", "later"]


def test_progress_events_update_overall_and_eta(qtbot, project, jobs, app_store):
    _root, store, ids = project
    vm = make_vm(project, jobs, app_store)
    assert vm.overall_percent == 0
    assert vm.eta_text is None
    finish_stage(store, ids, StageName.PROBE)
    store.set_stage(ids[0], StageName.PROXY, StageState.RUNNING)
    emit_stage(jobs, project, ids[0], StageName.PROXY, StageState.RUNNING)
    jobs.progress.emit(job_id(project), Progress("proxy", 30, 60, str(ids[0])))
    first = vm.overall_percent
    assert first > 0
    jobs.progress.emit(job_id(project), Progress("proxy", 60, 60, str(ids[0])))
    assert vm.overall_percent > first
    assert vm.stages[0].state == "running"
    assert vm.stages[0].percent is not None
    jobs.event.emit(job_id(project), Event("eta", {"remaining_s": 23 * 60 - 10}))
    assert vm.eta_text == "ещё ~23 мин"


def test_progress_of_other_job_is_ignored(qtbot, project, jobs, app_store):
    vm = make_vm(project, jobs, app_store)
    jobs.progress.emit("analyze:other", Progress("proxy", 30, 60, "1"))
    jobs.event.emit("analyze:other", Event("eta", {"remaining_s": 100}))
    assert vm.overall_percent == 0
    assert vm.eta_text is None


def test_stop_creates_stop_file_and_shows_resume(qtbot, project, jobs, app_store):
    root, _, _ = project
    vm = make_vm(project, jobs, app_store, delay=50)
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    screen.show()
    assert screen.resume_button.isHidden()
    screen.stop_button.click()
    assert stop_file_for(root, "analyze").exists()
    # the worker did not stop by itself: cancel after the delay
    qtbot.waitUntil(lambda: jobs.cancelled == [job_id(project)], timeout=2000)
    jobs.failed.emit(job_id(project), "cancelled", "")
    assert not stop_file_for(root, "analyze").exists()
    assert not screen.resume_button.isHidden()
    assert not screen.saved_label.isHidden()
    assert screen.saved_label.text() == "Пройденные этапы сохранены"
    assert screen.stop_button.isHidden()


def test_stop_without_cancel_when_worker_ends_in_time(qtbot, project, jobs, app_store):
    vm = make_vm(project, jobs, app_store, delay=100)
    vm.stop()
    jobs.failed.emit(job_id(project), "stopped", "")
    qtbot.wait(300)
    assert jobs.cancelled == []


def test_resume_restarts_analyze(qtbot, project, jobs, app_store):
    _root, _, _ = project
    vm = make_vm(project, jobs, app_store, running=False)
    assert vm.can_resume
    vm.resume()
    assert jobs.started[-1][0] == job_id(project)
    assert "analyze" in jobs.started[-1][1]
    assert not vm.can_resume
    assert vm.running


def test_finished_screen_shows_done_and_status(qtbot, project, jobs, app_store):
    _root, store, ids = project
    vm = make_vm(project, jobs, app_store)
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    for st in (*[s for s in StageName if s not in (StageName.SYNC,)],):
        finish_stage(store, ids, st)
    store.set_stage(0, StageName.SYNC, StageState.DONE)
    finished = []
    vm.finished.connect(finished.append)
    jobs.active.clear()
    jobs.finished.emit(job_id(project))
    assert finished == [0]
    assert app_store.list_projects()[0].status == "analysis_done"
    assert not screen.done_label.isHidden()


def test_notify_checkbox_drives_vm(qtbot, project, jobs, app_store):
    vm = make_vm(project, jobs, app_store)
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    assert screen.notify_check.isChecked() and vm.notify_when_done
    screen.notify_check.setChecked(False)
    assert vm.notify_when_done is False


def test_stop_of_queued_job_cancels_it(qtbot, project, jobs, app_store):
    jobs.queued.append(job_id(project))
    vm = AnalysisVM(project[0], jobs, app_store)
    assert vm.running and vm.can_resume is False
    vm.stop()
    assert jobs.cancelled == [job_id(project)]
    assert not stop_file_for(project[0], "analyze").exists()


def test_minimize_goes_to_tray(qtbot, project, jobs, app_store):
    notifier = FakeNotifier()
    vm = make_vm(project, jobs, app_store)
    screen = AnalysisScreen(vm, notifier=notifier)
    qtbot.addWidget(screen)
    screen.minimize_button.click()
    assert notifier.minimized == 1


def test_taskbar_noop_off_windows(qtbot):
    from PySide6.QtWidgets import QWidget

    from toystudio.app.platform.taskbar import TaskbarProgress

    w = QWidget()
    qtbot.addWidget(w)
    tb = TaskbarProgress(w)
    for v in (None, 0, 37.5, 100):
        tb.set_progress(v)


def test_notifier_noop_without_tray(qtbot, monkeypatch):
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QSystemTrayIcon, QWidget

    from toystudio.app.platform.notify import Notifier

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    n = Notifier(QIcon())
    n.notify("Той Студия", "x", lambda: None)
    w = QWidget()
    qtbot.addWidget(w)
    w.show()
    n.minimize_to_tray(w)
    assert w.isMinimized()


def test_reopen_interrupted_project_shows_resume(qtbot, project, jobs, app_store):
    _root, store, ids = project
    finish_stage(store, ids, StageName.PROBE)
    store.set_stage(ids[0], StageName.PROXY, StageState.INTERRUPTED)
    vm = make_vm(project, jobs, app_store, running=False)
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    screen.show()
    assert not screen.resume_button.isHidden()
    assert screen.resume_button.text() == "Продолжить анализ"
    assert screen.stop_button.isHidden()
    assert vm.stages[0].state == "waiting"
    # opening is read-only: the interrupted row is untouched and nothing is started
    assert store.stage(ids[0], StageName.PROXY).state is StageState.INTERRUPTED
    assert jobs.started == []


def test_stale_running_row_shows_as_waiting_when_no_job(qtbot, project, jobs, app_store):
    _root, store, ids = project
    store.set_stage(ids[0], StageName.PROBE, StageState.RUNNING)
    vm = make_vm(project, jobs, app_store, running=False)
    assert vm.stages[0].state == "waiting"


def test_failed_file_listed_and_retry(qtbot, project, jobs, app_store):
    _root, store, ids = project
    for st in (StageName.PROBE, StageName.PROXY, StageName.SCENES, StageName.QUALITY):
        store.set_stage(ids[0], st, StageState.DONE)
    store.set_stage(ids[1], StageName.PROBE, StageState.DONE)
    store.set_stage(ids[1], StageName.PROXY, StageState.FAILED, error_code="ffmpeg")
    store.set_stage(0, StageName.SYNC, StageState.DONE)
    vm = make_vm(project, jobs, app_store, running=False)
    assert vm.failed_files == ["IMG_0419.MP4"]
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    screen.show()
    assert screen.failed_label.text() == "Не удалось проанализировать: IMG_0419.MP4"
    assert screen.error_banner.isHidden()
    screen.retry_button.click()
    assert jobs.started and jobs.started[-1][0] == job_id(project)


def test_every_file_failed_shows_banner(qtbot, project, jobs, app_store):
    _root, store, ids = project
    for mid in ids:
        store.set_stage(mid, StageName.PROBE, StageState.DONE)
        store.set_stage(mid, StageName.PROXY, StageState.FAILED, error_code="ffmpeg")
    store.set_stage(0, StageName.SYNC, StageState.DONE)
    vm = make_vm(project, jobs, app_store, running=False)
    assert vm.all_failed
    assert vm.stages[0].state == "done" or vm.stages[0].state == "error"
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    screen.show()
    assert not screen.error_banner.isHidden()
    assert "Ни один файл не удалось проанализировать." in screen.error_banner._title.text()
    assert vm.stages[2].state == "error"


def test_job_failure_is_reported(qtbot, project, jobs, app_store):
    vm = make_vm(project, jobs, app_store)
    seen = []
    vm.failed.connect(lambda c, d: seen.append((c, d)))
    jobs.failed.emit(job_id(project), "crashed", "boom")
    assert seen == [("crashed", "boom")]
    assert not vm.running


def test_current_language_tracks_install(qapp):
    from toystudio.app.i18n import current_language, install_language

    assert current_language() == "ru"
    assert install_language(qapp, "ky")
    assert current_language() == "ky"
    install_language(qapp, "ru")
    assert current_language() == "ru"


def test_eta_text_in_kyrgyz(qtbot, qapp, project, jobs, app_store):
    from toystudio.app.i18n import install_language

    install_language(qapp, "ky")
    vm = make_vm(project, jobs, app_store)
    jobs.event.emit(job_id(project), Event("eta", {"remaining_s": 600}))
    assert vm.eta_text == "дагы ~10 мүн"


def test_old_crash_banner_does_not_return_after_resume_and_stop(qtbot, project, jobs, app_store):
    vm = make_vm(project, jobs, app_store, delay=50)
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    screen.show()
    jobs.failed.emit(job_id(project), "crashed", "boom")
    assert not screen.error_banner.isHidden()
    screen.error_banner.action.emit("retry")  # «Повторить»
    assert vm.running
    assert screen.error_banner.isHidden()
    vm.stop()
    jobs.failed.emit(job_id(project), "stopped", "")
    assert screen.error_banner.isHidden()
    assert not screen.resume_button.isHidden()


# ---- final review fixes
def _all_done(store, mid):
    for st in (StageName.PROBE, StageName.PROXY, StageName.SCENES, StageName.QUALITY):
        store.set_stage(mid, st, StageState.DONE)
    for st in (StageName.SPEECH, StageName.MOMENTS):
        store.set_stage(mid, st, StageState.SKIPPED)


def test_missing_file_is_listed_and_blocks_done(qtbot, project, jobs, app_store):
    _root, store, ids = project
    _all_done(store, ids[0])
    store.set_stage(ids[1], StageName.PROBE, StageState.PENDING)
    store.set_media_status(ids[1], MediaStatus.MISSING)
    store.set_stage(0, StageName.SYNC, StageState.DONE)
    vm = make_vm(project, jobs, app_store, running=False)
    assert vm.missing_files == ["IMG_0419.MP4"]
    assert not vm.done
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    screen.show()
    assert screen.missing_label.isVisible()
    assert screen.missing_label.text() == (
        "Файл не найден: IMG_0419.MP4. Подключите диск с ним или замените файл."
    )
    assert screen.done_label.isHidden()
    screen.missing_retry_button.click()  # the drive is back: analyze picks the file up
    assert jobs.started and jobs.started[-1][0] == job_id(project)


def test_corrupt_only_failure_hides_retry(qtbot, project, jobs, app_store):
    _root, store, ids = project
    _all_done(store, ids[0])
    store.set_media_status(ids[1], MediaStatus.CORRUPT)
    store.set_stage(ids[1], StageName.PROBE, StageState.FAILED, error_code="corrupt")
    store.set_stage(0, StageName.SYNC, StageState.DONE)
    vm = make_vm(project, jobs, app_store, running=False)
    assert vm.failed_files == ["IMG_0419.MP4"] and not vm.failed_retryable
    screen = AnalysisScreen(vm, notifier=FakeNotifier())
    qtbot.addWidget(screen)
    screen.show()
    assert screen.retry_button.isHidden()
    assert screen.replace_hint.isVisible()
    assert screen.replace_hint.text() == "Замените файл на шаге «Файлы»."


def test_retry_shown_when_a_failure_can_be_retried(qtbot, project, jobs, app_store):
    _root, store, ids = project
    store.set_media_status(ids[0], MediaStatus.CORRUPT)
    store.set_stage(ids[0], StageName.PROBE, StageState.FAILED, error_code="corrupt")
    store.set_stage(ids[1], StageName.PROBE, StageState.DONE)
    store.set_stage(ids[1], StageName.PROXY, StageState.FAILED, error_code="ffmpeg")
    vm = make_vm(project, jobs, app_store, running=False)
    assert vm.failed_retryable


def test_failed_sync_still_finishes(qtbot, project, jobs, app_store):
    _root, store, ids = project
    for mid in ids:
        _all_done(store, mid)
    store.set_stage(0, StageName.SYNC, StageState.FAILED, error_code="error")
    vm = make_vm(project, jobs, app_store, running=False)
    assert vm.done
    assert vm.stages[2].state == "done"
    assert vm.overall_percent == 100.0


def test_notifier_null_icon_falls_back_to_app_icon(qtbot):
    from PySide6.QtGui import QIcon

    from toystudio.app.platform.notify import Notifier
    from toystudio.app.widgets.icons import app_icon

    assert not app_icon().isNull()
    assert not app_icon().pixmap(256, 256).isNull()
    n = Notifier(QIcon())
    assert not n.icon.isNull()


def test_tray_menu_open_and_quit(qtbot):
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QSystemTrayIcon, QWidget

    from toystudio.app.platform.notify import Notifier

    n = Notifier(QIcon())
    assert [a.text() for a in n.menu.actions()] == ["Открыть", "Выйти"]
    w = QWidget()
    qtbot.addWidget(w)
    n._window = w  # as after minimize_to_tray
    n.open_action.trigger()
    assert w.isVisible()
    w.hide()
    n._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert w.isVisible()
    n.quit_action.trigger()
    assert not w.isVisible()
