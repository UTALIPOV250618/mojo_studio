"""Pure helpers for S5: test EDL, frame quantisation, chunk plan, chunk keys, sync maths.

No subprocess here: everything is unit-tested in tests/spikes/test_s5_plan.py.
Times are integer microseconds unless the name says otherwise.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any

US = 1_000_000
PLAN_VERSION = "s5-1"


# --------------------------------------------------------------------------- EDL


def event_times_us(duration_us: int, first_us: int, every_us: int) -> list[int]:
    """Flash/beep instants of a synthetic source (see spikes.common.media)."""
    out = []
    t = first_us
    while t < duration_us:
        out.append(t)
        t += every_us
    return out


def _cut_ok(t_us: int, events: list[int], margin_us: int) -> bool:
    return all(abs(t_us - e) >= margin_us for e in events)


def make_test_edl(
    sources: dict[str, dict[str, Any]],
    *,
    n_segments: int = 120,
    min_s: float = 1.0,
    max_s: float = 4.0,
    seed: int = 5,
    source_duration_s: float = 60.0,
    first_event_s: float = 1.0,
    every_s: float = 2.0,
    margin_s: float = 0.1,
) -> dict[str, Any]:
    """Random EDL whose cuts stay >= margin from every flash/beep and whose every
    segment fully contains at least one event (so each flash keeps its own beep).

    ``sources`` maps a key to {"file": name, "fps": "25"}; segments pick a source at
    random. Mean length (min+max)/2 = 2.5 s gives ~5 min for 120 segments.
    """
    rng = random.Random(seed)
    dur_us = round(source_duration_s * US)
    events = event_times_us(dur_us, round(first_event_s * US), round(every_s * US))
    margin = round(margin_s * US)
    keys = sorted(sources)
    segments: list[dict[str, Any]] = []
    while len(segments) < n_segments:
        length = round(rng.uniform(min_s, max_s) * US)
        start = rng.randrange(0, dur_us - length - margin)
        end = start + length
        if not (_cut_ok(start, events, margin) and _cut_ok(end, events, margin)):
            continue
        if not any(start + margin <= e <= end - margin for e in events):
            continue
        segments.append({"src": rng.choice(keys), "in_us": start, "dur_us": length})
    return {
        "version": 1,
        "seed": seed,
        "sources": sources,
        "events": {
            "first_us": round(first_event_s * US),
            "every_us": round(every_s * US),
            "source_duration_us": dur_us,
        },
        "margin_us": margin,
        "segments": segments,
    }


# ------------------------------------------------------------------ quantisation


def frames_for(dur_us: int, fps: Fraction) -> int:
    """Whole output frames for one segment (round half up, independent per segment,
    so an edit never shifts frame counts of other segments)."""
    return math.floor(Fraction(dur_us, US) * fps + Fraction(1, 2))


def frame_time_us(frames: int, fps: Fraction) -> int:
    return round(Fraction(frames) / fps * US)


@dataclass(frozen=True)
class Piece:
    """A run of output frames taken from one source starting at ``in_us``."""

    seg: int
    src: str
    in_us: int
    frames: int


def quantise(segments: list[dict[str, Any]], fps: Fraction) -> list[Piece]:
    out = []
    for i, s in enumerate(segments):
        n = frames_for(int(s["dur_us"]), fps)
        if n > 0:
            out.append(Piece(seg=i, src=str(s["src"]), in_us=int(s["in_us"]), frames=n))
    return out


# -------------------------------------------------------------------- chunk plan


def _split_long(pieces: list[Piece], fps: Fraction, size: int, limit: int) -> list[Piece]:
    """Split pieces longer than ``limit`` frames into parts of ``size`` frames from
    the piece's own start (local: depends only on the piece)."""
    out = []
    for p in pieces:
        if p.frames <= limit:
            out.append(p)
            continue
        done = 0
        while done < p.frames:
            n = min(size, p.frames - done)
            out.append(Piece(p.seg, p.src, p.in_us + frame_time_us(done, fps), n))
            done += n
    return out


def plan_chunks(pieces: list[Piece], fps: Fraction, target_s: float) -> list[list[Piece]]:
    """Greedy plan: close a chunk once it reaches ~target_s, at a segment boundary
    (a segment longer than 1.5x target is split inside, §9 p.1).

    Simple, but a length change in one segment shifts every later boundary.
    """
    target = max(1, round(target_s * fps))
    chunks: list[list[Piece]] = []
    cur: list[Piece] = []
    cur_n = 0
    for p in _split_long(pieces, fps, target, target + target // 2):
        cur.append(p)
        cur_n += p.frames
        if cur_n >= target:
            chunks.append(cur)
            cur, cur_n = [], 0
    if cur:
        chunks.append(cur)
    return chunks


def piece_hash(p: Piece, sources: dict[str, str]) -> int:
    blob = f"{sources[p.src]}|{p.in_us}|{p.frames}".encode()
    return int(hashlib.sha256(blob).hexdigest()[:16], 16)


def plan_chunks_local(
    pieces: list[Piece], fps: Fraction, target_s: float, sources: dict[str, str]
) -> list[list[Piece]]:
    """Content-defined plan (local minimum / "winnowing"): cut after a piece whose
    content hash is the smallest among all pieces whose centre lies within
    +-target/2 of its own centre on the output timeline. Mean chunk ~target.

    The decision for a piece depends only on pieces within target/2 of it, so an
    edit can only move boundaries within ~target of the edit: 1-3 chunks re-render
    instead of every chunk after the edit. Long segments are split from their own
    start; a safety cut at 2x target bounds the chunk length.
    """
    target = max(1, round(target_s * fps))
    ps = _split_long(pieces, fps, target, target + target // 2)
    centres, t = [], 0
    for p in ps:
        centres.append(2 * t + p.frames)  # doubled to stay integral
        t += p.frames
    hashes = [piece_hash(p, sources) for p in ps]
    cut = [False] * len(ps)
    lo = 0
    for i in range(len(ps)):
        while centres[i] - centres[lo] > target:  # doubled units: +-target/2
            lo += 1
        hi = i
        while hi + 1 < len(ps) and centres[hi + 1] - centres[i] <= target:
            hi += 1
        cut[i] = all(hashes[i] < hashes[j] for j in range(lo, hi + 1) if j != i)
    chunks: list[list[Piece]] = []
    cur: list[Piece] = []
    cur_n = 0
    for i, p in enumerate(ps):
        if cur and cur_n + p.frames > 2 * target:
            chunks.append(cur)
            cur, cur_n = [], 0
        cur.append(p)
        cur_n += p.frames
        if cut[i]:
            chunks.append(cur)
            cur, cur_n = [], 0
    if cur:
        chunks.append(cur)
    return chunks


def chunk_key(
    chunk: list[Piece], sources: dict[str, str], preset: dict[str, Any], encoder: list[str]
) -> str:
    """Hash of everything that determines the chunk's bytes (§9 p.2).

    ``sources`` maps the source key to a stable identity (path + size + mtime).
    The segment index is not part of the key: moving identical material keeps it.
    """
    payload = {
        "v": PLAN_VERSION,
        "pieces": [[sources[p.src], p.in_us, p.frames] for p in chunk],
        "preset": preset,
        "encoder": encoder,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]


# --------------------------------------------------------------- expected events


def expected_events(
    pieces: list[Piece], edl: dict[str, Any], fps: Fraction
) -> list[dict[str, int]]:
    """Where each source event should land on the output timeline.

    beep_us: exact (audio is sample-accurate). flash_us: the output frame tick that
    shows the source flash frame (flash = nearest source frame, then nearest output
    tick). Pieces are contiguous from 0.
    """
    ev = edl["events"]
    events = event_times_us(ev["source_duration_us"], ev["first_us"], ev["every_us"])
    out = []
    start_frames = 0
    for p in pieces:
        src_fps = Fraction(edl["sources"][p.src]["fps"])
        end_us = p.in_us + frame_time_us(p.frames, fps)
        for e in events:
            if p.in_us <= e < end_us:
                src_frame = math.floor(Fraction(e, US) * src_fps + Fraction(1, 2))
                rel = Fraction(src_frame) / src_fps - Fraction(p.in_us, US)
                tick = start_frames + math.floor(rel * fps + Fraction(1, 2))
                out.append(
                    {
                        "beep_us": frame_time_us(start_frames, fps) + (e - p.in_us),
                        "flash_us": frame_time_us(tick, fps),
                    }
                )
        start_frames += p.frames
    return out


# ------------------------------------------------------------------- sync maths


def dedupe_runs(times: list[int], gap_us: int) -> list[int]:
    """Keep the first of consecutive detections closer than gap_us (a flash frame
    duplicated by fps conversion shows up on two or three output frames)."""
    out: list[int] = []
    last: int | None = None
    for t in sorted(times):
        if last is None or t - last >= gap_us:
            out.append(t)
        last = t
    return out


def pair_nearest(flashes: list[int], beeps: list[int], max_us: int) -> list[tuple[int, int]]:
    """(flash, beep) for each flash whose nearest beep is within max_us."""
    pairs = []
    if not beeps:
        return pairs
    sb = sorted(beeps)
    for f in flashes:
        b = min(sb, key=lambda x: abs(x - f))
        if abs(b - f) <= max_us:
            pairs.append((f, b))
    return pairs


@dataclass(frozen=True)
class Fit:
    slope_ms_per_min: float
    stderr_ms_per_min: float
    intercept_ms: float


def drift_fit(t_us: list[int], delta_us: list[int]) -> Fit:
    """Least squares delta(t); slope in ms per minute with its standard error."""
    n = len(t_us)
    if n < 3:
        return Fit(0.0, float("inf"), 0.0)
    x = [t / 60 / US for t in t_us]  # minutes
    y = [d / 1000 for d in delta_us]  # ms
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return Fit(0.0, float("inf"), my)
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / sxx
    icpt = my - slope * mx
    resid = sum((b - (icpt + slope * a)) ** 2 for a, b in zip(x, y, strict=True))
    stderr = math.sqrt(resid / (n - 2) / sxx)
    return Fit(slope, stderr, icpt)


def sync_stats(pairs: list[tuple[int, int]]) -> dict[str, Any]:
    deltas = [b - f for f, b in pairs]
    if not deltas:
        return {"n_pairs": 0}
    fit = drift_fit([f for f, _ in pairs], deltas)
    return {
        "n_pairs": len(deltas),
        "max_abs_delta_us": max(abs(d) for d in deltas),
        "mean_delta_us": round(sum(deltas) / len(deltas)),
        "min_delta_us": min(deltas),
        "max_delta_us": max(deltas),
        "drift": asdict(fit),
    }


def errors_vs_expected(
    detected: list[int], expected: list[int], max_us: int
) -> list[tuple[int, int]]:
    """(time, detected - nearest expected) for detections with one within max_us."""
    if not expected:
        return []
    se = sorted(expected)
    out = []
    for d in detected:
        e = min(se, key=lambda x: abs(x - d))
        if abs(d - e) <= max_us:
            out.append((d, d - e))
    return out
