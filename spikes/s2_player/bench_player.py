"""S2: unattended benchmark of libmpv as the draft-editor player.

    python -m spikes.s2_player.bench_player --mode render|wid [--segments 200] [--seed 2]
        [--work DIR] [--out NAME] [--seeks 100] [--steps 50] [--rebuilds 20]
        [--play-s SECONDS] [--no-music] [--fonts-dir DIR] [--hwdec auto-safe]

Opens a real window (render: QOpenGLWidget + render API; wid: native child window),
drives mpv from a worker thread and exits. Everything is measured from mpv
properties and events; nothing needs a human. Steps:

  control   headless: same EDL with segment starts exactly on the first frame's pts
            (lead 0) -> how many first frames mpv drops (why the EDL uses a lead).
  seams     at every seam: exact seek to the last frame of segment k-1,
            screenshot-raw, frame-step, screenshot-raw; both compared with the frame
            ffmpeg extracts from the proxy (mean abs diff < 2/255).
  playback  real-time play of the EDL (default: whole EDL): frame-drop-count,
            vo-delayed-frame-count, mistimed-frame-count, avsync.
  seek      N random frames, `seek t absolute+exact` -> playback-restart (paused and
            while playing); render mode also times the first paint after it.
  step      50 x frame-step and 50 x frame-back-step across a seam.
  rebuild   EDL edited, `loadfile edl://... start=<pos>` and loadfile + seek.
  titles    sub-add ASS (Inter, Cyrillic), screenshot with subtitles -> PNG.
  sync      headless encode of the same EDL through mpv's own pipeline (o=, with
            the same lavfi-complex amix music bed): flash vs beep vs EDL, seam clicks.

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
    TITLE_FONT,
    TITLE_START_S,
    TITLE_TEXT,
    load_sources,
    make_music_bed,
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


def step_control(url_lead0: str, frames0: list[tl.OutFrame]) -> dict[str, Any]:
    """Headless: exact seek to every first frame of an EDL whose starts sit exactly on
    the pts; count how many land one frame late (the first frame was dropped)."""
    m = mpv.MPV(vo="null", ao="null", pause=True, hr_seek="yes", keep_open="yes")
    try:
        d = Driver(m)
        d._wait_event("playback-restart", lambda: m.loadfile(url_lead0))
        firsts = [i for i, f in enumerate(frames0) if i == 0 or frames0[i - 1].seg != f.seg]
        late = []
        for i in firsts:
            d.seek(frames0[i].t_us / tl.US)
            err = (m.time_pos or 0) * tl.US - frames0[i].t_us
            if abs(err) > 1000:
                late.append({"seg": frames0[i].seg, "src": frames0[i].src, "err_us": round(err)})
        return {
            "lead_us": 0,
            "segments": len(firsts),
            "first_frame_lost": len(late),
            "examples": late[:5],
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
    out: dict[str, Any] = {
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


def step_seek(
    d: Driver, frames: list[tl.OutFrame], n: int, rng: random.Random, playing: bool
) -> dict[str, Any]:
    m = d.m
    times, to_paint, after_restart, wrong = [], [], [], []
    picks = [rng.randrange(len(frames) - 10) for _ in range(n)]
    if playing:
        m.pause = False
    try:
        for i in picks:
            f = frames[i]
            t0, t1 = d.seek(f.t_us / tl.US)
            times.append((t1 - t0) * 1000)
            # Paused: nothing else repaints, so the first paint after the command is the
            # new frame (it lands just before playback-restart reaches Python). Playing:
            # paints never stop, so only "first paint after restart" means anything.
            tp = d.first_paint_after(t0) if not playing else None
            if tp is not None:
                to_paint.append((tp - t0) * 1000)
            ta = d.paint_after(t1)
            if ta is not None:
                after_restart.append((ta - t0) * 1000)
            if not playing:
                err = (m.time_pos or 0) * tl.US - f.t_us
                if abs(err) > 1000:
                    wrong.append({"t_us": round(f.t_us), "err_us": round(err)})
    finally:
        m.pause = True
    out: dict[str, Any] = {"n": n, "playing": playing, "to_restart_ms": tl.summary_ms(times)}
    if to_paint:
        out["to_first_paint_ms"] = tl.summary_ms(to_paint)
    if after_restart:
        out["to_paint_after_restart_ms"] = tl.summary_ms(after_restart)
    if not playing:
        out["wrong_frame"] = len(wrong)
        out["wrong_examples"] = wrong[:5]
    return out


def step_frame_steps(d: Driver, frames: list[tl.OutFrame], n: int) -> dict[str, Any]:
    """n forward steps then n back steps, starting n/2 frames before a mid seam."""
    firsts = [i for i, f in enumerate(frames) if i and frames[i - 1].seg != f.seg]
    seam = firsts[len(firsts) // 2]
    start = max(0, seam - n // 2)
    d.seek(frames[start].t_us / tl.US)
    res: dict[str, Any] = {"start_frame_index": start, "seam_index": seam}
    for back in (False, True):
        ms_list, errors = [], []
        i = start if not back else start + n
        for _ in range(n):
            ms, pos, _prev = d.step(back=back)
            i = i - 1 if back else i + 1
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
        res["back" if back else "forward"] = {
            "steps": n,
            "errors": len(errors),
            "error_examples": errors[:5],
            "ms": tl.summary_ms(ms_list),
        }
    return res


def step_rebuild(
    d: Driver,
    segments: list[tl.Segment],
    sources: dict[str, tl.Source],
    n: int,
    rng: random.Random,
    playing: bool,
) -> dict[str, Any]:
    m = d.m
    base_url = tl.edl_url(segments, sources)
    base_frames = tl.output_frames(segments, sources)
    starts = tl.seg_out_starts(segments, sources)
    rows = []
    for it in range(n):
        f = base_frames[rng.randrange(len(base_frames) - 10)]
        k = min(len(segments) - 1, f.seg + rng.choice([0, 1, 2]))  # edit at or after the playhead
        new = tl.edit_segment(segments, sources, k, 25)
        url = tl.edl_url(new, sources)
        new_frames = tl.output_frames(new, sources)
        pos_s = f.t_us / tl.US
        variant = "loadfile_start" if it % 2 == 0 else "loadfile_then_seek"
        d.seek(pos_s)
        m.pause = not playing

        def at_pos(_evt: Any, p: float = pos_s) -> bool:
            tp = m.time_pos
            return tp is not None and (playing or abs(tp - p) < 0.002)

        t0 = time.perf_counter()
        if variant == "loadfile_start":
            t1 = d._wait_event(
                "playback-restart",
                lambda u=url, p=pos_s: m.loadfile(u, "replace", start=f"{p:.6f}"),
                at_pos,
            )
        else:
            d._wait_event("file-loaded", lambda u=url: m.loadfile(u, "replace"))
            t1 = d._wait_event(
                "playback-restart",
                lambda p=pos_s: m.command("seek", f"{p:.6f}", "absolute+exact"),
                at_pos,
            )
        tp = d.paint_after(t1)
        row: dict[str, Any] = {
            "variant": variant,
            "edited_seg": k,
            "to_restart_ms": (t1 - t0) * 1000,
            "to_paint_after_restart_ms": None if tp is None else (tp - t0) * 1000,
        }
        if not playing:
            j = tl.frame_index_at(new_frames, pos_s * tl.US)
            row["pos_err_us"] = round(
                (m.time_pos or 0) * tl.US - (new_frames[j].t_us if j is not None else 0)
            )
            row["frame_ok"] = j is not None and abs(row["pos_err_us"]) <= 1000
        rows.append(row)
        m.pause = True
        d._wait_event(
            "playback-restart",
            lambda p=pos_s: m.loadfile(base_url, "replace", start=f"{p:.6f}"),
        )
    out: dict[str, Any] = {
        "n": n,
        "playing": playing,
        "edl_segments": len(segments),
        "total_s": starts[-1] / tl.US,
    }
    for variant in ("loadfile_start", "loadfile_then_seek"):
        sub = [r for r in rows if r["variant"] == variant]
        out[variant] = {
            "to_restart_ms": tl.summary_ms([r["to_restart_ms"] for r in sub]),
            "to_paint_after_restart_ms": tl.summary_ms(
                [
                    r["to_paint_after_restart_ms"]
                    for r in sub
                    if r["to_paint_after_restart_ms"] is not None
                ]
            ),
            "wrong_frame": sum(1 for r in sub if r.get("frame_ok") is False),
        }
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
    """Headless encode through mpv's playback pipeline (o=), then detectors."""
    opts: dict[str, Any] = {
        "o": str(out),
        "of": "matroska",
        "ovc": "libx264",
        "ovcopts": "preset=veryfast,crf=18",
        "oac": "pcm_s16le",
        "hwdec": hwdec,
    }
    if bed is not None:
        opts["audio_files"] = str(bed)
        opts["lavfi_complex"] = LAVFI_MUSIC
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
    enc_s = time.perf_counter() - t0
    expected = tl.expected_events(segments, sources)
    report = tl.sync_report(detect_flashes(out), detect_beeps(out), expected)
    samples = decode_mono(out)
    music = tone_level(samples, 440.0)
    starts = tl.seg_out_starts(segments, sources)
    clicks = tl.seam_clicks(samples, SAMPLE_RATE, starts[1:-1], [e["beep_us"] for e in expected])
    return {
        "music_bed": bed is not None,
        "music_440hz_level": music,
        "encode_s": round(enc_s, 2),
        "errors": errors[:10],
        **report,
        "clicks": clicks,
    }


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

    from spikes.s2_player.render_widget import disable_app_nap

    if args.mode == "render":
        from spikes.s2_player.render_widget import configure_gl

        configure_gl()
    app_nap_disabled = disable_app_nap()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    if args.mode == "render":
        from spikes.s2_player.render_widget import MpvRenderWidget

        video: Any = MpvRenderWidget(block_for_target_time=not args.no_block, **opts)
    else:
        from spikes.s2_player.wid_widget import MpvWidWidget

        video = MpvWidWidget(**opts)
    win = PlayerWindow(video)
    # macOS throttles drawing of windows covered by other apps; keep ours on top
    # and in the corner so the playback numbers measure mpv, not occlusion.
    win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    win.move(0, 0)
    win.resize(980, 640)
    win.show()
    win.raise_()
    win.activateWindow()
    result: dict[str, Any] = {}
    quitter = Quitter()
    quitter.done.connect(app.quit, Qt.ConnectionType.QueuedConnection)

    def worker() -> None:
        d = Driver(video.mpv, video)
        rng = random.Random(args.seed)
        try:
            time.sleep(0.5)  # let the window and GL context come up
            t0 = time.perf_counter()
            t1 = d._wait_event("playback-restart", lambda: video.mpv.loadfile(ctx["url"]))
            result["first_load_ms"] = (t1 - t0) * 1000
            result["video_params"] = video.mpv._get_property("video-params")
            result["hwdec_current"] = video.mpv._get_property("hwdec-current")
            result["duration_s"] = video.mpv.duration
            result["app_nap_disabled"] = app_nap_disabled
            scr = win.screen()
            result["screen"] = {"refresh_hz": scr.refreshRate(), "dpr": scr.devicePixelRatio()}
            for p in ("display-fps", "estimated-display-fps", "video-sync"):
                try:
                    result[p] = video.mpv._get_property(p)
                except Exception:  # noqa: BLE001
                    result[p] = None
            for name, fn in (
                ("playback", lambda: step_playback(d, args.play_s, ctx["starts"][1:-1])),
                ("seams", lambda: step_seams(d, ctx["frames"], ctx["refs"])),
                ("seek_paused", lambda: step_seek(d, ctx["frames"], args.seeks, rng, False)),
                (
                    "seek_playing",
                    lambda: step_seek(d, ctx["frames"], max(10, args.seeks // 3), rng, True),
                ),
                ("frame_step", lambda: step_frame_steps(d, ctx["frames"], args.steps)),
                (
                    "rebuild_paused",
                    lambda: step_rebuild(
                        d, ctx["segments"], ctx["sources"], args.rebuilds, rng, False
                    ),
                ),
                (
                    "rebuild_playing",
                    lambda: step_rebuild(
                        d, ctx["segments"], ctx["sources"], max(4, args.rebuilds // 2), rng, True
                    ),
                ),
                ("titles", lambda: step_titles(d, ctx["ass"], ctx["png"], log)),
                (
                    "playback_after",
                    lambda: step_playback(d, args.play_after_s, ctx["starts"][1:-1]),
                ),
            ):
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


def verdicts(r: dict[str, Any]) -> dict[str, Any]:
    v: dict[str, Any] = {}
    seams = r.get("seams", {})
    pb = r.get("playback", {}).get("counters_delta", {})
    if "bad_seams" in seams:
        drops = (pb.get("frame-drop-count") or 0) + (pb.get("vo-delayed-frame-count") or 0)
        v["seams_0_bad"] = tl.verdict(seams["bad_seams"], 0, unit="seams")
        v["playback_0_dropped_or_delayed"] = tl.verdict(drops if pb else None, 0, unit="frames")
    pa = r.get("playback_after", {}).get("counters_delta", {})
    if pa:
        drops = (pa.get("frame-drop-count") or 0) + (pa.get("vo-delayed-frame-count") or 0)
        v["playback_after_seeks_0_dropped_or_delayed"] = tl.verdict(drops, 0, unit="frames")
    sk = r.get("seek_paused", {}).get("to_restart_ms", {})
    v["seek_p95_le_150ms"] = tl.verdict(sk.get("p95"), tl.SEEK_P95_MS, unit="ms")
    for name in ("rebuild_paused", "rebuild_playing"):
        rb = r.get(name, {})
        worst = None
        for variant in ("loadfile_start", "loadfile_then_seek"):
            mx = rb.get(variant, {}).get("to_restart_ms", {}).get("max")
            if mx is not None:
                worst = mx if worst is None else max(worst, mx)
        v[f"{name}_max_le_300ms"] = tl.verdict(worst, tl.REBUILD_MAX_MS, unit="ms")
    fs = r.get("frame_step", {})
    if "forward" in fs:
        worst = max(fs["forward"]["ms"].get("max", 0), fs["back"]["ms"].get("max", 0))
        errs = fs["forward"]["errors"] + fs["back"]["errors"]
        v["frame_step_le_100ms_no_errors"] = tl.verdict(
            worst, tl.STEP_MAX_MS, unit="ms", ok=worst <= tl.STEP_MAX_MS and errs == 0
        )
    sy = r.get("sync", {})
    if "worst_abs_us" in sy:
        v["sync_within_1_frame"] = tl.verdict(
            sy["worst_abs_us"] / 1000,
            sy["frame_us_used"] / 1000,
            unit="ms",
            ok=sy["worst_abs_us"] <= sy["frame_us_used"] and sy["all_events_found"],
        )
        cl = sy.get("clicks", {})
        if "seams_above_rest_peak" in cl:
            v["no_seam_clicks"] = tl.verdict(cl["seams_above_rest_peak"], 0, unit="seams")
    ti = r.get("titles", {})
    if "rendered" in ti:
        v["titles_rendered"] = {
            "value": ti["changed_pixels"],
            "pass": ti["rendered"],
            "note": "tofu: check PNG by eye",
        }
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
    ap.add_argument("--skip-sync", action="store_true")
    ap.add_argument(
        "--no-block", action="store_true", help="render API: block_for_target_time=0 in paintGL"
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
    wanted: set[tuple[str, int]] = set()
    for i, f in enumerate(frames):
        if i == 0 or frames[i - 1].seg != f.seg:
            wanted |= {(f.src, f.frame), (f.src, f.frame + 1)}
            if i:
                wanted.add((frames[i - 1].src, frames[i - 1].frame))
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
    }

    result: dict[str, Any] = {
        "spike": "s2",
        "mode": args.mode,
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "edl": {
            "segments": len(segments),
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
            "seek_p95_ms": tl.SEEK_P95_MS,
            "rebuild_max_ms": tl.REBUILD_MAX_MS,
            "step_max_ms": tl.STEP_MAX_MS,
            "sync_max_frames": tl.SYNC_MAX_FRAMES,
        },
    }
    result["libmpv"] = libmpv_info()

    print("[s2] control (lead 0) ...", flush=True)
    lead = tl.CUT_LEAD_US
    tl.CUT_LEAD_US = 0
    try:
        url0, frames0 = tl.edl_url(segments, sources), tl.output_frames(segments, sources)
    finally:
        tl.CUT_LEAD_US = lead
    result["control_lead0"] = step_control(url0, frames0)

    result.update(run_gui(args, ctx))

    if not args.skip_sync:
        print("[s2] sync (encode through mpv) ...", flush=True)
        try:
            result["sync"] = step_sync(
                url, bed, segments, sources, args.work / f"sync-{name}.mkv", args.hwdec
            )
        except Exception:  # noqa: BLE001
            result["sync"] = {"error": traceback.format_exc()}

    result["verdicts"] = verdicts(result)
    path = write_result("s2", name, result)
    print(path)
    for k, val in result["verdicts"].items():
        print(f"  {k}: {val}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
