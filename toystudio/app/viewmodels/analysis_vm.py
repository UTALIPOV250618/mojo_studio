"""View-model of screen 3: analysis progress, stop/resume, results read from project.db."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QObject, QTimer, Signal

from toystudio.app.i18n import current_language
from toystudio.core.format import format_eta
from toystudio.core.models import (
    US_PER_S,
    MediaKind,
    MediaRow,
    MediaStatus,
    QualityLabel,
    StageName,
    StageRun,
    StageState,
)
from toystudio.engine.analysis.quality import overall_label
from toystudio.jobs.launch import stop_file_for, worker_argv
from toystudio.jobs.manager import JobManager
from toystudio.jobs.protocol import Progress
from toystudio.storage.app_store import AppStore
from toystudio.storage.project_store import ProjectStore

UiState = Literal["waiting", "running", "done", "error", "later"]
STOP_GRACE_MS = 10_000
SYNC_SHARE = 1.0  # percentage points of the overall bar reserved for camera sync
_MEASURED = (StageName.PROBE, StageName.PROXY, StageName.SCENES, StageName.QUALITY)
_FINISHED = (StageState.DONE, StageState.SKIPPED)
_SYNC_FINISHED = (*_FINISHED, StageState.FAILED)  # a failed camera sync does not block the result
_ENDED_BY_USER = ("stopped", "cancelled")

# key -> (stages of every file, includes project-level SYNC); None = arrives in phase 2b
_UI_STAGES: tuple[tuple[str, tuple[StageName, ...] | None, bool], ...] = (
    ("prepare", (StageName.PROBE, StageName.PROXY), False),
    ("speech", None, False),
    ("scenes", (StageName.SCENES,), True),
    ("quality", (StageName.QUALITY,), False),
    ("moments", None, False),
)


@dataclass(frozen=True)
class StageView:
    key: str
    title: str
    state: UiState
    found_text: str | None
    percent: float | None  # 0..1, only meaningful while running


@dataclass
class _Snapshot:
    name: str = ""
    files: list[MediaRow] = field(default_factory=list)
    missing: list[MediaRow] = field(default_factory=list)  # source gone (drive unplugged)
    runs: dict[tuple[int, StageName], StageRun] = field(default_factory=dict)
    scene_cuts: int = 0
    labels: list[QualityLabel] = field(default_factory=list)
    step_done: bool = False


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Pick the Russian noun form; Kyrgyz translations simply repeat the same text."""
    if n % 10 == 1 and n % 100 != 11:
        source = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        source = few
    else:
        source = many
    return QCoreApplication.translate("AnalysisVM", source).replace("%1", str(n))


class AnalysisVM(QObject):
    changed = Signal()
    finished = Signal(int)  # scene cuts found; phase 2b replaces it with moments
    failed = Signal(str, str)

    def __init__(
        self,
        root: Path,
        jobs: JobManager,
        app_store: AppStore,
        *,
        stop_delay_ms: int = STOP_GRACE_MS,
        assume_running: bool = False,
    ) -> None:
        super().__init__()
        self.root = root
        self._jobs = jobs
        self._app = app_store
        self.job_id = f"analyze:{root}"
        self._active = assume_running or jobs.is_active(self.job_id)  # running or queued
        self.notify_when_done = True
        self._stopping = False
        self._remaining_s: float | None = None
        self._live: dict[int, float] = {}
        self._snap = _Snapshot()
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.setInterval(stop_delay_ms)
        self._stop_timer.timeout.connect(self._cancel_if_running)
        self.stages: list[StageView] = []
        self.overall_percent = 0.0
        self.eta_text: str | None = None
        self.failed_files: list[str] = []
        self.failed_retryable = False  # False: every failure is a corrupt file to replace
        self.missing_files: list[str] = []
        self.all_failed = False
        jobs.progress.connect(self._on_progress)
        jobs.event.connect(self._on_event)
        jobs.finished.connect(self._on_finished)
        jobs.failed.connect(self._on_failed)
        self._reload()
        self._compute()

    # ---- state for the screen
    @property
    def running(self) -> bool:
        return self._active

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def done(self) -> bool:
        """Finished, and no file waits for its drive to come back."""
        return not self._active and self._snap.step_done and not self._snap.missing

    @property
    def can_resume(self) -> bool:
        return not self._active and not self._snap.step_done

    @property
    def remaining_s(self) -> float | None:
        """Worker's latest time-left estimate while running (for the project card)."""
        return self._remaining_s if self._active else None

    @property
    def project_name(self) -> str:
        return self._snap.name

    @property
    def scene_cuts(self) -> int:
        return self._snap.scene_cuts

    # ---- commands
    def stop(self) -> None:
        if not self._active or self._stopping:
            return
        if self.job_id in self._jobs.pending():  # never started: drop it from the queue
            self._jobs.cancel(self.job_id)
            return
        stop = stop_file_for(self.root, "analyze")
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.touch()
        self._stopping = True
        self._stop_timer.start()
        self.changed.emit()

    def resume(self) -> None:
        """Run `analyze` again: finished stages are skipped, failed and interrupted are redone."""
        if self._active:
            return
        stop = stop_file_for(self.root, "analyze")
        stop.unlink(missing_ok=True)
        argv = worker_argv("analyze", self.root, self.job_id, app_db=self._app.path, stop_file=stop)
        try:
            self._jobs.start(self.job_id, argv)
        except ValueError:  # the manager still holds the previous run
            return
        self._active = True
        self._stopping = False
        self._remaining_s = None
        self._live.clear()
        self._set_status("analysis")
        self._reload()
        self._compute()
        self.changed.emit()

    def retranslate(self) -> None:
        self._compute()
        self.changed.emit()

    # ---- job signals
    def _on_progress(self, job_id: str, msg: object) -> None:
        if job_id != self.job_id or not isinstance(msg, Progress):
            return
        self._active = True
        if msg.stage == StageName.PROXY.value and msg.total > 0 and msg.media_id:
            try:
                mid = int(msg.media_id)
            except ValueError:
                return
            self._live[mid] = min(max(msg.done / msg.total, 0.0), 1.0)
            self._compute()
            self.changed.emit()

    def _on_event(self, job_id: str, ev: object) -> None:
        if job_id != self.job_id:
            return
        kind = getattr(ev, "kind", "")
        payload = getattr(ev, "payload", {})
        self._active = True
        if kind == "eta":
            self._remaining_s = float(payload.get("remaining_s", 0.0))
        elif kind == "stage":
            if str(payload.get("stage")) == StageName.PROXY.value:
                self._live.pop(int(payload.get("media_id", 0)), None)
            self._reload()
        else:
            return
        self._compute()
        self.changed.emit()

    def _on_finished(self, job_id: str) -> None:
        if job_id != self.job_id:
            return
        self._ended()
        self.finished.emit(self._snap.scene_cuts)

    def _on_failed(self, job_id: str, code: str, detail: str) -> None:
        if job_id != self.job_id:
            return
        self._ended()
        if code not in _ENDED_BY_USER:
            self.failed.emit(code, detail)

    def _ended(self) -> None:
        self._stop_timer.stop()
        stop_file_for(self.root, "analyze").unlink(missing_ok=True)
        self._active = False
        self._stopping = False
        self._remaining_s = None
        self._live.clear()
        self._reload()
        # a missing file keeps the card on "analysis" until its drive is back
        self._set_status("analysis_done" if self.done else "analysis")
        self._compute()
        self.changed.emit()

    def _cancel_if_running(self) -> None:
        if self._active:
            self._jobs.cancel(self.job_id)

    def _set_status(self, status: str) -> None:
        try:
            self._app.set_status(self.root, status, datetime.now())
        except sqlite3.Error:
            pass  # the list status is cosmetic; project.db stays the source of truth

    # ---- reading project.db (read-only: never touches RUNNING rows, see ruling R11/R17)
    def _reload(self) -> None:
        try:
            store = ProjectStore.open_readonly(self.root)
        except (OSError, sqlite3.Error):
            return
        try:
            snap = _Snapshot(name=store.name)
            snap.runs = {(r.media_id, r.stage): r for r in store.stages()}
            snap.files = [
                m
                for m in store.list_media(MediaKind.VIDEO)
                if m.status is MediaStatus.OK
                or (
                    m.status is MediaStatus.CORRUPT
                    and (r := snap.runs.get((m.id, StageName.PROBE))) is not None
                    and r.state is StageState.FAILED
                )
            ]
            snap.missing = [
                m for m in store.list_media(MediaKind.VIDEO) if m.status is MediaStatus.MISSING
            ]
            snap.scene_cuts = store.scene_cut_count()
            for m in snap.files:
                run = snap.runs.get((m.id, StageName.QUALITY))
                if run is not None and run.state in _FINISHED and m.info is not None:
                    snap.labels.append(
                        overall_label(store.quality_intervals(m.id), m.info.duration_us)
                    )
            snap.step_done = store.step() == "analysis_done"
            self._snap = snap
        except sqlite3.Error:
            return
        finally:
            store.close()

    # ---- derived state
    def _run_state(self, mid: int, stage: StageName) -> StageState:
        run = self._snap.runs.get((mid, stage))
        if run is None:
            return StageState.PENDING
        if run.state is StageState.RUNNING and not self._active:
            return StageState.INTERRUPTED  # stale row of a worker that is gone
        return run.state

    def _file_failed(self, mid: int) -> bool:
        return any(
            self._run_state(mid, s) is StageState.FAILED for s in (*_MEASURED, StageName.SYNC)
        )

    def _file_state(self, mid: int, names: tuple[StageName, ...]) -> str:
        states = [self._run_state(mid, n) for n in names]
        if StageState.FAILED in states:
            return "failed"
        if StageState.RUNNING in states:
            return "running"
        if all(s in _FINISHED for s in states):
            return "done"
        return "failed" if self._file_failed(mid) else "pending"

    def _sync_state(self) -> StageState:
        return self._run_state(0, StageName.SYNC)

    def _ui_state(self, names: tuple[StageName, ...], with_sync: bool) -> UiState:
        files = self._snap.files
        if not files:
            return "waiting"
        states = [self._file_state(m.id, names) for m in files]
        if all(s == "failed" for s in states):
            return "error"
        sync = self._sync_state() if with_sync else StageState.DONE
        if "running" in states or sync is StageState.RUNNING:
            return "running"
        if all(s in ("done", "failed") for s in states) and sync in _SYNC_FINISHED:
            return "done"
        return "waiting"

    def _weight(self, m: MediaRow) -> float:
        return m.info.duration_us / US_PER_S if m.info else 0.0

    def _weights(self) -> dict[int, float]:
        w = {m.id: self._weight(m) for m in self._snap.files}
        return w if sum(w.values()) > 0 else dict.fromkeys(w, 1.0)

    def _unit(self, mid: int, stage: StageName) -> float:
        """Share (0..1) of one file's stage that is finished."""
        state = self._run_state(mid, stage)
        if state in _FINISHED or self._file_failed(mid):
            return 1.0
        if stage is StageName.PROXY and state is StageState.RUNNING:
            return self._live.get(mid, 0.0)
        return 0.0

    def _fraction(self, names: tuple[StageName, ...]) -> float:
        weights = self._weights()
        total = sum(weights.values()) * len(names)
        if total <= 0:
            return 0.0
        done = sum(weights[mid] * self._unit(mid, st) for mid in weights for st in names)
        return done / total

    def _compute(self) -> None:
        snap = self._snap
        measured = self._fraction(_MEASURED)
        sync_done = self._sync_state() in _SYNC_FINISHED
        if snap.files:
            self.overall_percent = round(
                (100.0 - SYNC_SHARE) * measured + (SYNC_SHARE if sync_done else 0.0), 2
            )
        else:
            self.overall_percent = 0.0
        self.eta_text = (
            format_eta(self._remaining_s, current_language())
            if self._active and self._remaining_s is not None
            else None
        )
        failed = [m for m in snap.files if self._file_failed(m.id)]
        self.failed_files = [m.path.name for m in failed]
        # a corrupt file fails again on every rerun: only replacing it on screen 2 helps
        self.failed_retryable = any(m.status is not MediaStatus.CORRUPT for m in failed)
        self.missing_files = [m.path.name for m in snap.missing]
        self.all_failed = bool(snap.files) and len(self.failed_files) == len(snap.files)
        views: list[StageView] = []
        for key, names, with_sync in _UI_STAGES:
            title = self._title(key)
            if names is None:
                views.append(StageView(key, title, "later", None, None))
                continue
            state = self._ui_state(names, with_sync)
            found = self._found(key) if state == "done" else None
            percent = self._fraction(names) if state == "running" else None
            views.append(StageView(key, title, state, found, percent))
        self.stages = views

    def _title(self, key: str) -> str:
        return QCoreApplication.translate("AnalysisVM", _TITLES[key])

    def _found(self, key: str) -> str | None:
        snap = self._snap
        if key == "prepare":
            n = sum(
                1
                for m in snap.files
                if self._file_state(m.id, (StageName.PROBE, StageName.PROXY)) == "done"
            )
            return _plural(
                n,
                QT_TRANSLATE_NOOP("AnalysisVM", "Готово: %1 файл"),
                QT_TRANSLATE_NOOP("AnalysisVM", "Готово: %1 файла"),
                QT_TRANSLATE_NOOP("AnalysisVM", "Готово: %1 файлов"),
            )
        if key == "scenes":
            return _plural(
                snap.scene_cuts,
                QT_TRANSLATE_NOOP("AnalysisVM", "Найдено %1 смена сцен"),
                QT_TRANSLATE_NOOP("AnalysisVM", "Найдено %1 смены сцен"),
                QT_TRANSLATE_NOOP("AnalysisVM", "Найдено %1 смен сцен"),
            )
        if key == "quality":
            return self._quality_text()
        return None

    def _quality_text(self) -> str:
        labels = self._snap.labels
        clear = sum(1 for lbl in labels if lbl is QualityLabel.CLEAR)
        parts = [
            _plural(
                clear,
                QT_TRANSLATE_NOOP("AnalysisVM", "Чётко: %1 файл"),
                QT_TRANSLATE_NOOP("AnalysisVM", "Чётко: %1 файла"),
                QT_TRANSLATE_NOOP("AnalysisVM", "Чётко: %1 файлов"),
            ),
        ]
        for label, text in _BAD_LABELS:
            n = sum(1 for lbl in labels if lbl is label)
            if n:
                parts.append(f"{QCoreApplication.translate('AnalysisVM', text)}: {n}")
        return " · ".join(parts)


_TITLES = {
    "prepare": QT_TRANSLATE_NOOP("AnalysisVM", "Готовим копию для анализа"),
    "speech": QT_TRANSLATE_NOOP("AnalysisVM", "Слушаем речь и ищем тосты"),
    "scenes": QT_TRANSLATE_NOOP("AnalysisVM", "Находим смены сцен"),
    "quality": QT_TRANSLATE_NOOP("AnalysisVM", "Убираем размытые и тёмные кадры"),
    "moments": QT_TRANSLATE_NOOP("AnalysisVM", "Ищем танцы, аплодисменты, музыку"),
}
_BAD_LABELS = (
    (QualityLabel.DARK, QT_TRANSLATE_NOOP("AnalysisVM", "темновато")),
    (QualityLabel.SHAKY, QT_TRANSLATE_NOOP("AnalysisVM", "трясётся")),
    (QualityLabel.BLURRY, QT_TRANSLATE_NOOP("AnalysisVM", "размыто")),
)
