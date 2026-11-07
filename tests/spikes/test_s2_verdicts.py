import pytest

try:
    from spikes.s2_player.bench_player import verdicts
except (ImportError, OSError):  # python-mpv missing or libmpv not found
    pytest.skip("python-mpv / libmpv not available", allow_module_level=True)


def _result(**over):
    r = {
        "seams": {"bad_seams": 0},
        "playback": {"counters_delta": {"frame-drop-count": 0, "vo-delayed-frame-count": 0}},
        "seek_paused": {"to_restart_ms": {"p95": 40.0}},
        "rebuild_paused": {
            "loadfile_start": {"to_restart_ms": {"max": 120.0}},
            "loadfile_then_seek": {"to_restart_ms": {"max": 180.0}},
        },
        "frame_step": {
            "forward": {"ms": {"max": 50.0}, "errors": 0},
            "back": {"ms": {"max": 90.0}, "errors": 0},
        },
        "sync": {
            "worst_abs_us": 5000,
            "frame_us_used": 20000,
            "all_events_found": True,
            "clicks": {"seams_above_rest_peak": 0},
        },
        "titles": {"rendered": True, "changed_pixels": 9000},
    }
    r.update(over)
    return r


def test_all_pass():
    v = verdicts(_result())
    assert all(x["pass"] for k, x in v.items() if k != "rebuild_playing_max_le_300ms"), v
    assert v["rebuild_paused_max_le_300ms"]["value"] == 180.0
    assert v["rebuild_playing_max_le_300ms"]["pass"] is None  # not measured


def test_failures():
    v = verdicts(
        _result(
            playback={"counters_delta": {"frame-drop-count": 2, "vo-delayed-frame-count": 1}},
            frame_step={
                "forward": {"ms": {"max": 50.0}, "errors": 1},
                "back": {"ms": {"max": 90.0}, "errors": 0},
            },
            seek_paused={"to_restart_ms": {"p95": 151.0}},
        )
    )
    assert v["playback_0_dropped_or_delayed"]["value"] == 3
    assert v["playback_0_dropped_or_delayed"]["pass"] is False
    assert v["frame_step_le_100ms_no_errors"]["pass"] is False
    assert v["seek_p95_le_150ms"]["pass"] is False
