from datetime import date

import numpy as np

from toystudio.core.models import (
    US_PER_S,
    AudioEvent,
    AudioLabel,
    MediaKind,
    MomentType,
    QualityInterval,
    QualityLabel,
    SpeechRegion,
    ToiKind,
)
from toystudio.engine.moments.rules import find_moments
from toystudio.engine.moments.signals import FileSignals, load_signals, motion_per_second
from toystudio.storage.project_store import ProjectStore

S = US_PER_S
DURATION = 600 * S


def ev(label, a, b, score=0.8):
    return AudioEvent(1, round(a * S), round(b * S), label, score)


def sp(a, b):
    return SpeechRegion(round(a * S), round(b * S), 0.9)


def sig(speech=(), events=(), motion=None, cuts=(), bad=()):
    return FileSignals(
        media_id=1,
        duration_us=DURATION,
        speech=list(speech),
        events=list(events),
        motion_per_s=np.asarray(motion if motion is not None else [], dtype=float),
        cuts_us=list(cuts),
        bad=list(bad),
    )


def types(moments):
    return [m.type for m in moments]


def test_toast_found_for_long_speech_ending_in_applause():
    # 72 s of speech with a 2 s pause inside, applause right after
    s = sig(
        speech=[sp(100, 150), sp(152, 172)],
        events=[ev(AudioLabel.APPLAUSE, 173, 180)],
    )
    toasts = [m for m in find_moments(s) if m.type is MomentType.TOAST]
    assert len(toasts) == 1
    t = toasts[0]
    assert (t.start_us, t.end_us) == (100 * S, 172 * S)
    assert t.id == 0 and t.group_id is None and t.preview is None
    assert t.score > 0.8
    assert [r.code for r in t.reasons] == ["long_speech", "end_applause"]
    assert t.reasons[0].ru == "Долгая речь без пауз — 1 мин 12 с"
    assert t.reasons[0].ky
    assert t.reasons[1].ru == "В конце аплодисменты"


def test_toast_rejected_with_music_under_speech():
    s = sig(speech=[sp(100, 140)], events=[ev(AudioLabel.MUSIC, 100, 140)])
    assert MomentType.TOAST not in types(find_moments(s))
    # too short, or long pause in the middle: also no toast
    assert find_moments(sig(speech=[sp(100, 120)])) == []
    assert find_moments(sig(speech=[sp(100, 115), sp(120, 135)])) == []


def test_dance_needs_motion():
    music = [ev(AudioLabel.MUSIC, 200, 240)]
    motion = np.ones(600)
    motion[200:240] = 3.0
    dance = find_moments(sig(events=music, motion=motion))
    assert types(dance) == [MomentType.DANCE]
    assert (dance[0].start_us, dance[0].end_us) == (200 * S, 240 * S)
    assert dance[0].score > 0.5
    # same music over flat motion is not a dance
    flat = find_moments(sig(events=music, motion=np.ones(600)))
    assert MomentType.DANCE not in types(flat)
    # music shorter than 20 s is not a dance even with motion
    short = [ev(AudioLabel.MUSIC, 200, 215)]
    assert find_moments(sig(events=short, motion=motion)) == []


def test_entrance_applause_after_music_start():
    s = sig(events=[ev(AudioLabel.MUSIC, 300, 360), ev(AudioLabel.APPLAUSE, 303, 310)])
    found = find_moments(s)
    entrance = [m for m in found if m.type is MomentType.ENTRANCE]
    assert len(entrance) == 1
    assert (entrance[0].start_us, entrance[0].end_us) == (300 * S, 320 * S)
    assert entrance[0].score == 0.6
    assert MomentType.APPLAUSE not in types(found)  # inside the entrance
    # applause 30 s into the music is not an entrance
    late = sig(events=[ev(AudioLabel.MUSIC, 300, 360), ev(AudioLabel.APPLAUSE, 330, 336)])
    assert MomentType.ENTRANCE not in types(find_moments(late))
    # burst shorter than 3 s is not an entrance
    brief = sig(events=[ev(AudioLabel.MUSIC, 300, 360), ev(AudioLabel.CHEER, 302, 304)])
    assert MomentType.ENTRANCE not in types(find_moments(brief))


def test_applause_not_inside_toast():
    speech = [sp(100, 140)]
    inside = sig(speech=speech, events=[ev(AudioLabel.APPLAUSE, 136, 146)])  # midpoint 141 > end
    # midpoint inside the toast: dropped
    mid_inside = sig(speech=speech, events=[ev(AudioLabel.APPLAUSE, 120, 126)])
    assert types(find_moments(mid_inside)) == [MomentType.TOAST]
    # a separate applause far from the toast stays
    apart = sig(speech=speech, events=[ev(AudioLabel.APPLAUSE, 400, 410)])
    assert set(types(find_moments(apart))) == {MomentType.TOAST, MomentType.APPLAUSE}
    # midpoint after the toast end: kept as applause
    assert MomentType.APPLAUSE in types(find_moments(inside))
    # too short
    assert find_moments(sig(events=[ev(AudioLabel.APPLAUSE, 400, 402)])) == []


def test_music_without_dance():
    music = [ev(AudioLabel.MUSIC, 100, 140)]
    found = find_moments(sig(events=music, motion=np.ones(600)))
    assert types(found) == [MomentType.MUSIC]
    # with high motion it is a dance, not plain music
    motion = np.ones(600)
    motion[100:140] = 4.0
    assert types(find_moments(sig(events=music, motion=motion))) == [MomentType.DANCE]
    # shorter than 30 s: nothing
    assert find_moments(sig(events=[ev(AudioLabel.MUSIC, 100, 125)], motion=np.ones(600))) == []


def test_boundaries_snap_to_scene_cut():
    music = [ev(AudioLabel.MUSIC, 100, 140)]
    cuts = [int(98.5 * S), int(141.5 * S)]
    m = find_moments(sig(events=music, motion=np.ones(600), cuts=cuts))[0]
    assert (m.start_us, m.end_us) == tuple(cuts)
    # a cut 5 s away is ignored
    far = find_moments(sig(events=music, motion=np.ones(600), cuts=[95 * S, 145 * S]))[0]
    assert (far.start_us, far.end_us) == (100 * S, 140 * S)
    # snapping never shrinks below 3 s
    tiny = sig(events=[ev(AudioLabel.APPLAUSE, 100, 104)], cuts=[102 * S, int(101.9 * S)])
    out = find_moments(tiny)
    assert out and out[0].end_us - out[0].start_us >= 3 * S


def test_bad_frames_lower_score():
    music = [ev(AudioLabel.MUSIC, 100, 140)]
    base = find_moments(sig(events=music, motion=np.ones(600)))[0]
    assert base.quality_label is QualityLabel.CLEAR
    bad = [QualityInterval(1, 100 * S, 130 * S, QualityLabel.SHAKY)]
    worse = find_moments(sig(events=music, motion=np.ones(600), bad=bad))[0]
    assert worse.quality_label is QualityLabel.SHAKY
    assert abs(worse.score - (base.score - 0.3)) < 1e-9
    # bad frames over 40 % of the moment: label set, but no penalty
    some = [QualityInterval(1, 100 * S, 116 * S, QualityLabel.DARK)]
    mild = find_moments(sig(events=music, motion=np.ones(600), bad=some))[0]
    assert mild.quality_label is QualityLabel.DARK and mild.score == base.score


def test_no_audio_signals_give_no_moments():
    assert find_moments(sig(motion=np.full(600, 5.0), cuts=[10 * S, 20 * S])) == []


def test_same_type_overlaps_merge_with_max_score():
    events = [
        ev(AudioLabel.APPLAUSE, 100, 105),
        ev(AudioLabel.APPLAUSE, 103, 125),
    ]
    out = find_moments(sig(events=events))
    assert len(out) == 1
    assert (out[0].start_us, out[0].end_us) == (100 * S, 125 * S)
    assert out[0].score == 0.7  # the longer burst, 22 s capped at 15 s


def test_motion_per_second_and_load_signals(tmp_path):
    assert list(motion_per_second(np.array([1, 1, 1, 1, 3, 3]))) == [1.0, 3.0]
    store = ProjectStore.create(tmp_path / "p", "Той", ToiKind.WEDDING, date(2026, 10, 12))
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    mid = store.add_media(f, MediaKind.VIDEO, 1, copied=False)[0]
    store.replace_scene_cuts(mid, [5 * S, 2 * S], [0.1, 0.2])
    cache = tmp_path / "cache"
    (cache / "frames").mkdir(parents=True)
    np.savez(
        cache / "frames" / f"{mid}.npz", motion=np.array([1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0])
    )
    loaded = load_signals(store, cache, mid)
    assert loaded.media_id == mid
    assert loaded.cuts_us == [2 * S, 5 * S]
    assert list(loaded.motion_per_s) == [1.0, 2.0]
    # no npz: empty motion
    other = load_signals(store, tmp_path / "nowhere", mid)
    assert len(other.motion_per_s) == 0


def test_duration_reasons_match_the_final_moment():
    # applause 100-105 s and a cheer 103-125 s merge into one 25 s APPLAUSE moment
    events = [ev(AudioLabel.APPLAUSE, 100, 105), ev(AudioLabel.CHEER, 103, 125)]
    (m,) = find_moments(sig(events=events))
    assert m.type is MomentType.APPLAUSE and m.end_us - m.start_us == 25 * S
    assert [r.code for r in m.reasons] == ["applause"]
    assert m.reasons[0].ru == "Аплодисменты 25 с"
    assert m.reasons[0].ky == "Кол чабуу 25 сек"
    # snapping to cuts changes the length: the reason follows it
    music = [ev(AudioLabel.MUSIC, 100, 140)]
    cuts = [int(98.5 * S), int(141.5 * S)]
    (snapped,) = find_moments(sig(events=music, motion=np.ones(600), cuts=cuts))
    assert snapped.reasons[0].ru == "Музыка 43 с без активного движения"
