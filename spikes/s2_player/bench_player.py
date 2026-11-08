"""S2: unattended benchmark of libmpv as the draft-editor player.

    python -m spikes.s2_player.bench_player --mode render|wid [--segments 200] [--seed 2]
        [--work DIR] [--out NAME] [--seeks 100] [--steps 50] [--rebuilds 20]
        [--play-s SECONDS] [--play-after-s 60] [--no-music] [--fonts-dir DIR]
        [--hwdec no|auto-safe] [--block] [--only STEP,...] [--skip-sync]

Opens a real window (render: QOpenGLWidget + render API; wid: native child window),
drives mpv from a worker thread and exits. Nothing needs a human. Steps:

  control_lead0 / control_lead100
            headless: exact seek onto the first frame of all segments, with the
            segment starts exactly on the pts (0) and with the 100 us lead the EDL
            uses; time-pos and screenshot-raw vs the ffmpeg reference.
  playback  real-time play of the EDL (default: whole EDL): frame-drop-count,
            vo-delayed-frame-count, decoder-frame-drop-count, avsync.
  seams     at every seam: exact seek to the last frame of segment k-1,
            screenshot-raw, frame-step, screenshot-raw; both compared with the frame
            ffmpeg extracts from the proxy (mean abs diff < 2/255).
  seek      N random frames, `seek t absolute+exact`: time to playback-restart and,
            render mode, time to the first paint whose FBO read-back shows the target
            frame (and to the buffer swap after it).
  step      50 x frame-step and 50 x frame-back-step across a seam; time-pos change
            and painted frame (render mode).
  rebuild   real edits (shift the segment under the playhead / lengthen the one
            before it), `loadfile edl://... start=<pos>` and loadfile + seek; the
            screenshot at the restored position vs the reference of the frame the
            edited timeline must show; painted time for shifts.
  titles    sub-add ASS (Inter, Cyrillic), screenshot with subtitles -> PNG.
  playback_after  60 s of playback after all of the above.
  sync      headless encode of the EDL through mpv's own pipeline (o=, with the same
            lavfi-complex amix music bed): flash vs beep vs EDL.
  seam_audio  headless encode of the EDL over copies of the proxies whose audio has
            a steady tone (cuts land mid-waveform): each seam vs the ideal hard cut.

Writes spikes/results/s2/<out>-<UTC>.json and spikes/results/s2/titles-<out>.png.
"""

from __future__ import annotations

import argparse
import itertools
import math
import platform
import random
import subprocess
import sys
import threading
import time
import traceback
from fractions import Fraction
from pathlib import Path
from typing import Any

import mpv  # type: ignore[import-untyped]
import numpy as np

from spikes.common.detect import detect_beeps, detect_flashes
from spikes.common.media import SAMPLE_RATE, tool
from spikes.common.results import RESULTS_DIR, write_result
from spikes.s2_player import timeline as tl
from spikes.s2_player.prepare import (
    PROXY_H,
    PROXY_W,
    TITLE_FONT,
    TITLE_START_S,
    TITLE_TEXT,
    TONE_HZ,
    load_sources,
    make_music_bed,
    make_tone_proxies,
    prepare,
    reference_frames,
    write_title_ass,
)

DEFAULT_WORK = Path(__file__).resolve().parents[1] / "data" / "s2" / "Той Студия"
# Audio only: routing video through lavfi-complex too ("[vid1]null[vo]") forces
# hardware decoding into copy mode (frames are downloaded for the filter graph).
LAVFI_MUSIC = "[aid1][aid2]amix=inputs=2:duration=first:normalize=0[ao]"
EVT_TIMEOUT = 5.0


# ------------------------------------------------------------------ mpv helpers


class Driver:
    """Synchronous helpers over one mpv instance (called from the worker thread)."""

    def __init__(self, m: mpv.MPV, video: Any = None) -> None:
        self.m = m
        self.video = video

    def _wait_event(self, name: str, action: Any, cond: Any = None) -> float:
        """Run action, return perf_counter time of the first `name` event (cond)."""

        def stamp(evt: Any) -> float | None:
            if cond is not None and not cond(evt):
                return None
            return time.perf_counter()

        with self.m.prepare_and_wait_for_event(name, cond=stamp, timeout=EVT_TIMEOUT) as fut:
            action()
        return float(fut.result())

    def seek(self, t_s: float) -> tuple[float, float]:
        """Exact absolute seek; (t_issue, t_restart)."""
        t0 = 0.0

        def act() -> None:
            nonlocal t0
            t0 = time.perf_counter()
            self.m.command("seek", f"{t_s:.6f}", "absolute+exact")

        t1 = self._wait_event("playback-restart", act)
        return t0, t1

    def step(self, back: bool = False) -> tuple[float, float | None, float | None]:
        """frame-step / frame-back-step; (ms to new time-pos, new time-pos, prev).

        Waits for time-pos to change, then for pause to come back (frame-step plays
        one frame and pauses again) so the next screenshot shows the new frame.
        """
        before = self.m.time_pos
        changed = threading.Event()
        t_changed = [0.0]

        def obs(_name: str, val: Any) -> None:
            moved = val is not None and before is not None and abs(val - before) > 1e-4
            if moved and not changed.is_set():
                t_changed[0] = time.perf_counter()
                changed.set()

        self.m.observe_property("time-pos", obs)
        try:
            t0 = time.perf_counter()
            self.m.command("frame-back-step" if back else "frame-step")
            if not changed.wait(EVT_TIMEOUT):
                return float("nan"), None, before
        finally:
            self.m.unobserve_property("time-pos", obs)
        deadline = time.perf_counter() + EVT_TIMEOUT
        while not self.m.pause and time.perf_counter() < deadline:
            time.sleep(0.001)
        return (t_changed[0] - t0) * 1000, self.m.time_pos, before

    def shot(self, includes: str = "video") -> np.ndarray:
        r = self.m.command("screenshot-raw", includes)
        if r["format"] != "bgr0":
            raise RuntimeError(f"screenshot format {r['format']}")
        return tl.bgr0_from_raw(r["data"], r["w"], r["h"], r["stride"]).copy()

    def can_probe(self) -> bool:
        return self.video is not None and hasattr(self.video, "probe_shots")

    def painted_small(self, rgba: np.ndarray) -> np.ndarray:
        """Video rect of a read-back FBO, scaled to the proxy size (rect from mpv's
        osd-dimensions, read once: the video widget has a fixed size)."""
        if getattr(self, "_rect", None) is None or self._rect_fbo != rgba.shape[:2]:
            osd = self.m._get_property("osd-dimensions")
            self._rect = tl.video_rect(osd, rgba.shape[1], rgba.shape[0])
            self._rect_fbo = rgba.shape[:2]
        return tl.painted_small(rgba, self._rect, PROXY_W, PROXY_H)

    def counters(self) -> dict[str, Any]:
        out = {}
        for p in (
            "frame-drop-count",
            "decoder-frame-drop-count",
            "vo-delayed-frame-count",
            "mistimed-frame-count",
            "total-avsync-change",
        ):
            try:
                out[p] = self.m._get_property(p)
            except Exception:  # noqa: BLE001 - property may be unavailable
                out[p] = None
        return out

    def paint_after(self, t: float) -> float | None:
        if self.video is None or not hasattr(self.video, "wait_paint_after"):
            return None
        return self.video.wait_paint_after(t)

    def first_paint_after(self, t: float) -> float | None:
        if self.video is None or not hasattr(self.video, "first_paint_after"):
            return None
        return self.video.first_paint_after(t)


# ------------------------------------------------------------------ bench steps


def step_control(
    url: str,
    frames: list[tl.OutFrame],
    refs: dict[tuple[str, int], np.ndarray],
    lead_us: int,
) -> dict[str, Any]:
    """Headless: exact seek onto the first frame of every segment. Checks both the
    position (time-pos) and the picture (screenshot-raw vs the ffmpeg reference)."""
    m = mpv.MPV(vo="null", ao="null", pause=True, hr_seek="yes", keep_open="yes")
    try:
        d = Driver(m)
        d._wait_event("playback-restart", lambda: m.loadfile(url))
        firsts = [i for i, f in enumerate(frames) if i == 0 or frames[i - 1].seg != f.seg]
        late, wrong_pic, mads = [], [], []
        for i in firsts:
            f = frames[i]
            d.seek(f.t_us / tl.US)
            err = (m.time_pos or 0) * tl.US - f.t_us
            if abs(err) > 1000:
                late.append({"seg": f.seg, "src": f.src, "err_us": round(err)})
            mad = tl.mean_abs_diff(d.shot(), refs[(f.src, f.frame)])
            mads.append(mad)
            if mad >= tl.SEAM_MAD_MAX:
                wrong_pic.append({"seg": f.seg, "src": f.src, "mad": round(mad, 3)})
        return {
            "lead_us": lead_us,
            "segments": len(firsts),
            "first_frame_lost": len(late),
            "picture_wrong": len(wrong_pic),
            "first_mad": tl.summary_ms(mads),
            "examples": late[:5],
            "picture_examples": wrong_pic[:5],
        }
    finally:
        m.terminate()


def step_seams(
    d: Driver, frames: list[tl.OutFrame], refs: dict[tuple[str, int], np.ndarray]
) -> dict[str, Any]:
    firsts = [i for i, f in enumerate(frames) if i == 0 or frames[i - 1].seg != f.seg]
    rows = []
    for i in firsts:
        f = frames[i]
        row: dict[str, Any] = {"seg": f.seg, "src": f.src, "frame": f.frame}
        if i == 0:
            d.seek(0.0)
            row["pos_err_us"] = round((d.m.time_pos or 0) * tl.US - f.t_us)
        else:
            p = frames[i - 1]
            d.seek(p.t_us / tl.US)
            shot = d.shot()
            row["last_prev_mad"] = tl.mean_abs_diff(shot, refs[(p.src, p.frame)])
            ms, pos, _ = d.step()
            row["step_ms"] = ms
            row["pos_err_us"] = None if pos is None else round(pos * tl.US - f.t_us)
        shot = d.shot()
        row["first_mad"] = tl.mean_abs_diff(shot, refs[(f.src, f.frame)])
        row["first_next_mad"] = tl.mean_abs_diff(shot, refs[(f.src, f.frame + 1)])
        if i > 0:
            p = frames[i - 1]
            row["first_vs_prev_last_mad"] = tl.mean_abs_diff(shot, refs[(p.src, p.frame)])
        row["first_mean_luma"] = float(shot[..., :3].mean())
        rows.append(row)
    bad = [
        r
        for r in rows
        if r["first_mad"] >= tl.SEAM_MAD_MAX
        or r.get("last_prev_mad", 0) >= tl.SEAM_MAD_MAX
        or r.get("pos_err_us") is None
        or abs(r["pos_err_us"]) > 1000
    ]
    first = [r["first_mad"] for r in rows]
    last = [r["last_prev_mad"] for r in rows if "last_prev_mad" in r]
    others = [r["first_next_mad"] for r in rows] + [
        r["first_vs_prev_last_mad"] for r in rows if "first_vs_prev_last_mad" in r
    ]
    return {
        "segments": len(rows),
        "bad_seams": len(bad),
        "first_mad": tl.summary_ms(first),
        "last_prev_mad": tl.summary_ms(last),
        "wrong_frame_mad_min": round(min(others), 3),
        "min_first_luma": round(min(r["first_mean_luma"] for r in rows), 2),
        "bad_examples": bad[:10],
        "rows": rows,
    }


def step_playback(d: Driver, play_s: float | None, seams_us: list[int]) -> dict[str, Any]:
    m = d.m
    d.seek(0.0)
    events: list[dict[str, Any]] = []
    avsync: list[float] = []

    def on_counter(name: str, val: Any) -> None:
        if val:
            pos = m.time_pos or 0.0
            near = min(seams_us, key=lambda s: abs(s - pos * tl.US)) if seams_us else 0
            events.append(
                {
                    "prop": name,
                    "value": val,
                    "pos_s": pos,
                    "to_seam_ms": (pos * tl.US - near) / 1000,
                }
            )

    def on_avsync(_n: str, val: Any) -> None:
        if val is not None:
            avsync.append(float(val))

    props = ("frame-drop-count", "vo-delayed-frame-count", "mistimed-frame-count")
    before = d.counters()
    paints0 = getattr(d.video, "paints", None)
    for p in props:
        m.observe_property(p, on_counter)
    m.observe_property("avsync", on_avsync)
    duration = m.duration or 0.0
    target = duration if play_s is None else min(play_s, duration)
    t0 = time.perf_counter()
    m.pause = False
    try:
        while True:
            pos = m.time_pos or 0.0
            if m.eof_reached or pos >= target - 0.05:
                break
            if time.perf_counter() - t0 > target * 1.5 + 30:
                raise TimeoutError("playback did not reach the target")
            time.sleep(0.05)
    finally:
        m.pause = True
        for p in props:
            m.unobserve_property(p, on_counter)
        m.unobserve_property("avsync", on_avsync)
    wall = time.perf_counter() - t0
    after = d.counters()
    try:
        hwdec_now = m._get_property("hwdec-current")
    except Exception:  # noqa: BLE001
        hwdec_now = None
    delta = {
        k: (after[k] - before[k])
        if isinstance(after[k], int | float) and isinstance(before[k], int | float)
        else after[k]
        for k in after
    }
    from spikes.s2_player.render_widget import timer_resolution_ms

    out: dict[str, Any] = {
        "timer_ms": timer_resolution_ms(),
        "played_s": round(m.time_pos or 0.0, 3),
        "hwdec_current": hwdec_now,
        "wall_s": round(wall, 3),
        "counters_delta": delta,
        "counter_events": events[:2000],
        "drops_by_10s": _by_bucket(events, "frame-drop-count", 10.0),
        "avsync_ms": tl.summary_ms([abs(a) * 1000 for a in avsync]),
    }
    if paints0 is not None:
        out["paints"] = d.video.paints - paints0
        times = [t for t in d.video.paint_times if t0 <= t <= t0 + wall]
        gaps = [(b - a) * 1000 for a, b in itertools.pairwise(times)]
        out["paint_gap_ms"] = tl.summary_ms(gaps)
        out["paint_gaps_over_100ms"] = sum(1 for g in gaps if g > 100)
    return out


def _by_bucket(events: list[dict[str, Any]], prop: str, size_s: float) -> dict[str, int]:
    """Increments of a counter per playback-position bucket (where drops happen)."""
    out: dict[str, int] = {}
    last = 0
    for e in events:
        if e["prop"] != prop:
            continue
        key = f"{int(e['pos_s'] // size_s * size_s)}"
        out[key] = out.get(key, 0) + e["value"] - last
        last = e["value"]
    return out


def painted_hit(
    d: Driver,
    t0: float,
    target: np.ndarray,
    wrong: list[np.ndarray],
    timeout: float = 1.0,
) -> dict[str, Any]:
    """Wait for the first paint after t0 whose read-back FBO matches `target` (MAD
    below PAINTED_MAD_MAX and closer than every `wrong` reference). Returns the time
    of that paint and of the buffer swap after it, both from t0, in ms."""
    video = d.video
    deadline = time.perf_counter() + timeout
    seen = 0
    first_mad = None
    while True:
        shots = [s for s in list(video.probe_shots) if s[0] >= t0]
        for t, img in shots[seen:]:
            seen += 1
            small = d.painted_small(img)
            mt = tl.small_mad(small, target)
            if first_mad is None:
                first_mad = mt
            if mt < tl.PAINTED_MAD_MAX and all(mt < tl.small_mad(small, w) for w in wrong):
                sw = video.swap_after(t)
                return {
                    "paint_ms": (t - t0) * 1000,
                    "swap_ms": None if sw is None else (sw - t0) * 1000,
                    "mad": mt,
                    "paints_before": seen - 1,
                }
        if time.perf_counter() > deadline:
            return {"paint_ms": None, "swap_ms": None, "first_mad": first_mad, "paints": seen}
        time.sleep(0.003)


def _painted_summary(hits: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [h for h in hits if h["paint_ms"] is not None]
    swaps = [h["swap_ms"] if h["swap_ms"] is not None else h["paint_ms"] for h in ok]
    return {
        "n": len(hits),
        "not_seen_within_1s": len(hits) - len(ok),
        "to_painted_ms": tl.summary_ms([h["paint_ms"] for h in ok]),
        "to_swap_ms": tl.summary_ms(swaps),
        "painted_mad": tl.summary_ms([h["mad"] for h in ok], 3),
        "stale_paints_before_hit": sum(h["paints_before"] for h in ok),
    }


def step_seek(
    d: Driver,
    frames: list[tl.OutFrame],
    picks: list[int],
    refs: dict[tuple[str, int], np.ndarray],
    playing: bool,
) -> dict[str, Any]:
    """Exact seeks to the picked frames. Paused (render mode): the FBO is read back
    after every paint and the time to the first paint that shows the target frame
    is measured as well as the time to playback-restart."""
    m = d.m
    probe = not playing and d.can_probe()
    times, after_restart, wrong, hits = [], [], [], []
    prev: tuple[str, int] | None = None
    if playing:
        m.pause = False
    try:
        for i in picks:
            f = frames[i]
            key = (f.src, f.frame)
            if probe:
                d.video.probe_shots.clear()
                d.video.probe = True
            t0, t1 = d.seek(f.t_us / tl.US)
            times.append((t1 - t0) * 1000)
            if probe:
                wrong_refs = [refs[(f.src, f.frame + 1)]] + ([refs[prev]] if prev else [])
                hits.append(painted_hit(d, t0, refs[key], wrong_refs))
                d.video.probe = False
                d.video.probe_shots.clear()
            ta = d.paint_after(t1)
            if ta is not None:
                after_restart.append((ta - t0) * 1000)
            if not playing:
                err = (m.time_pos or 0) * tl.US - f.t_us
                if abs(err) > 1000:
                    wrong.append({"t_us": round(f.t_us), "err_us": round(err)})
            prev = key
    finally:
        m.pause = True
        if d.can_probe():
            d.video.probe = False
    out: dict[str, Any] = {"n": len(picks), "playing": playing}
    out["to_restart_ms"] = tl.summary_ms(times)
    if hits:
        out["painted"] = _painted_summary(hits)
    if after_restart:
        out["to_paint_after_restart_ms"] = tl.summary_ms(after_restart)
    if not playing:
        out["wrong_frame"] = len(wrong)
        out["wrong_examples"] = wrong[:5]
    return out


def step_frame_steps(
    d: Driver,
    frames: list[tl.OutFrame],
    start: int,
    n: int,
    refs: dict[tuple[str, int], np.ndarray],
) -> dict[str, Any]:
    """n forward steps then n back steps from `start` (n/2 frames before a seam).
    Render mode: also time to the first paint showing the new frame."""
    probe = d.can_probe()
    d.seek(frames[start].t_us / tl.US)
    res: dict[str, Any] = {"start_frame_index": start}
    for back in (False, True):
        ms_list, errors, hits = [], [], []
        i = start if not back else start + n
        for _ in range(n):
            if probe:
                d.video.probe_shots.clear()
                d.video.probe = True
            t0 = time.perf_counter()
            ms, pos, _prev = d.step(back=back)
            old = frames[i]
            i = i - 1 if back else i + 1
            if probe:
                f = frames[i]
                hits.append(
                    painted_hit(d, t0, refs[(f.src, f.frame)], [refs[(old.src, old.frame)]])
                )
                d.video.probe = False
                d.video.probe_shots.clear()
            if pos is None or math.isnan(ms):
                errors.append({"i": i, "error": "timeout"})
                continue
            ms_list.append(ms)
            err = pos * tl.US - frames[i].t_us
            if abs(err) > 1000:
                errors.append({"i": i, "err_us": round(err)})
                j = tl.frame_index_at(frames, pos * tl.US)
                if j is not None:
                    i = j  # resync so one error is not counted n times
        part: dict[str, Any] = {
            "steps": n,
            "errors": len(errors),
            "error_examples": errors[:5],
            "ms": tl.summary_ms(ms_list),
        }
        if hits:
            part["painted"] = _painted_summary(hits)
        res["back" if back else "forward"] = part
    return res


def rebuild_plan(
    segments: list[tl.Segment], sources: dict[str, tl.Source], n: int, seed: int
) -> list[dict[str, Any]]:
    """n real edits around a random playhead. Kinds alternate in pairs:
    shift   the segment under the playhead moves 25 frames in its source, so the
            picture at the (unchanged) position changes;
    extend  the segment before the playhead gets 10 frames longer, so the playhead
            moves (map_position) and the picture stays the same.
    Each kind is tried with both loadfile variants."""
    rng = random.Random(seed)
    base = tl.output_frames(segments, sources)
    plan = []
    for it in range(n):
        kind = "shift" if (it // 2) % 2 == 0 else "extend"
        while True:
            f = base[rng.randrange(len(base) - 10)]
            if f.seg > 0:
                break
        if kind == "shift":
            new = tl.edit_segment(segments, sources, f.seg, 25)
            new_pos = f.t_us
        else:
            new = tl.edit_length(segments, sources, f.seg - 1, 10)
            new_pos = tl.map_position(base, tl.output_frames(new, sources), f.t_us)
        new_frames = tl.output_frames(new, sources)
        j = tl.frame_index_at(new_frames, new_pos)
        if j is None:
            raise RuntimeError("rebuild plan: no frame at the mapped position")
        nf = new_frames[j]
        plan.append(
            {
                "kind": kind,
                "variant": "loadfile_start" if it % 2 == 0 else "loadfile_then_seek",
                "segments": new,
                "old_pos_us": f.t_us,
                "new_pos_us": nf.t_us,
                "old_frame": (f.src, f.frame),
                "new_frame": (nf.src, nf.frame),
                "neighbours": [(nf.src, nf.frame - 1), (nf.src, nf.frame + 1)],
            }
        )
    return plan


def step_rebuild(
    d: Driver,
    segments: list[tl.Segment],
    sources: dict[str, tl.Source],
    plan: list[dict[str, Any]],
    refs: dict[tuple[str, int], np.ndarray],
    playing: bool,
) -> dict[str, Any]:
    """Apply each planned edit: playhead at old_pos, reload the edited EDL at the
    mapped position. Paused: screenshot-raw at the restored position vs the
    reference of the frame the edited timeline must show there (and vs the old
    frame for shifts), plus time to the first painted correct frame."""
    m = d.m
    base_url = tl.edl_url(segments, sources)
    probe = not playing and d.can_probe()
    rows = []
    for e in plan:
        url = tl.edl_url(e["segments"], sources)
        old_s, new_s = e["old_pos_us"] / tl.US, e["new_pos_us"] / tl.US
        d.seek(old_s)
        m.pause = not playing

        def at_pos(_evt: Any, p: float = new_s) -> bool:
            tp = m.time_pos
            return tp is not None and (playing or abs(tp - p) < 0.002)

        if probe:
            d.video.probe_shots.clear()
            d.video.probe = True
        t0 = time.perf_counter()
        if e["variant"] == "loadfile_start":
            t1 = d._wait_event(
                "playback-restart",
                lambda u=url, p=new_s: m.loadfile(u, "replace", start=f"{p:.6f}"),
                at_pos,
            )
        else:
            d._wait_event("file-loaded", lambda u=url: m.loadfile(u, "replace"))
            t1 = d._wait_event(
                "playback-restart",
                lambda p=new_s: m.command("seek", f"{p:.6f}", "absolute+exact"),
                at_pos,
            )
        row: dict[str, Any] = {
            "kind": e["kind"],
            "variant": e["variant"],
            "to_restart_ms": (t1 - t0) * 1000,
        }
        tp = d.paint_after(t1)
        row["to_paint_after_restart_ms"] = None if tp is None else (tp - t0) * 1000
        if probe:
            # Only a shift changes the picture; after an extend the old frame is the
            # right one, so a paint "hit" would just be the stale picture.
            if e["kind"] == "shift":
                wrong = [refs[k] for k in e["neighbours"]] + [refs[e["old_frame"]]]
                row["painted"] = painted_hit(d, t0, refs[e["new_frame"]], wrong)
            d.video.probe = False
            d.video.probe_shots.clear()
        if not playing:
            shot = d.shot()
            row["pos_err_us"] = round((m.time_pos or 0) * tl.US - e["new_pos_us"])
            row["new_mad"] = tl.mean_abs_diff(shot, refs[e["new_frame"]])
            row["old_mad"] = tl.mean_abs_diff(shot, refs[e["old_frame"]])
            row["frame_ok"] = abs(row["pos_err_us"]) <= 1000 and row["new_mad"] < tl.SEAM_MAD_MAX
            if e["kind"] == "shift":  # the edit must be visible: old picture gone
                row["frame_ok"] = row["frame_ok"] and row["old_mad"] >= tl.SEAM_MAD_MAX
        rows.append(row)
        m.pause = True
        d._wait_event(
            "playback-restart",
            lambda p=old_s: m.loadfile(base_url, "replace", start=f"{p:.6f}"),
        )
    out: dict[str, Any] = {"n": len(plan), "playing": playing, "edl_segments": len(segments)}
    for variant in ("loadfile_start", "loadfile_then_seek"):
        sub = [r for r in rows if r["variant"] == variant]
        part: dict[str, Any] = {
            "to_restart_ms": tl.summary_ms([r["to_restart_ms"] for r in sub]),
            "to_paint_after_restart_ms": tl.summary_ms(
                [r["to_paint_after_restart_ms"] for r in sub if r["to_paint_after_restart_ms"]]
            ),
        }
        if probe:
            part["painted_shift"] = _painted_summary([r["painted"] for r in sub if "painted" in r])
            part["extend_to_restart_ms"] = tl.summary_ms(
                [r["to_restart_ms"] for r in sub if r["kind"] == "extend"]
            )
        if not playing:
            part["frame_ok"] = sum(1 for r in sub if r["frame_ok"])
            part["frame_wrong"] = sum(1 for r in sub if not r["frame_ok"])
        out[variant] = part
    out["rows"] = rows
    return out


def step_titles(d: Driver, ass: Path, png: Path, log: list[str]) -> dict[str, Any]:
    m = d.m
    log_start = len(log)
    m.command("sub-add", str(ass))
    time.sleep(0.2)
    d.seek(TITLE_START_S + 1.0)
    time.sleep(0.2)
    with_subs = d.shot("subtitles")
    plain = d.shot("video")
    changed = int(
        (np.abs(with_subs[..., :3].astype(np.int16) - plain[..., :3]).max(axis=2) > 32).sum()
    )
    save_png(with_subs, png)
    m.command("sub-remove")
    font_lines = [ln for ln in log[log_start:] if "font" in ln.lower()]
    return {
        "text": TITLE_TEXT,
        "requested_font": TITLE_FONT,
        "png": str(png.relative_to(RESULTS_DIR.parent.parent))
        if png.is_relative_to(RESULTS_DIR.parent.parent)
        else str(png),
        "changed_pixels": changed,
        "rendered": changed > 500,
        "font_log": font_lines[:20],
    }


def save_png(img: np.ndarray, path: Path) -> None:
    from PySide6.QtGui import QImage

    h, w = img.shape[:2]
    buf = np.ascontiguousarray(img)
    qimg = QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGB32)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not qimg.copy().save(str(path), "PNG"):
        raise RuntimeError(f"cannot save {path}")


def step_sync(
    url: str,
    bed: Path | None,
    segments: list[tl.Segment],
    sources: dict[str, tl.Source],
    out: Path,
    hwdec: str,
) -> dict[str, Any]:
    """Headless encode through mpv's playback pipeline (o=) with the music bed, then
    flash/beep detectors vs the EDL."""
    extra: dict[str, Any] = {}
    if bed is not None:
        extra = {"audio_files": str(bed), "lavfi_complex": LAVFI_MUSIC}
    info = encode_through_mpv(url, out, hwdec, extra)
    expected = tl.expected_events(segments, sources)
    report = tl.sync_report(detect_flashes(out), detect_beeps(out), expected)
    return {
        "music_bed": bed is not None,
        "music_440hz_level": tone_level(decode_mono(out), 440.0),
        **info,
        **report,
    }


def encode_through_mpv(url: str, out: Path, hwdec: str, extra: dict[str, Any]) -> dict[str, Any]:
    """Run mpv's encoding mode over url -> out (matroska, x264, PCM audio)."""
    opts: dict[str, Any] = {
        "o": str(out),
        "of": "matroska",
        "ovc": "libx264",
        "ovcopts": "preset=veryfast,crf=18",
        "oac": "pcm_s16le",
        "hwdec": hwdec,
        **extra,
    }
    errors: list[str] = []
    m = mpv.MPV(
        log_handler=lambda lvl, comp, text: (
            errors.append(f"{lvl} {comp}: {text.strip()}") if lvl in ("error", "fatal") else None
        ),
        loglevel="error",
        **opts,
    )
    t0 = time.perf_counter()
    try:
        m.loadfile(url)
        m.wait_for_event("end-file", timeout=1800)
    finally:
        m.terminate()
    return {"encode_s": round(time.perf_counter() - t0, 2), "errors": errors[:10]}


def step_seam_audio(
    segments: list[tl.Segment], tone_sources: dict[str, tl.Source], out: Path, hwdec: str
) -> dict[str, Any]:
    """Clip audio with a continuous tone, cut by the same EDL, encoded by mpv (no
    music): every seam vs the ideal hard cut from the continuously decoded sources."""
    url = tl.edl_url(segments, tone_sources)
    info = encode_through_mpv(url, out, hwdec, {})
    played = decode_mono(out)
    src_audio = {k: decode_mono(Path(s.path)) for k, s in tone_sources.items()}
    res = tl.seam_residuals(played, src_audio, segments, tone_sources, SAMPLE_RATE)
    return {**info, "tone_hz": TONE_HZ, **res}


def tone_level(samples: np.ndarray, freq: float, win_s: float = 0.1) -> float:
    """Median single-bin amplitude of `freq` over 100 ms windows (music present?)."""
    win = round(SAMPLE_RATE * win_s)
    n = len(samples) // win
    if n == 0:
        return 0.0
    frames = samples[: n * win].reshape(n, win).astype(np.float64)
    basis = np.exp(-2j * np.pi * freq * np.arange(win) / SAMPLE_RATE)
    return round(float(np.median(2 * np.abs(frames @ basis) / win)), 4)


def decode_mono(path: Path) -> np.ndarray:
    cmd = [
        tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
    ]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return np.frombuffer(proc.stdout, dtype="<f4")


# ------------------------------------------------------------------ GUI run


def run_gui(args: argparse.Namespace, ctx: dict[str, Any]) -> dict[str, Any]:
    from PySide6.QtCore import QObject, Qt, QTimer, Signal
    from PySide6.QtWidgets import QApplication

    class Quitter(QObject):
        done = Signal()

    from spikes.s2_player.render_widget import PlayerWindow

    log: list[str] = []

    def log_handler(level: str, comp: str, text: str) -> None:
        line = f"{level} {comp}: {text.strip()}"
        low = text.lower()
        if level in ("error", "fatal", "warn") or "font" in low or "hardware decoding" in low:
            log.append(line)

    opts: dict[str, Any] = {"hwdec": args.hwdec, "log_handler": log_handler, "loglevel": "info"}
    if ctx["bed"] is not None:
        opts["audio_files"] = str(ctx["bed"])
        opts["lavfi_complex"] = LAVFI_MUSIC
    if args.fonts_dir:
        opts["sub_fonts_dir"] = str(args.fonts_dir)
    for kv in args.mpv_opt:
        k, _, val = kv.partition("=")
        opts[k.replace("-", "_")] = val

    from spikes.s2_player.render_widget import (
        disable_app_nap,
        disable_windows_throttling,
        timer_resolution_ms,
    )

    if args.mode == "render":
        from spikes.s2_player.render_widget import configure_gl

        configure_gl()
    app_nap_disabled = disable_app_nap()
    win_throttling = (
        {"skipped": True, "timer": timer_resolution_ms()}
        if args.no_win_throttle_fix
        else disable_windows_throttling()
    )
    app = QApplication.instance() or QApplication(sys.argv[:1])
    if args.mode == "render":
        from spikes.s2_player.render_widget import MpvRenderWidget

        video: Any = MpvRenderWidget(block_for_target_time=args.block, **opts)
    else:
        from spikes.s2_player.wid_widget import MpvWidWidget

        video = MpvWidWidget(**opts)
    # Fixed 960x540 video area: on a 2x screen the FBO video rect is exactly 2x the
    # proxy, so the painted-frame probe compares like with like.
    video.setFixedSize(PROXY_W, PROXY_H)
    win = PlayerWindow(video)
    # macOS throttles drawing of windows covered by other apps; keep ours on top
    # and in the corner so the playback numbers measure mpv, not occlusion.
    win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    win.move(0, 0)
    win.show()
    win.raise_()
    win.activateWindow()
    result: dict[str, Any] = {}
    quitter = Quitter()
    quitter.done.connect(app.quit, Qt.ConnectionType.QueuedConnection)

    def worker() -> None:
        d = Driver(video.mpv, video)
        try:
            time.sleep(0.5)  # let the window and GL context come up
            t0 = time.perf_counter()
            t1 = d._wait_event("playback-restart", lambda: video.mpv.loadfile(ctx["url"]))
            result["first_load_ms"] = (t1 - t0) * 1000
            result["video_params"] = video.mpv._get_property("video-params")
            result["hwdec_current"] = video.mpv._get_property("hwdec-current")
            result["duration_s"] = video.mpv.duration
            result["app_nap_disabled"] = app_nap_disabled
            result["win_throttling"] = win_throttling
            scr = win.screen()
            result["screen"] = {"refresh_hz": scr.refreshRate(), "dpr": scr.devicePixelRatio()}
            for p in ("display-fps", "estimated-display-fps", "video-sync"):
                try:
                    result[p] = video.mpv._get_property(p)
                except Exception:  # noqa: BLE001
                    result[p] = None
            steps = (
                ("playback", lambda: step_playback(d, args.play_s, ctx["starts"][1:-1])),
                ("seams", lambda: step_seams(d, ctx["frames"], ctx["refs"])),
                (
                    "seek_paused",
                    lambda: step_seek(d, ctx["frames"], ctx["seek_picks"], ctx["refs"], False),
                ),
                (
                    "seek_playing",
                    lambda: step_seek(d, ctx["frames"], ctx["seek_play_picks"], ctx["refs"], True),
                ),
                (
                    "frame_step",
                    lambda: step_frame_steps(
                        d, ctx["frames"], ctx["step_start"], args.steps, ctx["refs"]
                    ),
                ),
                (
                    "rebuild_paused",
                    lambda: step_rebuild(
                        d, ctx["segments"], ctx["sources"], ctx["rebuilds"], ctx["refs"], False
                    ),
                ),
                (
                    "rebuild_playing",
                    lambda: step_rebuild(
                        d, ctx["segments"], ctx["sources"], ctx["rebuilds_play"], ctx["refs"], True
                    ),
                ),
                ("titles", lambda: step_titles(d, ctx["ass"], ctx["png"], log)),
                (
                    "playback_after",
                    lambda: step_playback(d, args.play_after_s, ctx["starts"][1:-1]),
                ),
            )
            for name, fn in steps:
                if not wanted_step(args, name):
                    continue
                print(f"[s2] {name} ...", flush=True)
                t = time.perf_counter()
                try:
                    result[name] = fn()
                except Exception:  # noqa: BLE001 - record and continue with the next step
                    result[name] = {"error": traceback.format_exc()}
                print(f"[s2] {name} done in {time.perf_counter() - t:.1f} s", flush=True)
            if hasattr(video, "render_ms"):
                result["render_call_ms"] = tl.summary_ms(video.render_ms)
        except Exception:  # noqa: BLE001
            result["fatal"] = traceback.format_exc()
        finally:
            result["log_tail"] = [ln for ln in log if not ln.startswith("v ")][-40:]
            quitter.done.emit()  # QTimer cannot fire in a thread without an event loop

    th = threading.Thread(target=worker, daemon=True)
    QTimer.singleShot(200, th.start)
    app.exec()
    th.join(timeout=10)
    video.shutdown()
    win.close()
    return result


# ------------------------------------------------------------------ verdicts


VERDICT_KEYS = (
    "seams_0_bad",
    "control_lead100_first_frames",
    "playback_0_dropped_or_delayed",
    "playback_after_seeks_0_dropped_or_delayed",
    "seek_painted_p95_le_150ms",
    "seek_event_p95_le_150ms",
    "rebuild_paused_le_300ms",
    "rebuild_playing_le_300ms",
    "frame_step_le_100ms_no_errors",
    "sync_within_1_frame",
    "seam_audio_no_glitch",
    "titles_rendered",
)


def _err(r: dict[str, Any], name: str) -> str | None:
    step = r.get(name)
    if step is None:
        return "not run"
    if isinstance(step, dict) and "error" in step:
        return "step failed: " + step["error"].strip().splitlines()[-1]
    return None


def _drops(c: dict[str, Any]) -> int:
    keys = ("frame-drop-count", "vo-delayed-frame-count", "decoder-frame-drop-count")
    return sum(int(c.get(k) or 0) for k in keys)


def _worst(*values: float | None) -> float | None:
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def verdicts(r: dict[str, Any]) -> dict[str, Any]:
    """One entry per brief metric, always present: pass None + reason when the step
    did not run or failed."""
    v: dict[str, Any] = {}

    def put(key: str, step: str, make: Any) -> None:
        reason = _err(r, step)
        if reason is None:
            try:
                v[key] = make(r[step])
                return
            except (KeyError, TypeError, ValueError) as exc:
                reason = f"incomplete result: {exc!r}"
        v[key] = {"value": None, "pass": None, "reason": reason}

    put("seams_0_bad", "seams", lambda s: tl.verdict(s["bad_seams"], 0, unit="seams"))
    put(
        "control_lead100_first_frames",
        "control_lead100",
        lambda c: tl.verdict(c["first_frame_lost"] + c["picture_wrong"], 0, unit="segments wrong"),
    )
    put(
        "playback_0_dropped_or_delayed",
        "playback",
        lambda p: tl.verdict(_drops(p["counters_delta"]), 0, unit="frames"),
    )
    put(
        "playback_after_seeks_0_dropped_or_delayed",
        "playback_after",
        lambda p: tl.verdict(_drops(p["counters_delta"]), 0, unit="frames"),
    )

    def seek_painted(s: dict[str, Any]) -> dict[str, Any]:
        p = s["painted"]
        val = p["to_swap_ms"].get("p95")
        ok = val is not None and val <= tl.SEEK_P95_MS and p["not_seen_within_1s"] == 0
        return tl.verdict(val, tl.SEEK_P95_MS, unit="ms", ok=ok)

    put("seek_painted_p95_le_150ms", "seek_paused", seek_painted)
    put(
        "seek_event_p95_le_150ms",
        "seek_paused",
        lambda s: tl.verdict(s["to_restart_ms"]["p95"], tl.SEEK_P95_MS, unit="ms"),
    )

    def rebuild(rb: dict[str, Any]) -> dict[str, Any]:
        parts = [rb[k] for k in ("loadfile_start", "loadfile_then_seek")]
        if "painted_shift" in parts[0]:  # paused, render: first correct painted frame
            vals = [p["painted_shift"]["to_swap_ms"].get("max") for p in parts]
            vals += [p["extend_to_restart_ms"].get("max") for p in parts]
            unseen = sum(p["painted_shift"]["not_seen_within_1s"] for p in parts)
            wrong = sum(p.get("frame_wrong", 0) for p in parts)
            val = _worst(*vals)
            ok = val is not None and val <= tl.REBUILD_MAX_MS and unseen == 0 and wrong == 0
            return {
                **tl.verdict(val, tl.REBUILD_MAX_MS, unit="ms", ok=ok),
                "basis": "painted (shift edits); playback-restart (extend edits, same picture)",
            }
        vals = [p["to_paint_after_restart_ms"].get("max") for p in parts]
        vals += [p["to_restart_ms"].get("max") for p in parts]
        val = _worst(*vals)
        wrong = sum(p.get("frame_wrong", 0) for p in parts)
        ok = val is not None and val <= tl.REBUILD_MAX_MS and wrong == 0
        return {
            **tl.verdict(val, tl.REBUILD_MAX_MS, unit="ms", ok=ok),
            "basis": "paint after playback-restart (upper bound)",
        }

    put("rebuild_paused_le_300ms", "rebuild_paused", rebuild)
    put("rebuild_playing_le_300ms", "rebuild_playing", rebuild)

    def step(fs: dict[str, Any]) -> dict[str, Any]:
        parts = [fs["forward"], fs["back"]]
        errs = sum(p["errors"] for p in parts)
        if all("painted" in p for p in parts):
            val = _worst(*[p["painted"]["to_swap_ms"].get("max") for p in parts])
            errs += sum(p["painted"]["not_seen_within_1s"] for p in parts)
            basis = "painted"
        else:
            val = _worst(*[p["ms"].get("max") for p in parts])
            basis = "time-pos change"
        ok = val is not None and val <= tl.STEP_MAX_MS and errs == 0
        return {**tl.verdict(val, tl.STEP_MAX_MS, unit="ms", ok=ok), "basis": basis}

    put("frame_step_le_100ms_no_errors", "frame_step", step)
    put(
        "sync_within_1_frame",
        "sync",
        lambda sy: tl.verdict(
            sy["worst_abs_us"] / 1000,
            sy["frame_us_used"] / 1000,
            unit="ms",
            ok=sy["worst_abs_us"] <= sy["frame_us_used"] and sy["all_events_found"],
        ),
    )
    put(
        "seam_audio_no_glitch",
        "seam_audio",
        lambda a: tl.verdict(a["seams_above_limit"] + a["lag_at_search_edge"], 0, unit="seams"),
    )
    put(
        "titles_rendered",
        "titles",
        lambda t: {
            "value": t["changed_pixels"],
            "pass": t["rendered"],
            "note": "tofu: check PNG by eye",
        },
    )
    return v


# ------------------------------------------------------------------ main


def _cli_features() -> list[str] | None:
    """`mpv -v --version` feature list (the CLI of the same build; libmpv has no
    property for it). Contains "gpl" unless built with -Dgpl=false."""
    try:
        proc = subprocess.run(
            [tool("mpv"), "-v", "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
    except Exception:  # noqa: BLE001 - mpv CLI is optional (Windows ships only the DLL)
        return None
    for line in proc.stdout.splitlines():
        if "List of enabled features:" in line:
            return line.split(":", 1)[1].split()
    return None


def libmpv_info() -> dict[str, Any]:
    """Version and build configuration of the loaded libmpv (license check)."""
    m = mpv.MPV(vo="null", ao="null")
    try:
        conf = m._get_property("mpv-configuration")
        return {
            "version": m.mpv_version,
            "ffmpeg_version": m._get_property("ffmpeg-version"),
            "configuration": conf,
            "gpl_false_in_configuration": "-Dgpl=false" in (conf or ""),
            "library": getattr(mpv.backend, "_name", None),
            "cli_features": _cli_features(),
        }
    finally:
        m.terminate()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--mode", choices=["render", "wid"], required=True)
    ap.add_argument("--segments", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2)
    ap.add_argument("--work", type=Path, default=DEFAULT_WORK)
    ap.add_argument("--out", default=None, help="result name (default <os>-<mode>)")
    ap.add_argument("--seeks", type=int, default=100)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--rebuilds", type=int, default=20)
    ap.add_argument(
        "--play-s", type=float, default=None, help="real-time playback length (default: all)"
    )
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--fonts-dir", type=Path, default=None, help="sub-fonts-dir (e.g. with Inter)")
    ap.add_argument(
        "--hwdec", default="no", help="no (default: 540p proxies decode cheaply) or auto-safe"
    )
    ap.add_argument(
        "--gop-s", type=float, default=1.0, help="proxy GOP (1 s = §5; other = experiment)"
    )
    ap.add_argument(
        "--play-after-s", type=float, default=60.0, help="second playback after the seek steps"
    )
    ap.add_argument("--skip-sync", action="store_true", help="skip sync and seam_audio")
    ap.add_argument(
        "--no-win-throttle-fix",
        action="store_true",
        help="Windows: do not opt out of power throttling / 1 ms timers (for comparison)",
    )
    ap.add_argument(
        "--only",
        default="",
        help="comma list of steps to run (control_lead0, control_lead100, playback, seams, "
        "seek_paused, seek_playing, frame_step, rebuild_paused, rebuild_playing, titles, "
        "playback_after, sync, seam_audio)",
    )
    ap.add_argument(
        "--block",
        action="store_true",
        help="render API: block_for_target_time=1 in paintGL (mpv default; the bench "
        "default is 0, which keeps paintGL from sleeping until the frame's display time)",
    )
    ap.add_argument(
        "--mpv-opt", action="append", default=[], help="extra mpv option k=v (repeatable)"
    )
    args = ap.parse_args(argv)
    name = args.out or f"{platform.system().lower()}-{args.mode}"
    if args.mode == "wid" and sys.platform == "darwin":
        print(
            "warning: wid embedding is not reliable on macOS (render API is the Mac path)",
            file=sys.stderr,
        )

    print("[s2] preparing media ...", flush=True)
    paths = prepare(args.work, args.gop_s)
    sources = load_sources(paths["proxy"])
    segments = tl.make_segments(sources, args.segments, seed=args.seed)
    url = tl.edl_url(segments, sources)
    frames = tl.output_frames(segments, sources)
    starts = tl.seg_out_starts(segments, sources)
    total_s = starts[-1] / tl.US

    # Everything random is drawn up front, so the reference frames can be extracted
    # before the window opens.
    rng = random.Random(args.seed)
    seek_picks = [rng.randrange(len(frames) - 10) for _ in range(args.seeks)]
    seek_play_picks = [rng.randrange(len(frames) - 10) for _ in range(max(10, args.seeks // 3))]
    firsts = [i for i, f in enumerate(frames) if i and frames[i - 1].seg != f.seg]
    step_start = max(1, firsts[len(firsts) // 2] - args.steps // 2)
    rebuilds = rebuild_plan(segments, sources, args.rebuilds, args.seed + 1)
    rebuilds_play = rebuild_plan(segments, sources, max(4, args.rebuilds // 2), args.seed + 2)

    wanted: set[tuple[str, int]] = set()
    for i, f in enumerate(frames):
        if i == 0 or frames[i - 1].seg != f.seg:
            wanted |= {(f.src, f.frame), (f.src, f.frame + 1)}
            if i:
                wanted.add((frames[i - 1].src, frames[i - 1].frame))
    for i in seek_picks:
        wanted |= {(frames[i].src, frames[i].frame), (frames[i].src, frames[i].frame + 1)}
    for i in range(step_start - 1, step_start + args.steps + 2):
        wanted.add((frames[i].src, frames[i].frame))
    for e in rebuilds:
        wanted |= {e["old_frame"], e["new_frame"], *e["neighbours"]}
    refs = reference_frames(sources, wanted, args.work / f"frames-{paths['proxy'].name}")
    bed = (
        None
        if args.no_music
        else make_music_bed(paths["music"], args.work / f"music_bed-{args.seed}.wav", total_s + 1)
    )
    ass = write_title_ass(args.work / "title.ass")
    png = RESULTS_DIR / "s2" / f"titles-{name}.png"
    ctx = {
        "url": url,
        "frames": frames,
        "refs": refs,
        "starts": starts,
        "segments": segments,
        "sources": sources,
        "bed": bed,
        "ass": ass,
        "png": png,
        "seek_picks": seek_picks,
        "seek_play_picks": seek_play_picks,
        "step_start": step_start,
        "rebuilds": rebuilds,
        "rebuilds_play": rebuilds_play,
    }

    result: dict[str, Any] = {
        "spike": "s2",
        "mode": args.mode,
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "edl": {
            "segments": len(segments),
            "frames": len(frames),
            "total_s": total_s,
            "lead_us": tl.CUT_LEAD_US,
            "sources": {k: {"fps": str(s.fps), "frames": s.frames} for k, s in sources.items()},
            "url_chars": len(url),
            "seg_len_s": tl.summary_ms(
                [float(Fraction(s.frames) / sources[s.src].fps) for s in segments]
            ),
        },
        "thresholds": {
            "seam_mad_max": tl.SEAM_MAD_MAX,
            "painted_mad_max": tl.PAINTED_MAD_MAX,
            "seek_p95_ms": tl.SEEK_P95_MS,
            "rebuild_max_ms": tl.REBUILD_MAX_MS,
            "step_max_ms": tl.STEP_MAX_MS,
            "sync_max_frames": tl.SYNC_MAX_FRAMES,
            "seam_audio_max": tl.SEAM_AUDIO_MAX,
        },
    }
    result["libmpv"] = libmpv_info()

    def headless(step: str, fn: Any) -> None:
        if not wanted_step(args, step):
            return
        print(f"[s2] {step} ...", flush=True)
        try:
            result[step] = fn()
        except Exception:  # noqa: BLE001 - record; the verdict says "step failed"
            result[step] = {"error": traceback.format_exc()}

    def control(lead_us: int) -> dict[str, Any]:
        keep = tl.CUT_LEAD_US
        tl.CUT_LEAD_US = lead_us
        try:
            u, fr = tl.edl_url(segments, sources), tl.output_frames(segments, sources)
        finally:
            tl.CUT_LEAD_US = keep
        return step_control(u, fr, refs, lead_us)

    headless("control_lead0", lambda: control(0))
    headless("control_lead100", lambda: control(tl.CUT_LEAD_US))

    gui_steps = {
        "playback", "seams", "seek_paused", "seek_playing", "frame_step",
        "rebuild_paused", "rebuild_playing", "titles", "playback_after",
    }  # fmt: skip
    if any(wanted_step(args, s) for s in gui_steps):
        result.update(run_gui(args, ctx))

    if not args.skip_sync:
        headless(
            "sync",
            lambda: step_sync(
                url, bed, segments, sources, args.work / f"sync-{name}.mkv", args.hwdec
            ),
        )
        headless(
            "seam_audio",
            lambda: step_seam_audio(
                segments,
                load_sources(make_tone_proxies(paths["proxy"])),
                args.work / f"seam-audio-{name}.mkv",
                args.hwdec,
            ),
        )

    result["verdicts"] = verdicts(result)
    path = write_result("s2", name, result)
    print(path)
    for k, val in result["verdicts"].items():
        print(f"  {k}: {val}")
    return 0


def wanted_step(args: argparse.Namespace, step: str) -> bool:
    return not args.only or step in args.only.split(",")


if __name__ == "__main__":
    sys.exit(main())
