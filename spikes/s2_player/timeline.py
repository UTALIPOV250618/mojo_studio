"""S2 pure logic: test segments, the inline `edl://` string, the output frame timeline,
expected flash/beep positions, frame and audio comparisons, stats and verdicts.

Times are integer microseconds unless a name says otherwise. Segment cuts sit on
source frame boundaries (the product quantises edits to frames), so "the first frame
of a segment" is unambiguous.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import numpy as np

from spikes.s5_export.plan import event_times_us

US = 1_000_000

# Thresholds from the S2 brief (§8.2 / §1.2).
SEAM_MAD_MAX = 2.0  # mean abs pixel diff (0-255) vs the ffmpeg-extracted frame
SEEK_P95_MS = 150.0
REBUILD_MAX_MS = 300.0
STEP_MAX_MS = 100.0
SYNC_MAX_FRAMES = 1

EVENT_FIRST_US = 1_000_000  # spikes.common.media: flash + beep at 1.0 s + k * 2 s
EVENT_EVERY_US = 2_000_000
CUT_MARGIN_US = 60_000  # no cut within 60 ms of a flash/beep (a beep lasts 20 ms)
EOF_MARGIN_S = 1
# Seam audio: peak |played - ideal hard cut| allowed around a seam. For scale: the
# tones are 0.3 full scale at 310-690 Hz, so the cut misplaced by ONE sample gives
# ~0.027 and a 1 ms gap ~0.3; AAC re-decoding noise mid-segment is ~4e-5.
SEAM_AUDIO_MAX = 0.01
PAINTED_MAD_MAX = 2.5  # painted (GPU-scaled, read back) vs reference frame
CUT_LEAD_US = 100  # EDL segment start this much before the first frame (see seg_times)


@dataclass(frozen=True)
class Source:
    name: str
    path: str
    fps: Fraction
    frames: int


@dataclass(frozen=True)
class Segment:
    src: str
    in_frame: int
    frames: int


def pts_floor_us(frame: int, fps: Fraction) -> int:
    """Frame time floored to a microsecond: never later than the real pts, so a cut
    at this time keeps the frame instead of dropping it by a rounding hair."""
    return (frame * US * fps.denominator) // fps.numerator


def pts_us(frame: int, fps: Fraction) -> Fraction:
    return Fraction(frame * US) / fps


def seg_times(
    seg: Segment, sources: dict[str, Source], lead_us: int | None = None
) -> tuple[int, int]:
    """(start_us, length_us) as written into the EDL.

    The start sits lead_us before the first frame's pts: a start exactly on the pts
    loses that frame to float rounding in mpv's timeline in about half the seams.
    The length is the exact frame span, so the last frame and the cut stay put.
    """
    lead = CUT_LEAD_US if lead_us is None else lead_us
    fps = sources[seg.src].fps
    start = pts_floor_us(seg.in_frame, fps)
    end = pts_floor_us(seg.in_frame + seg.frames, fps)
    return start - lead, end - start


def _cut_ok(t_us: int, events: list[int]) -> bool:
    i = bisect.bisect_left(events, t_us - CUT_MARGIN_US)
    return i >= len(events) or events[i] > t_us + CUT_MARGIN_US


def make_segments(
    sources: dict[str, Source],
    n: int,
    *,
    seed: int,
    min_s: float = 0.5,
    max_s: float = 3.0,
) -> list[Segment]:
    """n random segments of min_s..max_s (whole source frames) from random sources.

    Cuts avoid flash/beep instants and the last second of a source; consecutive
    segments never continue each other, so every seam is a real cut.
    """
    rng = random.Random(seed)
    names = sorted(sources)
    out: list[Segment] = []
    while len(out) < n:
        src = sources[rng.choice(names)]
        frames = max(1, round(rng.uniform(min_s, max_s) * src.fps))
        last_in = src.frames - frames - math.ceil(EOF_MARGIN_S * src.fps)
        if last_in <= 0:
            continue
        in_frame = rng.randint(0, last_in)
        prev = out[-1] if out else None
        if prev and prev.src == src.name and prev.in_frame + prev.frames == in_frame:
            continue
        duration_us = round(pts_us(src.frames, src.fps))
        events = event_times_us(duration_us, EVENT_FIRST_US, EVENT_EVERY_US)
        start = pts_floor_us(in_frame, src.fps)
        end = pts_floor_us(in_frame + frames, src.fps)
        if in_frame == 0 or not (_cut_ok(start, events) and _cut_ok(end, events)):
            continue
        out.append(Segment(src.name, in_frame, frames))
    return out


def fmt_us(t_us: int) -> str:
    sign = "-" if t_us < 0 else ""
    t = abs(t_us)
    return f"{sign}{t // US}.{t % US:06d}"


def edl_url(segments: list[Segment], sources: dict[str, Source]) -> str:
    """Inline mpv EDL. Paths use the `%<bytes>%path` form, so commas, spaces and
    Cyrillic in paths are safe (the length is in UTF-8 bytes)."""
    parts = []
    for seg in segments:
        path = sources[seg.src].path
        start, length = seg_times(seg, sources)
        parts.append(f"%{len(path.encode('utf-8'))}%{path},{fmt_us(start)},{fmt_us(length)}")
    return "edl://" + ";".join(parts)


def seg_out_starts(segments: list[Segment], sources: dict[str, Source]) -> list[int]:
    out, t = [], 0
    for seg in segments:
        out.append(t)
        t += seg_times(seg, sources)[1]
    out.append(t)  # total
    return out


@dataclass(frozen=True)
class OutFrame:
    t_us: float  # output (EDL timeline) time of the frame
    seg: int
    src: str
    frame: int  # frame index in the source


def output_frames(segments: list[Segment], sources: dict[str, Source]) -> list[OutFrame]:
    starts = seg_out_starts(segments, sources)
    out = []
    for k, seg in enumerate(segments):
        fps = sources[seg.src].fps
        start, _ = seg_times(seg, sources)
        for i in range(seg.frames):
            f = seg.in_frame + i
            out.append(OutFrame(float(starts[k] + pts_us(f, fps) - start), k, seg.src, f))
    return out


def frame_index_at(frames: list[OutFrame], t_us: float, tol_us: float = 1000.0) -> int | None:
    """Index of the frame whose output time is within tol_us of t_us, else None."""
    times = [f.t_us for f in frames]
    i = bisect.bisect_left(times, t_us - tol_us)
    if i < len(frames) and abs(frames[i].t_us - t_us) <= tol_us:
        return i
    return None


def edit_segment(
    segments: list[Segment], sources: dict[str, Source], index: int, shift_frames: int
) -> list[Segment]:
    """Copy of the EDL with segment `index` moved by shift_frames in its source
    (same length); the shift flips sign if it would leave the source."""
    out = list(segments)
    seg = out[index]
    src = sources[seg.src]
    limit = src.frames - seg.frames - 1
    new_in = seg.in_frame + shift_frames
    if not 0 <= new_in <= limit:
        new_in = seg.in_frame - shift_frames
    out[index] = Segment(seg.src, max(0, min(limit, new_in)), seg.frames)
    return out


def edit_length(
    segments: list[Segment], sources: dict[str, Source], index: int, delta_frames: int
) -> list[Segment]:
    """Copy of the EDL with segment `index` made delta_frames longer (at its tail if
    the source allows, else at its head)."""
    out = list(segments)
    seg = out[index]
    src = sources[seg.src]
    if seg.in_frame + seg.frames + delta_frames <= src.frames - 1:
        out[index] = Segment(seg.src, seg.in_frame, seg.frames + delta_frames)
    else:
        out[index] = Segment(seg.src, seg.in_frame - delta_frames, seg.frames + delta_frames)
    return out


def map_position(old: list[OutFrame], new: list[OutFrame], pos_us: float) -> float:
    """Playhead after an edit: the new time of the same (segment, source frame) that
    was shown at pos_us; unchanged if that frame is no longer in the timeline."""
    times = [f.t_us for f in old]
    i = max(0, bisect.bisect_right(times, pos_us + 1000) - 1)
    key = (old[i].seg, old[i].src, old[i].frame)
    for f in new:
        if (f.seg, f.src, f.frame) == key:
            return f.t_us + (pos_us - old[i].t_us)
    return pos_us


# ---------------------------------------------------------------- flash / beep


def flash_frame(event_us: int, fps: Fraction) -> int:
    """Source frame that carries the flash for an event (generate_clip: nearest frame)."""
    return math.floor(Fraction(event_us, US) * fps + Fraction(1, 2))


def expected_events(segments: list[Segment], sources: dict[str, Source]) -> list[dict[str, int]]:
    """Output times of every flash and beep that the EDL keeps."""
    starts = seg_out_starts(segments, sources)
    out = []
    for k, seg in enumerate(segments):
        src = sources[seg.src]
        start, length = seg_times(seg, sources)
        duration_us = round(pts_us(src.frames, src.fps))
        for e in event_times_us(duration_us, EVENT_FIRST_US, EVENT_EVERY_US):
            if start <= e < start + length:
                ff = flash_frame(e, src.fps)
                out.append(
                    {
                        "seg": k,
                        "beep_us": starts[k] + e - start,
                        "flash_us": round(starts[k] + pts_us(ff, src.fps) - start),
                        "frame_us": round(pts_us(1, src.fps)),
                    }
                )
    return out


def sync_report(
    flashes: list[int], beeps: list[int], expected: list[dict[str, int]]
) -> dict[str, Any]:
    """Flash and beep of every kept event in the played-out stream vs the EDL.

    Per event: flash error, beep error (detected - expected) and the beep-flash
    shift (measured beep - flash minus the same in the source: in a 29.97 source the
    flash frame is up to half a frame off the beep by construction). The verdict
    bound is the shortest frame of the set (20 ms at 50 fps); raw beep - flash is
    also checked against each event's own source frame.
    """
    frame_us = min(e["frame_us"] for e in expected) if expected else 20_000
    max_us = 200_000
    rows = []
    for e in expected:
        f = _nearest(flashes, e["flash_us"], max_us)
        b = _nearest(beeps, e["beep_us"], max_us)
        row: dict[str, Any] = {
            "seg": e["seg"],
            "beep_us": e["beep_us"],
            "found": f is not None and b is not None,
        }
        if f is not None:
            row["flash_err_us"] = f - e["flash_us"]
        if b is not None:
            row["beep_err_us"] = b - e["beep_us"]
        if f is not None and b is not None:
            row["beep_minus_flash_us"] = b - f
            row["shift_us"] = (b - f) - (e["beep_us"] - e["flash_us"])
            row["raw_ok"] = abs(b - f) <= e["frame_us"]
        rows.append(row)

    def col(key: str) -> list[int]:
        return [r[key] for r in rows if key in r]

    worst = max([0] + [abs(x) for k in ("flash_err_us", "beep_err_us", "shift_us") for x in col(k)])
    return {
        "expected_events": len(expected),
        "flashes_detected": len(flashes),
        "beeps_detected": len(beeps),
        "events_found": sum(r["found"] for r in rows),
        "all_events_found": all(r["found"] for r in rows),
        "flash_vs_edl": _err_summary(col("flash_err_us")),
        "beep_vs_edl": _err_summary(col("beep_err_us")),
        "shift": _err_summary(col("shift_us")),
        "beep_minus_flash": _err_summary(col("beep_minus_flash_us")),
        "raw_within_own_frame": all(r.get("raw_ok", False) for r in rows),
        "frame_us_used": frame_us,
        "worst_abs_us": worst,
        "extra_flashes": len(flashes) - len(col("flash_err_us")),
        "extra_beeps": len(beeps) - len(col("beep_err_us")),
    }


def _nearest(values: list[int], target: int, max_us: int) -> int | None:
    if not values:
        return None
    v = min(values, key=lambda x: abs(x - target))
    return v if abs(v - target) <= max_us else None


def _err_summary(d: list[int]) -> dict[str, Any]:
    if not d:
        return {"n": 0}
    return {"n": len(d), "min_us": min(d), "max_us": max(d), "mean_us": round(sum(d) / len(d))}


# ---------------------------------------------------------------- frames / audio


def mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Mean abs difference of two HxWx4 bgr0 (or HxWx3) images over B, G, R."""
    if a.shape[:2] != b.shape[:2]:
        raise ValueError(f"size mismatch {a.shape} vs {b.shape}")
    x = a[..., :3].astype(np.int16)
    y = b[..., :3].astype(np.int16)
    return float(np.abs(x - y).mean())


def bgr0_from_raw(data: bytes, w: int, h: int, stride: int) -> np.ndarray:
    """mpv screenshot-raw (bgr0, rows may be padded) -> HxWx4 uint8."""
    arr = np.frombuffer(data, dtype=np.uint8)[: stride * h].reshape(h, stride)
    return arr[:, : w * 4].reshape(h, w, 4)


def seam_residuals(
    out: np.ndarray,
    src_audio: dict[str, np.ndarray],
    segments: list[Segment],
    sources: dict[str, Source],
    sample_rate: int,
    half_us: int = 10_000,
    max_lag: int = 48,
) -> dict[str, Any]:
    """Audio at every seam vs the ideal hard cut built from the sources.

    For seam k the ideal is the last `half` samples of segment k-1 followed by the
    first `half` samples of segment k, both taken from the continuously decoded
    source audio at the EDL's own start/length. The residual |out - ideal| (best
    of +-max_lag samples of alignment) catches gaps, overlaps, misplaced cuts and
    decoder warm-up garbage. The same is done mid-segment (no cut) as a baseline,
    so both columns go through the identical alignment and comparison. A best lag
    on the edge of the search (+-1 ms) means the cut is misplaced: counted as bad.
    """
    n = round(half_us * sample_rate / US)
    starts = seg_out_starts(segments, sources)

    def idx(t_us: float) -> int:
        return round(t_us * sample_rate / US)

    def residual(ideal: np.ndarray, at: int) -> tuple[float, int]:
        best = (math.inf, 0)
        for lag in range(-max_lag, max_lag + 1):
            lo = at - n + lag
            if lo < 0 or lo + 2 * n > len(out):
                continue
            peak = float(np.abs(out[lo : lo + 2 * n] - ideal).max())
            best = min(best, (peak, lag))
        return best

    seams, base = [], []
    for k, seg in enumerate(segments):
        start, length = seg_times(seg, sources)
        a = src_audio[seg.src]
        s0, s1 = idx(start), idx(start + length)
        if s1 - s0 > 2 * n:  # baseline: middle of the segment, no cut
            mid = (s0 + s1) // 2
            peak, lag = residual(a[mid - n : mid + n], idx(starts[k]) + (mid - s0))
            base.append({"seg": k, "peak": peak, "lag": lag})
        if k == 0:
            continue
        prev = segments[k - 1]
        p_start, p_len = seg_times(prev, sources)
        pa = src_audio[prev.src]
        p_end = idx(p_start + p_len)
        ideal = np.concatenate([pa[p_end - n : p_end], a[s0 : s0 + n]])
        if len(ideal) != 2 * n:
            continue
        peak, lag = residual(ideal, idx(starts[k]))
        jump = float(abs(a[s0] - pa[p_end - 1]))  # the cut's own waveform step
        seams.append({"seam": k, "peak": peak, "lag": lag, "cut_step": jump})
    base_peaks = [b["peak"] for b in base]
    seam_peaks = [s["peak"] for s in seams]
    limit = SEAM_AUDIO_MAX
    return {
        "n_seams": len(seams),
        "n_baseline": len(base),
        "baseline_peak": summary_ms(base_peaks, 5),
        "seam_peak": summary_ms(seam_peaks, 5),
        "cut_step": summary_ms([s["cut_step"] for s in seams], 4),
        "limit": limit,
        "seams_above_limit": sum(1 for p in seam_peaks if p > limit),
        "lag_at_search_edge": sum(1 for r in seams + base if abs(r["lag"]) >= max_lag),
        "lags": sorted({s["lag"] for s in seams} | {b["lag"] for b in base}),
        "worst_seams": sorted(seams, key=lambda r: -r["peak"])[:5],
    }


# ---------------------------------------------------------------- stats / verdicts


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (numpy's default), q in 0..100."""
    if not values:
        raise ValueError("no values")
    s = sorted(values)
    pos = (len(s) - 1) * q / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def summary_ms(values: list[float], digits: int = 2) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), digits),
        "p50": round(percentile(values, 50), digits),
        "p95": round(percentile(values, 95), digits),
        "max": round(max(values), digits),
        "mean": round(sum(values) / len(values), digits),
    }


def verdict(value: float | None, threshold: float, *, unit: str, ok: bool | None = None) -> dict:
    """value <= threshold passes unless `ok` overrides; None = not measured."""
    if value is None:
        return {"value": None, "threshold": threshold, "unit": unit, "pass": None}
    passed = value <= threshold if ok is None else ok
    return {"value": value, "threshold": threshold, "unit": unit, "pass": bool(passed)}


# ---------------------------------------------------------------- painted frames


def video_rect(osd: dict[str, Any], fbo_w: int, fbo_h: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the video inside the FBO from mpv's `osd-dimensions`
    (margins in OSD pixels, OSD size = FBO size for the render API)."""
    sx = fbo_w / osd["w"] if osd.get("w") else 1.0
    sy = fbo_h / osd["h"] if osd.get("h") else 1.0
    x0, x1 = round(osd["ml"] * sx), fbo_w - round(osd["mr"] * sx)
    y0, y1 = round(osd["mt"] * sy), fbo_h - round(osd["mb"] * sy)
    return x0, y0, x1 - x0, y1 - y0


def resample_area(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Box-filter img (HxWxC) to h x w: each output pixel is the mean of the input
    pixels whose centres fall in its cell. Exact for integer factors."""
    src_h, src_w = img.shape[:2]
    if src_h % h == 0 and src_w % w == 0:
        fy, fx = src_h // h, src_w // w
        return img.reshape(h, fy, w, fx, -1).mean(axis=(1, 3), dtype=np.float64)
    ys = np.floor((np.arange(src_h) + 0.5) * h / src_h).astype(np.int64)
    xs = np.floor((np.arange(src_w) + 0.5) * w / src_w).astype(np.int64)
    acc = np.zeros((h, w, img.shape[2]), dtype=np.float64)
    cnt = np.zeros((h, w, 1), dtype=np.float64)
    np.add.at(acc, (ys[:, None], xs[None, :]), img.astype(np.float64))
    np.add.at(cnt, (ys[:, None], xs[None, :]), 1.0)
    return acc / np.maximum(cnt, 1.0)


def painted_small(rgba: np.ndarray, rect: tuple[int, int, int, int], w: int, h: int) -> np.ndarray:
    """The painted video rect of an RGBA FBO read-back, box-filtered to w x h RGB."""
    x, y, rw, rh = rect
    return resample_area(rgba[y : y + rh, x : x + rw, :3], w, h)


def small_mad(small_rgb: np.ndarray, ref_bgr0: np.ndarray) -> float:
    """Mean abs diff between painted_small() output and an ffmpeg bgr0 reference."""
    ref_rgb = ref_bgr0[..., 2::-1].astype(np.float64)
    return float(np.abs(small_rgb - ref_rgb).mean())
