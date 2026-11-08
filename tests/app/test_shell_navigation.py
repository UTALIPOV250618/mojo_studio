"""Shell wiring of screens 1-3, the stepper, app paths and closing while a worker runs."""

from datetime import date, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox

from toystudio.app.main import app_paths
from toystudio.app.screens.analysis import AnalysisScreen
from toystudio.app.screens.new_project import NewProjectScreen
from toystudio.app.shell import Shell
from toystudio.app.viewmodels import new_project_vm as vm_mod
from toystudio.core.models import MediaKind, StageName, StageState, ToiKind
from toystudio.jobs.launch import stop_file_for
from toystudio.jobs.protocol import Event
from toystudio.storage.project_store import ProjectStore

GIB = 1024**3


class FakeJobs(QObject):
    """JobManager stand-in: a job "ends" as soon as its stop file exists (unless stubborn)."""

    progress = Signal(str, object)
    event = Signal(str, object)
    finished = Signal(str)
    failed = Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.active: list[str] = []
        self.started: list[str] = []
        self.shutdowns = 0
        self.stubborn = False

    def start(self, job_id, argv):
        if job_id in self.active:
            raise ValueError(job_id)
        self.started.append(job_id)
        self.active.append(job_id)

    def cancel(self, job_id):
        self.active = [j for j in self.active if j != job_id]

    def _stopped(self, job_id: str) -> bool:
        kind, _, root = job_id.partition(":")
        return not self.stubborn and stop_file_for(Path(root), kind).exists()

    def running(self):
        self.active = [j for j in self.active if not self._stopped(j)]
        return list(self.active)

    def shutdown(self):
        self.shutdowns += 1
        self.active.clear()


class FakeNotifier:
    def __init__(self) -> None:
        self.minimized = 0

    def notify(self, title, body, on_click):
        pass

    def minimize_to_tray(self, window):
        self.minimized += 1


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr(vm_mod, "free_bytes", lambda p: 500 * GIB)
    monkeypatch.setattr(vm_mod, "is_removable", lambda p: False)


@pytest.fixture
def jobs():
    return FakeJobs()


@pytest.fixture
def shell(make_shell, jobs):
    return make_shell(jobs=jobs, notifier=FakeNotifier())


def _project(w, name: str, *, video: bool) -> Path:
    root = (w.projects_dir / name).resolve()
    store = ProjectStore.create(root, name, ToiKind.TOI, date(2026, 5, 1))
    if video:
        src = root.parent.parent / "src" / f"{name}.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"x" * 100)
        mid, _ = store.add_media(src, MediaKind.VIDEO, 100, False)
        store.set_stage(mid, StageName.PROBE, StageState.RUNNING)
    store.close()
    w.app_store.register_project(root, name, "analysis" if video else "files", datetime.now())
    w.projects_vm.reload()
    return root


def _stage_state(root: Path) -> StageState:
    ro = ProjectStore.open_readonly(root)
    try:
        return ro.stages()[0].state
    finally:
        ro.close()


# ---- app paths
def test_app_paths_windows():
    env = {"USERPROFILE": "/u/aibek", "APPDATA": "/u/aibek/AppData/Roaming"}
    projects, db = app_paths(env, "win32", Path("/ignored"))
    assert projects == Path("/u/aibek/Videos/Той Студия")
    assert db == Path("/u/aibek/AppData/Roaming/ToyStudio/app.db")


def test_app_paths_mac_and_linux():
    assert app_paths({}, "darwin", Path("/h")) == (
        Path("/h/Movies/Той Студия"),
        Path("/h/.toystudio/app.db"),
    )
    assert app_paths({}, "linux", Path("/h")) == (
        Path("/h/Videos/Той Студия"),
        Path("/h/.toystudio/app.db"),
    )


def test_app_paths_override_puts_both_under_home(tmp_path):
    projects, db = app_paths({"TOYSTUDIO_HOME": str(tmp_path)}, "win32", Path("/h"))
    assert projects.parent == tmp_path and db.parent == tmp_path


# ---- navigation
def test_starts_on_projects_without_stepper(shell):
    assert shell.current_screen() is shell.projects_screen
    assert all(b.isHidden() for b in shell.top_bar._steps)
    assert shell.top_bar._projects_btn.isHidden()


def test_new_project_and_back(shell):
    qtbot_click(shell.projects_screen.new_button)
    assert isinstance(shell.current_screen(), NewProjectScreen)
    assert not shell.top_bar._steps[0].isHidden()
    assert shell.top_bar.step_enabled() == [True, False, False, False, False]
    assert shell.top_bar.current_step() == 0
    shell.top_bar.click_projects()
    assert shell.current_screen() is shell.projects_screen
    assert shell.new_project_screen is None


def qtbot_click(button):
    button.click()


def test_dropped_files_create_project_and_start_import(shell, jobs, tmp_path):
    clip = tmp_path / "в папке" / "клип 1.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"x" * 10)
    shell.projects_vm.request_new([clip])
    screen = shell.new_project_screen
    assert screen is not None and shell.current_screen() is screen
    root = screen.vm.root
    assert root is not None and root.is_relative_to(shell.projects_dir)
    assert jobs.started == [f"import:{root}"]
    assert shell.is_busy(root)


def test_files_card_while_import_runs_reads_only(shell, jobs):
    root = _project(shell, "Той", video=True)
    jobs.active.append(f"import:{root}")
    shell._open_project(root, "files")
    screen = shell.new_project_screen
    assert screen is not None and shell.current_screen() is screen
    assert screen.vm._store is None
    assert screen.name_edit.text() == "Той"
    assert len(screen.video_zone.row_status_texts()) == 1
    assert _stage_state(root) is StageState.RUNNING  # open() would flip it to interrupted
    jobs.active.clear()
    jobs.finished.emit(f"import:{root}")
    assert screen.vm._store is not None


def test_files_card_without_job_opens_store(shell):
    root = _project(shell, "Пусто", video=False)
    shell.projects_vm.open(root)
    screen = shell.new_project_screen
    assert screen is not None and screen.vm._store is not None
    assert screen.vm.root == root


def test_attached_vm_queues_files_until_worker_ends(shell, jobs, tmp_path):
    root = _project(shell, "Той", video=True)
    jobs.active.append(f"import:{root}")
    shell._open_project(root, "files")
    vm = shell.new_project_screen.vm
    extra = tmp_path / "extra.mp4"
    extra.write_bytes(b"y" * 5)
    vm.add_files(MediaKind.VIDEO, [extra])
    assert len(vm.rows(MediaKind.VIDEO)) == 1
    jobs.active.clear()
    jobs.finished.emit(f"import:{root}")
    assert len(vm.rows(MediaKind.VIDEO)) == 2


def test_analysis_card_opens_screen_3(shell):
    root = _project(shell, "Той", video=True)
    shell.projects_vm.open(root)
    assert isinstance(shell.current_screen(), AnalysisScreen)
    assert shell.analysis_screen.vm.root == root
    assert shell.top_bar.current_step() == 1
    assert shell.top_bar.step_enabled() == [True, True, False, False, False]
    assert shell.analysis_screen.resume_button.isVisibleTo(shell)


def test_analysis_started_switches_and_progress_reaches_card(shell, jobs):
    root = _project(shell, "Той", video=True)
    jobs.active.append(f"analyze:{root}")
    shell.show_new_project(root=root)
    shell.new_project_screen.vm.analysis_started.emit(root)
    screen = shell.analysis_screen
    assert screen is not None and shell.current_screen() is screen
    assert shell.new_project_screen is None
    # files cannot be edited while this project is analysed
    assert shell.top_bar.step_enabled() == [False, True, False, False, False]
    shell.top_bar._steps[0].click()
    assert shell.current_screen() is screen and shell.top_bar.current_step() == 1
    jobs.event.emit(f"analyze:{root}", Event("eta", {"remaining_s": 30.0}))
    item = shell.projects_vm.item(root)
    assert item is not None and item.eta_s == 30.0 and item.percent is not None


def test_step_back_to_files_after_analysis_stopped(shell):
    root = _project(shell, "Той", video=True)
    shell.show_analysis(root)
    shell.top_bar._steps[0].click()
    assert isinstance(shell.current_screen(), NewProjectScreen)
    assert shell.top_bar.step_enabled()[:2] == [True, True]
    shell.top_bar._steps[1].click()
    assert isinstance(shell.current_screen(), AnalysisScreen)


# ---- closing
def test_close_without_jobs_just_closes(shell, jobs):
    shell._ask_close = lambda: pytest.fail("must not ask")
    shell.show()
    assert shell.close()
    assert jobs.shutdowns == 1


def test_close_during_analysis_minimize(shell, jobs):
    root = _project(shell, "Той", video=True)
    jobs.active.append(f"analyze:{root}")
    shell.show()
    shell._ask_close = lambda: "minimize"
    assert not shell.close()
    assert shell.notifier.minimized == 1
    assert jobs.shutdowns == 0 and jobs.running() == [f"analyze:{root}"]


def test_close_dialog_dismissed_keeps_window(shell, jobs):
    root = _project(shell, "Той", video=True)
    jobs.active.append(f"analyze:{root}")
    shell.show()
    shell._ask_close = lambda: None
    assert not shell.close()
    assert shell.notifier.minimized == 0 and jobs.shutdowns == 0


def test_close_during_analysis_stop_writes_stop_file_and_waits(shell, jobs):
    root = _project(shell, "Той", video=True)
    jobs.active.append(f"analyze:{root}")
    shell.show()
    shell._ask_close = lambda: "stop"
    assert shell.close()
    assert stop_file_for(root, "analyze").exists()
    assert jobs.shutdowns == 1


def test_stop_jobs_gives_up_after_the_wait(shell, jobs):
    root = _project(shell, "Той", video=True)
    jobs.active.append(f"analyze:{root}")
    jobs.stubborn = True
    shell.stop_jobs(wait_s=0.1)
    assert jobs.shutdowns == 1 and jobs.active == []


@pytest.mark.parametrize(("index", "expected"), [(0, "minimize"), (1, "stop")])
def test_close_question_texts_and_buttons(shell, monkeypatch, index, expected):
    seen = {}

    def fake_exec(box):
        seen["text"] = box.text()
        buttons = sorted(box.buttons(), key=lambda b: b.text() != "Свернуть")
        seen["labels"] = [b.text() for b in buttons]
        buttons[index].click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert Shell._ask_close(shell) == expected
    assert seen["text"] == (
        "Анализ ещё идёт. Свернуть в трей или остановить? Пройденные этапы сохранятся."
    )
    assert seen["labels"] == ["Свернуть", "Остановить и закрыть"]


def test_offscreen_smoke_show_and_close(make_shell, qtbot, qapp):
    w = make_shell()
    w.show()
    qapp.processEvents()
    w.set_language("ky")
    qapp.processEvents()
    assert w.page_title() == "Долбоорлор"
    assert w.close()


def test_mouse_click_on_card_opens_project(shell, qtbot):
    root = _project(shell, "Той", video=True)
    shell.show()
    card = shell.projects_screen.card_at(0)
    qtbot.waitUntil(lambda: card.height() > 0)
    qtbot.mouseClick(card, Qt.MouseButton.LeftButton)
    assert isinstance(shell.current_screen(), AnalysisScreen)
    assert shell.analysis_screen.vm.root == root
