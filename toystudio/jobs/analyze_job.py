"""Worker job `analyze`: per-file stages with checkpoints in project.db, then camera sync."""

from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from toystudio.core.eta import DEFAULT_X_RT, profile_key, project_remaining_s
from toystudio.core.models import (
    STAGE_ORDER,
    US_PER_S,
    MediaInfo,
    MediaKind,
    MediaRow,
    MediaStatus,
    StageName,
    StageState,
)
from toystudio.engine.analysis import single_pass
from toystudio.engine.analysis.quality import bad_intervals
from toystudio.engine.analysis.scenes import detect_cuts
from toystudio.engine.analysis.single_pass import FfmpegFailed, Outputs, Stopped, outputs_for
from toystudio.engine.analysis.sync import sync_project
from toystudio.engine.media.probe import check_integrity, probe_media
from toystudio.engine.media.thumbs import card_thumbnail
from toystudio.jobs.protocol import Event, Progress
from toystudio.storage.app_store import AppStore
from toystudio.storage.project_store import ProjectStore

if TYPE_CHECKING:
    from toystudio.jobs.worker_main import JobContext

STAGE_VERSION: dict[StageName, int] = {s: 1 for s in (*STAGE_ORDER, StageName.SYNC)}
NOT_IMPLEMENTED = (StageName.SPEECH, StageName.MOMENTS)  # phase 2b
SPEED_STAGE = "proxy"
SPEED_ALPHA = 0.2
ETA_EVERY_S = 2.0
PROJECT_MEDIA_ID = 0  # project-level stages (SYNC) are stored with media_id 0


class _SkipFile(Exception):
    """This file cannot go further (missing, corrupt, ffmpeg failed); go to the next one."""


@dataclass
class _Run:
    ctx: JobContext
    store: ProjectStore
    app: AppStore | None
    speeds: dict[str, float]
    proxy_left_s: dict[int, tuple[str, float]] = field(default_factory=dict)
    last_eta: float = float("-inf")

    @property
    def cache(self) -> Path:
        return self.store.root / "cache"

    # --- events ---

    def stage_event(
        self, media_id: int, stage: StageName, state: StageState, found: int | None = None
    ) -> None:
        payload = {"media_id": media_id, "stage": stage.value, "state": state.value}
        self.ctx.report(Event("stage", {**payload, "found": found}))

    def finish(self, media_id: int, stage: StageName, state: StageState, *,
               found: int | None = None, error_code: str | None = None) -> None:  # fmt: skip
        self.store.set_stage(
            media_id,
            stage,
            state,
            progress=1.0 if state is StageState.DONE else 0.0,
            version=STAGE_VERSION[stage],
            error_code=error_code,
        )
        if state is StageState.DONE and stage is not StageName.PROXY:
            self.ctx.report(Progress(stage.value, 1, 1, str(media_id)))
        self.stage_event(media_id, stage, state, found)

    def eta(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_eta < ETA_EVERY_S:
            return
        self.last_eta = now
        left = project_remaining_s(list(self.proxy_left_s.values()), self.speeds)
        self.ctx.report(Event("eta", {"remaining_s": float(left)}))

    # --- speeds ---

    def record_speed(self, info: MediaInfo, media_s: float, wall_s: float) -> None:
        if wall_s <= 0 or media_s <= 0:
            return
        key = profile_key(info)
        measured = media_s / wall_s
        old = self.app.speed(SPEED_STAGE, key) if self.app is not None else None
        value = measured if old is None else (1 - SPEED_ALPHA) * old + SPEED_ALPHA * measured
        if self.app is not None:
            self.app.set_speed(SPEED_STAGE, key, value)
        self.speeds[key] = value

    # --- stages ---

    def is_current(self, media_id: int, stage: StageName) -> bool:
        st = self.store.stage(media_id, stage)
        return (
            st is not None
            and st.state in (StageState.DONE, StageState.SKIPPED)
            and st.version == STAGE_VERSION[stage]
        )

    def media(self, media_id: int) -> MediaRow:
        return next(m for m in self.store.list_media() if m.id == media_id)

    def analyze_file(self, media_id: int) -> None:
        for stage in STAGE_ORDER:
            if self.is_current(media_id, stage):
                continue
            row = self.media(media_id)
            if not row.path.is_file():
                self.store.set_media_status(media_id, MediaStatus.MISSING)
                self.ctx.report(Event("media", {"media_id": media_id, "status": "missing"}))
                self.proxy_left_s.pop(media_id, None)
                raise _SkipFile()
            try:
                if self.ctx.should_stop():
                    raise Stopped()
                self.store.set_stage(media_id, stage, StageState.RUNNING)
                self.stage_event(media_id, stage, StageState.RUNNING)
                self.run_stage(row, stage)
            except Stopped:
                self.store.set_stage(media_id, stage, StageState.INTERRUPTED)
                self.stage_event(media_id, stage, StageState.INTERRUPTED)
                raise
            except _SkipFile:
                raise
            except Exception:  # noqa: BLE001 - one bad file must not stop the others
                traceback.print_exc(file=sys.stderr)
                self.proxy_left_s.pop(media_id, None)
                self.finish(media_id, stage, StageState.FAILED, error_code="error")
                raise _SkipFile() from None

    def run_stage(self, row: MediaRow, stage: StageName) -> None:
        out = outputs_for(self.cache, row.id)
        if stage is StageName.PROBE:
            self.probe(row)
        elif stage is StageName.PROXY:
            self.proxy(row, out)
        elif stage in NOT_IMPLEMENTED:
            self.finish(row.id, stage, StageState.SKIPPED)
        elif stage is StageName.SCENES:
            cuts = detect_cuts(_metrics(out), fps=single_pass.METRIC_FPS)
            self.store.replace_scene_cuts(row.id, [c for c, _ in cuts], [s for _, s in cuts])
            self.finish(row.id, stage, StageState.DONE, found=len(cuts))
        elif stage is StageName.QUALITY:
            intervals = bad_intervals(row.id, _metrics(out), fps=single_pass.METRIC_FPS)
            self.store.replace_quality(row.id, intervals)
            self.finish(row.id, stage, StageState.DONE, found=len(intervals))
        else:  # pragma: no cover - STAGE_ORDER is closed
            raise ValueError(f"unexpected per-file stage {stage}")

    def probe(self, row: MediaRow) -> None:
        info = row.info
        if info is None:
            try:
                info = probe_media(row.path) if check_integrity(row.path) else None
            except ValueError:
                info = None
            if info is not None:
                self.store.set_media_info(row.id, info)
        if info is None or not info.has_video:
            self.store.set_media_status(row.id, MediaStatus.CORRUPT)
            self.finish(row.id, StageName.PROBE, StageState.FAILED, error_code="corrupt")
            self.ctx.report(Event("media", {"media_id": row.id, "status": "corrupt"}))
            self.proxy_left_s.pop(row.id, None)
            raise _SkipFile()
        self.proxy_left_s[row.id] = (profile_key(info), info.duration_us / US_PER_S)
        self.finish(row.id, StageName.PROBE, StageState.DONE)

    def proxy(self, row: MediaRow, out: Outputs) -> None:
        info = row.info
        assert info is not None  # PROBE is done before PROXY
        if not (out.proxy.is_file() and out.frames.is_file()):  # proxy = completion marker
            total_s = info.duration_us / US_PER_S
            key = profile_key(info)

            def on_progress(done_s: float) -> None:
                self.ctx.report(Progress(StageName.PROXY.value, done_s, total_s, str(row.id)))
                self.proxy_left_s[row.id] = (key, max(total_s - done_s, 0.0))
                self.eta()

            started = time.monotonic()
            try:
                single_pass.run(
                    row.path, info, out, on_progress=on_progress, should_stop=self.ctx.should_stop
                )
            except FfmpegFailed:
                self.proxy_left_s.pop(row.id, None)
                self.finish(row.id, StageName.PROXY, StageState.FAILED, error_code="ffmpeg")
                raise _SkipFile() from None
            self.record_speed(info, total_s, time.monotonic() - started)
        self.proxy_left_s.pop(row.id, None)
        self.finish(row.id, StageName.PROXY, StageState.DONE)
        self.eta(force=True)
        self.card(out.proxy)

    def card(self, proxy: Path) -> None:
        card = self.cache / "thumbs" / "card.jpg"
        if card.exists():
            return
        try:
            card_thumbnail(proxy, card)
        except (RuntimeError, ValueError, OSError):
            pass  # a missing card picture must not fail the analysis

    def sync(self, video_ids: list[int]) -> None:
        stage = StageName.SYNC
        if self.ctx.should_stop():
            self.store.set_stage(PROJECT_MEDIA_ID, stage, StageState.INTERRUPTED)
            raise Stopped()
        self.store.set_stage(PROJECT_MEDIA_ID, stage, StageState.RUNNING)
        self.stage_event(PROJECT_MEDIA_ID, stage, StageState.RUNNING)
        envelopes: dict[int, np.ndarray | None] = {}
        for mid in video_ids:
            env = outputs_for(self.cache, mid).envelope
            envelopes[mid] = np.load(env) if env.is_file() else None
        for r in sync_project(envelopes):
            self.store.set_sync(r.media_id, r.offset_us, r.confidence)
        self.finish(PROJECT_MEDIA_ID, stage, StageState.DONE)


def _metrics(out: Outputs) -> dict[str, np.ndarray]:
    with np.load(out.frames) as z:
        return {k: z[k] for k in z.files}


def _speeds(app: AppStore | None) -> dict[str, float]:
    speeds = dict(DEFAULT_X_RT)
    if app is not None:
        for key in speeds:
            stored = app.speed(SPEED_STAGE, key)
            if stored is not None and stored > 0:
                speeds[key] = stored
    return speeds


def _ok_videos(store: ProjectStore) -> list[MediaRow]:
    return [m for m in store.list_media(MediaKind.VIDEO) if m.status is MediaStatus.OK]


def run(ctx: JobContext) -> None:
    store = ProjectStore.open(Path(ctx.project))
    app = AppStore(ctx.app_db) if ctx.app_db is not None else None
    try:
        r = _Run(ctx, store, app, _speeds(app))
        videos = _ok_videos(store)
        for m in videos:  # pending rows up front, so the GUI shows the whole plan
            for stage in STAGE_ORDER:
                if store.stage(m.id, stage) is None:
                    store.set_stage(m.id, stage, StageState.PENDING, version=STAGE_VERSION[stage])
            if m.info is not None and not r.is_current(m.id, StageName.PROXY):
                r.proxy_left_s[m.id] = (profile_key(m.info), m.info.duration_us / US_PER_S)
        if store.stage(PROJECT_MEDIA_ID, StageName.SYNC) is None:
            store.set_stage(PROJECT_MEDIA_ID, StageName.SYNC, StageState.PENDING)
        r.eta(force=True)
        for m in videos:
            try:
                r.analyze_file(m.id)
            except _SkipFile:
                continue
        r.sync([m.id for m in _ok_videos(store)])
    finally:
        if app is not None:
            app.close()
        store.close()
