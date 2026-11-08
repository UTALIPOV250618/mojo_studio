import copy
from fractions import Fraction

from spikes.s5_export.build_chunks import PRESETS, audio_graph, video_chain
from spikes.s5_export.make_edl import SOURCES, edited
from spikes.s5_export.plan import (
    US,
    Piece,
    chunk_key,
    dedupe_runs,
    drift_fit,
    event_times_us,
    expected_events,
    frames_for,
    make_test_edl,
    pair_nearest,
    plan_chunks,
    plan_chunks_local,
    quantise,
    sync_stats,
)

FPS25 = Fraction(25)
NTSC = Fraction(30000, 1001)


def test_edl_is_deterministic_and_keeps_cuts_clear_of_events() -> None:
    a = make_test_edl(SOURCES, seed=5)
    assert a == make_test_edl(SOURCES, seed=5)
    assert a != make_test_edl(SOURCES, seed=6)
    ev = a["events"]
    events = event_times_us(ev["source_duration_us"], ev["first_us"], ev["every_us"])
    m = a["margin_us"]
    assert len(a["segments"]) == 120
    total = sum(s["dur_us"] for s in a["segments"])
    assert 270 * US < total < 330 * US
    for s in a["segments"]:
        start, end = s["in_us"], s["in_us"] + s["dur_us"]
        assert US <= s["dur_us"] <= 4 * US
        assert end < ev["source_duration_us"]
        assert all(abs(start - e) >= m and abs(end - e) >= m for e in events)
        assert any(start + m <= e <= end - m for e in events)


def test_edits_touch_one_segment_only() -> None:
    a = make_test_edl(SOURCES, seed=5)
    for b in (edited(a, move_us=2 * US), edited(a, extend_us=2 * US)):
        diff = [i for i, (x, y) in enumerate(zip(a["segments"], b["segments"])) if x != y]
        assert diff == [60]


def test_frames_for_rounds_per_segment() -> None:
    assert frames_for(2_000_000, FPS25) == 50
    assert frames_for(2_019_999, FPS25) == 50
    assert frames_for(2_020_000, FPS25) == 51
    assert frames_for(1_000_000, NTSC) == 30  # 29.97


def test_plan_chunks_targets_and_splits_long_piece() -> None:
    pieces = [Piece(i, "a", 0, 75) for i in range(10)]  # 3 s each at 25 fps
    chunks = plan_chunks(pieces, FPS25, 30)
    assert [sum(p.frames for p in c) for c in chunks] == [750]
    chunks = plan_chunks(pieces, FPS25, 6)
    assert [sum(p.frames for p in c) for c in chunks] == [150] * 5
    long = [Piece(0, "a", 1_000_000, 2000)]  # 80 s
    chunks = plan_chunks(long, FPS25, 30)
    assert [sum(p.frames for p in c) for c in chunks] == [750, 750, 500]
    assert chunks[1][0].in_us == 1_000_000 + 30 * US
    assert sum(p.frames for c in chunks for p in c) == 2000


def _keys(edl: dict, local: bool, ids: dict[str, str]) -> list[str]:
    ps = quantise(edl["segments"], FPS25)
    ch = plan_chunks_local(ps, FPS25, 30, ids) if local else plan_chunks(ps, FPS25, 30)
    return [chunk_key(c, ids, {}, [], toolchain="t", graph="g") for c in ch]


def test_local_plan_keeps_edits_local_greedy_does_not() -> None:
    edl = make_test_edl(SOURCES, seed=5)
    ids = {k: k for k in SOURCES}
    ps = quantise(edl["segments"], FPS25)
    chunks = plan_chunks_local(ps, FPS25, 30, ids)
    assert [p for c in chunks for p in c] == ps  # same material, same order
    assert all(sum(p.frames for p in c) <= 2 * 750 for c in chunks)
    assert 6 <= len(chunks) <= 20
    worst = {False: 0, True: 0}
    for local in (False, True):
        base = set(_keys(edl, local, ids))
        for i in range(0, 120, 5):
            e = copy.deepcopy(edl)
            e["segments"][i]["dur_us"] += 2 * US
            worst[local] = max(worst[local], len(set(_keys(e, local, ids)) - base))
    assert worst[True] <= 3
    assert worst[False] > 3  # the cascade the local plan avoids


def test_chunk_key_changes_only_with_content() -> None:
    ids = {"a": "/x/a.mp4|1|2"}
    enc = ["-c:v", "libx264"]

    def key(ch: list[Piece], **kw: object) -> str:
        args: dict = {"preset": {"fps": "25"}, "toolchain": "s5|ffmpeg 8|libx264", "graph": "g"}
        args.update(kw)
        return chunk_key(ch, ids, args.pop("preset"), enc, **args)

    ch = [Piece(3, "a", 0, 50)]
    k = key(ch)
    assert k == key([Piece(7, "a", 0, 50)])  # segment index is not content
    assert k != key([Piece(3, "a", 1, 50)])
    assert k != key([Piece(3, "a", 0, 50, crop_x=120)])
    assert k != key(ch, preset={"fps": "50"})
    assert k != key(ch, toolchain="s5|ffmpeg 7|libx264")
    assert k != key(ch, graph="g2")
    assert k != key(ch, titles="ass-hash")
    assert k != key(ch, fonts="fonts-hash")


def test_graphs_pad_before_trim() -> None:
    p = Piece(0, "a", 0, 38)
    v = video_chain(0, p, PRESETS["test180"], FPS25, naive=False)
    assert v.index("tpad=stop=-1:stop_mode=clone") < v.index("trim=end_frame=38")
    assert "tpad" not in video_chain(0, p, PRESETS["test180"], FPS25, naive=False, pad=False)
    a = audio_graph([{"src": "a", "in_us": 0, "dur_us": 1_520_000}], 0, 1_520_000)
    assert a.index("apad,") < a.index("atrim=end_sample=72960")
    assert "apad" not in audio_graph([{"src": "a", "in_us": 0, "dur_us": 1}], 0, 1, pad=False)


def test_expected_events_on_output_timeline() -> None:
    edl = {
        "sources": {"a": {"fps": "25"}},
        "events": {"source_duration_us": 60 * US, "first_us": US, "every_us": 2 * US},
    }
    pieces = quantise([{"src": "a", "in_us": 400_000, "dur_us": 1_000_000}] * 2, FPS25)
    ev = expected_events(pieces, edl, FPS25)
    assert ev == [
        {"beep_us": 600_000, "flash_us": 600_000},
        {"beep_us": 1_600_000, "flash_us": 1_600_000},
    ]


def test_pairing_dedupe_and_drift() -> None:
    assert dedupe_runs([0, 33_000, 66_000, 1_000_000], 100_000) == [0, 1_000_000]
    flashes = [i * 2 * US for i in range(10)]
    beeps = [f + 10_000 + i * 1_000 for i, f in enumerate(flashes)]  # 1 ms per 2 s
    pairs = pair_nearest(flashes, beeps + [999 * US], 500_000)
    assert len(pairs) == 10
    st = sync_stats(pairs)
    assert st["max_abs_delta_us"] == 19_000
    assert abs(st["drift"]["slope_ms_per_min"] - 30.0) < 1e-9  # 1 ms / 2 s = 30 ms/min
    flat = drift_fit([0, 60 * US, 120 * US], [3_000, 3_000, 3_000])
    assert flat.slope_ms_per_min == 0 and flat.intercept_ms == 3.0
    assert pair_nearest([0], [], 1) == []
