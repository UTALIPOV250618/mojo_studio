"""Rules that turn a file's signals into moments (thresholds are preliminary).

The Kyrgyz reason texts are drafts: needs native-speaker review.
"""

from dataclasses import replace

import numpy as np

from toystudio.core.format import format_duration_words
from toystudio.core.models import (
    US_PER_S,
    AudioEvent,
    AudioLabel,
    MomentRow,
    MomentType,
    QualityInterval,
    QualityLabel,
    Reason,
    SpeechRegion,
)
from toystudio.engine.analysis.quality import overall_label
from toystudio.engine.audio.events import music_share
from toystudio.engine.moments.signals import FileSignals

TOAST_MAX_PAUSE_S = 3.0
TOAST_MIN_S = 25.0
TOAST_MAX_MUSIC = 0.3
TOAST_TAIL_S = 10.0
DANCE_MIN_S = 20.0
DANCE_MOTION = 1.5  # times the file's median motion
APPLAUSE_MIN_S = 3.0
MUSIC_MIN_S = 30.0
ENTRANCE_BURST_MIN_S = 3.0
ENTRANCE_START_LATE_S = 5.0  # burst may start at most this long after the music starts
ENTRANCE_LOOKBACK_S = 60.0  # ...or up to this long before it
ENTRANCE_TAIL_S = 10.0
ENTRANCE_SCORE = 0.6
SNAP_S = 2.0
MIN_MOMENT_S = 3.0
BAD_SHARE = 0.5
BAD_PENALTY = 0.3

_BURST = (AudioLabel.APPLAUSE, AudioLabel.CHEER)


def _s(seconds: float) -> int:
    return round(seconds * US_PER_S)


def _words(us: int, lang: str) -> str:
    """'1 мин 12 с' / '25 с'; format_duration_words only knows whole minutes."""
    minutes, seconds = divmod(us // US_PER_S, 60)
    sec_unit = "с" if lang == "ru" else "сек"
    parts = []
    if minutes:
        parts.append(format_duration_words(minutes * 60 * US_PER_S, lang))
    if seconds or not minutes:
        parts.append(f"{seconds} {sec_unit}")
    return " ".join(parts)


def _row(
    sig: FileSignals, type_: MomentType, start: int, end: int, score: float, reasons: list[Reason]
) -> MomentRow:
    return MomentRow(
        id=0,
        media_id=sig.media_id,
        type=type_,
        start_us=start,
        end_us=end,
        score=min(1.0, max(0.0, score)),
        quality_label=QualityLabel.CLEAR,
        reasons=tuple(reasons[:3]),
    )


def _merged_speech(regions: list[SpeechRegion]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for r in sorted(regions, key=lambda r: r.start_us):
        if out and r.start_us - out[-1][1] <= _s(TOAST_MAX_PAUSE_S):
            out[-1][1] = max(out[-1][1], r.end_us)
        else:
            out.append([r.start_us, r.end_us])
    return [(a, b) for a, b in out]


def _toasts(sig: FileSignals) -> list[MomentRow]:
    out: list[MomentRow] = []
    for a, b in _merged_speech(sig.speech):
        length = b - a
        if length < _s(TOAST_MIN_S):
            continue
        music = music_share(sig.events, a, b)
        if music > TOAST_MAX_MUSIC:
            continue
        tail_end = b + _s(TOAST_TAIL_S)
        applause = any(
            e.label in _BURST and e.end_us > b and e.start_us < tail_end for e in sig.events
        )
        tail_music = any(
            e.label == AudioLabel.MUSIC and e.end_us > b and e.start_us < tail_end
            for e in sig.events
        )
        score = (
            0.5
            + 0.2 * (applause or tail_music)
            + 0.1 * min(1.0, length / _s(90))
            + 0.2 * (1 - music)
        )
        reasons = [
            Reason(
                "long_speech",
                f"Долгая речь без пауз — {_words(length, 'ru')}",
                f"Тыным албай узак сүйлөгөн — {_words(length, 'ky')}",
            )
        ]
        if applause:
            reasons.append(Reason("end_applause", "В конце аплодисменты", "Аягында кол чабуу"))
        elif tail_music:
            reasons.append(Reason("end_music", "В конце музыка", "Аягында музыка"))
        out.append(_row(sig, MomentType.TOAST, a, b, score, reasons))
    return out


def _music_events(sig: FileSignals) -> list[AudioEvent]:
    return [e for e in sig.events if e.label == AudioLabel.MUSIC]


def _motion_ratio(sig: FileSignals, e: AudioEvent) -> float | None:
    """Mean motion over the event relative to the file median (inf when the median is 0)."""
    m = sig.motion_per_s
    if not len(m):
        return None
    lo = e.start_us // US_PER_S
    window = m[lo : max(lo + 1, -(-e.end_us // US_PER_S))]
    if not len(window):
        return None
    mean = float(window.mean())
    median = float(np.median(m))
    if median <= 0:
        return float("inf") if mean > 0 else 0.0
    return mean / median


def _dances_and_music(sig: FileSignals) -> list[MomentRow]:
    out: list[MomentRow] = []
    for e in _music_events(sig):
        length = e.end_us - e.start_us
        ratio = _motion_ratio(sig, e)
        if ratio is not None and ratio >= DANCE_MOTION and length >= _s(DANCE_MIN_S):
            excess = min(1.0, (ratio - DANCE_MOTION) / DANCE_MOTION)
            reasons = [
                Reason(
                    "music_motion",
                    "Музыка и активное движение в кадре",
                    "Музыка жана кадрдагы активдүү кыймыл",
                ),
                Reason(
                    "duration",
                    f"Длится {_words(length, 'ru')}",
                    f"Узактыгы {_words(length, 'ky')}",
                ),
            ]
            score = 0.5 + 0.5 * excess
            out.append(_row(sig, MomentType.DANCE, e.start_us, e.end_us, score, reasons))
        elif length >= _s(MUSIC_MIN_S):
            reasons = [
                Reason(
                    "music_calm",
                    f"Музыка {_words(length, 'ru')} без активного движения",
                    f"Активдүү кыймылсыз музыка {_words(length, 'ky')}",
                )
            ]
            score = 0.3 + 0.3 * min(1.0, length / _s(120))
            out.append(_row(sig, MomentType.MUSIC, e.start_us, e.end_us, score, reasons))
    return out


def _entrances(sig: FileSignals) -> list[MomentRow]:
    out: list[MomentRow] = []
    bursts = sorted(
        (
            e
            for e in sig.events
            if e.label in _BURST and e.end_us - e.start_us >= _s(ENTRANCE_BURST_MIN_S)
        ),
        key=lambda e: e.start_us,
    )
    for m in _music_events(sig):
        for b in bursts:
            if _s(-ENTRANCE_LOOKBACK_S) <= b.start_us - m.start_us <= _s(ENTRANCE_START_LATE_S):
                start = min(m.start_us, b.start_us)
                end = max(b.end_us, m.start_us) + _s(ENTRANCE_TAIL_S)
                if sig.duration_us:
                    end = min(sig.duration_us, end)
                reasons = [
                    Reason(
                        "entrance_applause",
                        "Аплодисменты сразу после начала музыки",
                        "Музыка башталгандан кийин эле кол чабуу",
                    )
                ]
                out.append(_row(sig, MomentType.ENTRANCE, start, end, ENTRANCE_SCORE, reasons))
                break
    return out


def _applause(sig: FileSignals, taken: list[MomentRow]) -> list[MomentRow]:
    out: list[MomentRow] = []
    for e in sig.events:
        length = e.end_us - e.start_us
        if e.label not in _BURST or length < _s(APPLAUSE_MIN_S):
            continue
        mid = (e.start_us + e.end_us) // 2
        if any(t.start_us <= mid <= t.end_us for t in taken):
            continue
        reasons = [
            Reason(
                "applause",
                f"Аплодисменты {_words(length, 'ru')}",
                f"Кол чабуу {_words(length, 'ky')}",
            )
        ]
        score = 0.3 + 0.4 * min(1.0, length / _s(15))
        out.append(_row(sig, MomentType.APPLAUSE, e.start_us, e.end_us, score, reasons))
    return out


def _snap(sig: FileSignals, m: MomentRow) -> MomentRow:
    if not sig.cuts_us:
        return m

    def nearest(t: int) -> int:
        best = min(sig.cuts_us, key=lambda c: abs(c - t))
        return best if abs(best - t) <= _s(SNAP_S) else t

    start, end = max(0, nearest(m.start_us)), nearest(m.end_us)
    if sig.duration_us:
        end = min(sig.duration_us, end)
    if end - start < _s(MIN_MOMENT_S):
        # snapping would shrink it below the minimum: keep the original edge on that side
        if m.end_us - start >= _s(MIN_MOMENT_S):
            end = m.end_us
        elif end - m.start_us >= _s(MIN_MOMENT_S):
            start = m.start_us
        else:
            return m
    return replace(m, start_us=start, end_us=end)


def _merge(moments: list[MomentRow]) -> list[MomentRow]:
    out: list[MomentRow] = []
    for m in sorted(moments, key=lambda m: (m.type.value, m.start_us)):
        if out and out[-1].type == m.type and m.start_us < out[-1].end_us:
            last = out[-1]
            best = m if m.score > last.score else last
            out[-1] = replace(
                best, start_us=last.start_us, end_us=max(last.end_us, m.end_us), score=best.score
            )
        else:
            out.append(m)
    return out


def _apply_quality(sig: FileSignals, m: MomentRow) -> MomentRow:
    length = m.end_us - m.start_us
    clipped = [
        QualityInterval(
            iv.media_id, max(iv.start_us, m.start_us), min(iv.end_us, m.end_us), iv.label
        )
        for iv in sig.bad
        if iv.label is not QualityLabel.CLEAR and iv.end_us > m.start_us and iv.start_us < m.end_us
    ]
    covered = sum(iv.end_us - iv.start_us for iv in clipped)
    score = m.score - BAD_PENALTY if covered > BAD_SHARE * length else m.score
    return replace(m, quality_label=overall_label(clipped, length), score=max(0.0, score))


def find_moments(sig: FileSignals) -> list[MomentRow]:
    toasts = _toasts(sig)
    entrances = _entrances(sig)
    found = toasts + _dances_and_music(sig) + entrances + _applause(sig, toasts + entrances)
    merged = _merge([_snap(sig, m) for m in found])
    out = [_apply_quality(sig, m) for m in merged if m.end_us - m.start_us >= _s(MIN_MOMENT_S)]
    out.sort(key=lambda m: (m.start_us, m.end_us, m.type.value))
    return out
