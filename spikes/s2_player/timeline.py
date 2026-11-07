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


def seam_clicks(
    samples: np.ndarray,
    sample_rate: int,
    seams_us: list[int],
    beeps_us: list[int],
    window_us: int = 5_000,
) -> dict[str, Any]:
    """Click check on mono float samples: peak |second difference| in a window around
    every seam vs the peak elsewhere (beep windows excluded from both). A step or gap
    at a seam shows up as a large second difference; a sine music bed barely moves it.
    """
    x = np.asarray(samples, dtype=np.float64)
    d2 = np.abs(np.diff(x, 2)) if len(x) > 2 else np.zeros(0)
    if len(d2) == 0:
        return {"n_seams": 0}

    def idx(t_us: int) -> int:
        return max(0, min(len(d2), round(t_us * sample_rate / US)))

    beep_mask = np.zeros(len(d2), dtype=bool)
    for b in beeps_us:
        beep_mask[idx(b - 10_000) : idx(b + 40_000)] = True
    seam_mask = np.zeros(len(d2), dtype=bool)
    per_seam = []
    for s in seams_us:
        lo, hi = idx(s - window_us), idx(s + window_us)
        seam_mask[lo:hi] = True
        win = d2[lo:hi][~beep_mask[lo:hi]]
        per_seam.append(float(win.max()) if len(win) else 0.0)
    rest = d2[~seam_mask & ~beep_mask]
    rest_peak = float(rest.max()) if len(rest) else 0.0
    rest_p999 = float(np.quantile(rest, 0.999)) if len(rest) else 0.0
    seam_peak = max(per_seam) if per_seam else 0.0
    return {
        "n_seams": len(seams_us),
        "seam_peak_d2": seam_peak,
        "rest_peak_d2": rest_peak,
        "rest_p999_d2": rest_p999,
        "seams_above_rest_peak": sum(1 for p in per_seam if p > rest_peak),
        "worst_seams": sorted(
            ({"i": i, "t_us": seams_us[i], "d2": p} for i, p in enumerate(per_seam)),
            key=lambda r: -r["d2"],
        )[:5],
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


def summary_ms(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 2),
        "p50": round(percentile(values, 50), 2),
        "p95": round(percentile(values, 95), 2),
        "max": round(max(values), 2),
        "mean": round(sum(values) / len(values), 2),
    }


def verdict(value: float | None, threshold: float, *, unit: str, ok: bool | None = None) -> dict:
    """value <= threshold passes unless `ok` overrides; None = not measured."""
    if value is None:
        return {"value": None, "threshold": threshold, "unit": unit, "pass": None}
    passed = value <= threshold if ok is None else ok
    return {"value": value, "threshold": threshold, "unit": unit, "pass": bool(passed)}
