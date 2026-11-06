"""Editing commands: pure functions Timeline -> Timeline. Changed segments get manual=True."""

from dataclasses import replace
from typing import Literal

from toystudio.core.models import AudioMode, Track
from toystudio.core.timeline import MIN_SEGMENT_US, Segment, Timeline


def _with_track(t: Timeline, track: Track, segs: tuple[Segment, ...]) -> Timeline:
    if track is Track.VIDEO:
        return replace(t, video=segs)
    if track is Track.MUSIC:
        return replace(t, music=segs)
    return replace(t, titles=segs)


def _update(t: Timeline, seg_id: str, **changes: object) -> Timeline:
    track, i = t.index_of(seg_id)
    segs = t.track_segments(track)
    new = replace(segs[i], manual=True, **changes)  # type: ignore[arg-type]
    return _with_track(t, track, (*segs[:i], new, *segs[i + 1 :]))


def move(t: Timeline, seg_id: str, to_index: int) -> Timeline:
    track, i = t.index_of(seg_id)
    segs = list(t.track_segments(track))
    if not 0 <= to_index < len(segs):
        raise ValueError(f"to_index {to_index} out of range for {len(segs)} segments")
    seg = replace(segs.pop(i), manual=True)
    segs.insert(to_index, seg)
    return _with_track(t, track, tuple(segs))


def trim(t: Timeline, seg_id: str, edge: Literal["in", "out"], new_src: int) -> Timeline:
    s = t.segment(seg_id)
    if edge == "in":
        value = max(s.bound_in, min(new_src, s.src_out - MIN_SEGMENT_US))
        return _update(t, seg_id, src_in=value)
    value = min(s.bound_out, max(new_src, s.src_in + MIN_SEGMENT_US))
    return _update(t, seg_id, src_out=value)


def delete(t: Timeline, seg_id: str) -> Timeline:
    track, i = t.index_of(seg_id)
    segs = t.track_segments(track)
    out = _with_track(t, track, (*segs[:i], *segs[i + 1 :]))
    if track is Track.VIDEO:
        out = replace(out, titles=tuple(x for x in out.titles if x.anchor_id != seg_id))
    return out


def split(t: Timeline, seg_id: str, at_timeline: int, new_id: str) -> Timeline:
    track, i = t.index_of(seg_id)
    segs = t.track_segments(track)
    s = segs[i]
    offset = at_timeline - t.starts(track)[i]
    if offset < MIN_SEGMENT_US or s.duration - offset < MIN_SEGMENT_US:
        raise ValueError("split point is too close to a segment edge")
    cut = s.src_in + offset
    first = replace(s, src_out=cut, manual=True)
    second = replace(s, id=new_id, src_in=cut, manual=True)
    return _with_track(t, track, (*segs[:i], first, second, *segs[i + 1 :]))


def set_locked(t: Timeline, seg_id: str, locked: bool) -> Timeline:
    return _update(t, seg_id, locked=locked)


def set_audio_mode(t: Timeline, seg_id: str, mode: AudioMode) -> Timeline:
    return _update(t, seg_id, audio_mode=mode)


def duration_hint(t: Timeline, target_us: int) -> tuple[int, int] | None:
    """(actual, target) when the video deviates from the target by more than 10%."""
    actual = t.duration()
    if abs(actual - target_us) * 10 > target_us:
        return actual, target_us
    return None
