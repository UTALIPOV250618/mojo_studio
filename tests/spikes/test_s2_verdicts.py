from fractions import Fraction

import pytest

try:
    from spikes.s2_player.bench_player import VERDICT_KEYS, rebuild_plan, verdicts
except (ImportError, OSError):  # python-mpv missing or libmpv not found
    pytest.skip("python-mpv / libmpv not available", allow_module_level=True)

from spikes.s2_player import timeline as tl


def _painted(mx):
    return {"to_swap_ms": {"p95": mx, "max": mx}, "not_seen_within_1s": 0}


def _result(**over):
    r = {
        "seams": {"bad_seams": 0},
        "control_lead100": {"first_frame_lost": 0, "picture_wrong": 0},
        "playback": {"counters_delta": {"frame-drop-count": 0, "vo-delayed-frame-count": 0}},
        "playback_after": {"counters_delta": {"frame-drop-count": 0}},
        "seek_paused": {"to_restart_ms": {"p95": 40.0}, "painted": _painted(30.0)},
        "rebuild_paused": {
            v: {
                "to_restart_ms": {"max": 120.0},
                "to_paint_after_restart_ms": {"max": 200.0},
                "painted_shift": _painted(90.0),
                "extend_to_restart_ms": {"max": 60.0},
                "frame_wrong": 0,
            }
            for v in ("loadfile_start", "loadfile_then_seek")
        },
        "rebuild_playing": {
            v: {"to_restart_ms": {"max": 120.0}, "to_paint_after_restart_ms": {"max": 170.0}}
            for v in ("loadfile_start", "loadfile_then_seek")
        },
        "frame_step": {
            k: {"ms": {"max": 50.0}, "errors": 0, "painted": _painted(80.0)}
            for k in ("forward", "back")
        },
        "sync": {"worst_abs_us": 5000, "frame_us_used": 20000, "all_events_found": True},
        "seam_audio": {"seams_above_limit": 0, "lag_at_search_edge": 0},
        "titles": {"rendered": True, "changed_pixels": 9000},
    }
    r.update(over)
    return r


def test_all_pass_and_all_keys_present():
    v = verdicts(_result())
    assert set(v) == set(VERDICT_KEYS)
    assert all(x["pass"] for x in v.values()), v
    assert v["rebuild_paused_le_300ms"]["value"] == 90.0
    assert v["rebuild_playing_le_300ms"]["value"] == 170.0  # paint after restart bound
    assert v["frame_step_le_100ms_no_errors"]["basis"] == "painted"


def test_failures_and_missing_steps():
    r = _result(
        playback={"counters_delta": {"frame-drop-count": 2, "decoder-frame-drop-count": 1}},
        seek_paused={"to_restart_ms": {"p95": 40.0}, "painted": _painted(151.0)},
        frame_step={"error": "Traceback ...\nRuntimeError: boom"},
    )
    del r["seam_audio"]
    v = verdicts(r)
    assert set(v) == set(VERDICT_KEYS)
    assert v["playback_0_dropped_or_delayed"]["value"] == 3
    assert v["seek_painted_p95_le_150ms"]["pass"] is False
    assert v["seek_event_p95_le_150ms"]["pass"] is True
    assert v["frame_step_le_100ms_no_errors"]["pass"] is None
    assert "boom" in v["frame_step_le_100ms_no_errors"]["reason"]
    assert v["seam_audio_no_glitch"] == {"value": None, "pass": None, "reason": "not run"}


def test_rebuild_plan_makes_real_edits():
    sources = {
        "s25": tl.Source("s25", "/a.mp4", Fraction(25), 1500),
        "s50": tl.Source("s50", "/b.mp4", Fraction(50), 3000),
    }
    segs = tl.make_segments(sources, 40, seed=3)
    plan = rebuild_plan(segs, sources, 8, seed=4)
    assert [e["kind"] for e in plan] == ["shift", "shift", "extend", "extend"] * 2
    assert {e["variant"] for e in plan} == {"loadfile_start", "loadfile_then_seek"}
    for e in plan:
        assert e["segments"] != segs
        if e["kind"] == "shift":  # picture under the playhead changes, playhead stays
            assert e["new_frame"] != e["old_frame"]
            assert e["new_pos_us"] == pytest.approx(e["old_pos_us"], abs=1000)
        else:  # same picture, playhead moves by the added length
            assert e["new_frame"] == e["old_frame"]
            assert e["new_pos_us"] > e["old_pos_us"]
