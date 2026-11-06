import pytest

from tests.conftest import tl3
from toystudio.core.models import US_PER_S, AudioMode
from toystudio.core.timeline import MIN_SEGMENT_US, Segment, Timeline
from toystudio.core.timeline_ops import (
    delete,
    duration_hint,
    move,
    set_audio_mode,
    set_locked,
    split,
    trim,
)


def test_delete_closes_gap():
    t = delete(tl3(), "b")
    assert [s.id for s in t.video] == ["a", "c"] and t.duration() == 20 * US_PER_S
    assert t.titles == ()


def test_move_sets_manual_only_on_moved():
    t = move(tl3(), "c", 0)
    assert [s.id for s in t.video] == ["c", "a", "b"]
    assert [s.manual for s in t.video] == [True, False, False]


def test_move_bad_index_and_unknown_id():
    with pytest.raises(ValueError):
        move(tl3(), "a", 3)
    with pytest.raises(ValueError):
        move(tl3(), "a", -1)
    with pytest.raises(KeyError):
        move(tl3(), "zz", 0)
    with pytest.raises(KeyError):
        delete(tl3(), "zz")


def test_trim_clamps_to_bounds():
    s = tl3().segment("a")
    t = trim(tl3(), "a", "in", s.bound_in - 99 * US_PER_S)
    assert t.segment("a").src_in == s.bound_in
    t = trim(tl3(), "a", "out", s.bound_out + 99 * US_PER_S)
    assert t.segment("a").src_out == s.bound_out
    assert t.segment("a").manual


def test_trim_keeps_min_length():
    s = tl3().segment("a")
    t = trim(tl3(), "a", "out", s.src_in)
    assert t.segment("a").duration == MIN_SEGMENT_US
    t = trim(tl3(), "a", "in", s.src_out)
    assert t.segment("a").duration == MIN_SEGMENT_US


def test_split_at_position():
    t = split(tl3(), "b", 15 * US_PER_S, "b2")
    assert [s.id for s in t.video] == ["a", "b", "b2", "c"]
    assert t.segment("b").duration == t.segment("b2").duration == 5 * US_PER_S
    assert t.segment("b").manual and t.segment("b2").manual
    assert not t.segment("c").manual
    assert t.segment("t1").anchor_id == "b"
    assert t.duration() == 30 * US_PER_S


def test_split_rejects_too_close_to_edge():
    with pytest.raises(ValueError):
        split(tl3(), "b", 10 * US_PER_S + 20_000, "b2")
    with pytest.raises(ValueError):
        split(tl3(), "b", 20 * US_PER_S - 20_000, "b2")


def test_set_locked_and_audio_mode():
    t = set_locked(tl3(), "a", True)
    assert t.segment("a").locked and t.segment("a").manual
    t = set_audio_mode(t, "a", AudioMode.DUCK)
    assert t.segment("a").audio_mode is AudioMode.DUCK
    assert t.segment("b") is tl3().segment("b") or t.segment("b") == tl3().segment("b")


def test_duration_hint_threshold():
    assert duration_hint(tl3(), 30 * US_PER_S) is None
    assert duration_hint(delete(tl3(), "b"), 30 * US_PER_S) == (20 * US_PER_S, 30 * US_PER_S)
    assert duration_hint(tl3(), 33 * US_PER_S) is None


def test_map_position_after_move():
    old = tl3()
    new = move(old, "c", 0)
    assert new.map_position(old, 25 * US_PER_S) == 5 * US_PER_S


def test_map_position_deleted_goes_to_next():
    old = tl3()
    new = delete(old, "b")
    assert new.map_position(old, 15 * US_PER_S) == 10 * US_PER_S
    assert delete(old, "c").map_position(old, 25 * US_PER_S) == 20 * US_PER_S


def test_queries():
    t = tl3()
    assert t.starts(t.video[0].track) == [0, 10 * US_PER_S, 20 * US_PER_S]
    assert t.index_of("c")[1] == 2
    assert isinstance(t, Timeline)
    with pytest.raises(ValueError):
        Segment("x", t.video[0].track, "m", 5, 5, None, 0, 10)


S = US_PER_S


def test_trim_out_clamps_anchored_title():
    t = trim(tl3(), "b", "out", 200 * S + 3 * S)  # b is now 3 s; t1 window 1..4 s
    t1 = t.segment("t1")
    assert (t1.src_in, t1.src_out, t1.bound_out) == (1 * S, 3 * S, 3 * S)


def test_trim_out_drops_title_with_nothing_left():
    t = trim(tl3(), "b", "out", 200 * S + 1 * S)  # b is 1 s; title starts at 1 s
    assert t.titles == ()


def test_trim_in_shifts_title_with_source_moment():
    t = trim(tl3(), "b", "in", 200 * S + 1 * S)  # b now 9 s; title moves 1 s earlier
    t1 = t.segment("t1")
    assert (t1.src_in, t1.src_out, t1.bound_out) == (0, 3 * S, 9 * S)


def test_split_reanchors_title_by_offset():
    t = split(tl3(), "b", 10 * S + 3 * S, "b2")  # cut at 3 s; title 1..4 s straddles
    t1 = t.segment("t1")
    assert t1.anchor_id == "b" and (t1.src_in, t1.src_out, t1.bound_out) == (1 * S, 3 * S, 3 * S)
    t = split(tl3(), "b", 10 * S + 500_000, "b2")  # cut at 0.5 s; title wholly in second half
    t1 = t.segment("t1")
    assert t1.anchor_id == "b2"
    assert (t1.src_in, t1.src_out, t1.bound_out) == (500_000, 3_500_000, 9_500_000)


def test_split_rejects_duplicate_id():
    with pytest.raises(ValueError):
        split(tl3(), "b", 15 * S, "c")


def test_map_position_after_split_uses_source_time():
    old = tl3()
    new = split(old, "b", 15 * S, "b2")
    assert new.map_position(old, 17 * S) == 17 * S
    assert new.map_position(old, 12 * S) == 12 * S


def test_map_position_after_in_trim_keeps_source_frame():
    old = tl3()
    new = trim(old, "b", "in", 200 * S + 2 * S)  # frame at old 17 s (source 207) -> 5 s in b
    assert new.map_position(old, 17 * S) == 10 * S + 5 * S
