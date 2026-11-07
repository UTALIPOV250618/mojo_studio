import itertools
from fractions import Fraction

import numpy as np
import pytest

from spikes.s2_player import timeline as tl

SOURCES = {
    "s25": tl.Source("s25", "/media/Той Студия/a,b.mp4", Fraction(25), 1500),
    "s2997": tl.Source("s2997", "/m/c.mp4", Fraction(30000, 1001), 1798),
    "s50": tl.Source("s50", "/m/d.mp4", Fraction(50), 3000),
}


def test_pts_floor_never_after_real_pts():
    fps = Fraction(30000, 1001)
    for f in range(0, 2000, 7):
        assert tl.pts_floor_us(f, fps) <= tl.pts_us(f, fps) < tl.pts_floor_us(f, fps) + 1


def test_seg_times_lead_and_exact_length():
    seg = tl.Segment("s2997", 434, 34)
    start, length = tl.seg_times(seg, SOURCES)
    assert start == 14_481_133 - tl.CUT_LEAD_US
    assert length == 15_615_600 - 14_481_133
    assert tl.seg_times(seg, SOURCES, lead_us=0)[0] == 14_481_133


def test_edl_url_quotes_paths_by_utf8_bytes():
    segs = [tl.Segment("s25", 100, 25), tl.Segment("s50", 200, 50)]
    url = tl.edl_url(segs, SOURCES)
    assert url.startswith("edl://")
    first, second = url[len("edl://") :].split(";")
    path = SOURCES["s25"].path
    assert first == f"%{len(path.encode('utf-8'))}%{path},3.999900,1.000000"
    assert len(path.encode("utf-8")) != len(path)  # Cyrillic: bytes, not characters
    assert second == "%8%/m/d.mp4,3.999900,1.000000"


def test_fmt_us():
    assert tl.fmt_us(0) == "0.000000"
    assert tl.fmt_us(1_000_001) == "1.000001"
    assert tl.fmt_us(-100) == "-0.000100"


def test_make_segments_rules():
    segs = tl.make_segments(SOURCES, 200, seed=2)
    assert len(segs) == 200
    assert segs == tl.make_segments(SOURCES, 200, seed=2)
    for prev, seg in itertools.pairwise(segs):
        assert not (prev.src == seg.src and prev.in_frame + prev.frames == seg.in_frame)
    events = list(range(tl.EVENT_FIRST_US, 60_000_000, tl.EVENT_EVERY_US))
    for seg in segs:
        src = SOURCES[seg.src]
        dur = seg.frames / src.fps
        assert Fraction(1, 2) - 1 / src.fps <= dur <= 3 + 1 / src.fps
        assert seg.in_frame > 0
        assert seg.in_frame + seg.frames <= src.frames - src.fps
        for cut in (seg.in_frame, seg.in_frame + seg.frames):
            t = tl.pts_floor_us(cut, src.fps)
            assert all(abs(t - e) >= tl.CUT_MARGIN_US for e in events)


def test_output_frames_contiguous_and_mapped():
    segs = [tl.Segment("s25", 100, 3), tl.Segment("s50", 200, 2)]
    frames = tl.output_frames(segs, SOURCES)
    assert [(f.seg, f.src, f.frame) for f in frames] == [
        (0, "s25", 100), (0, "s25", 101), (0, "s25", 102), (1, "s50", 200), (1, "s50", 201)
    ]  # fmt: skip
    lead = tl.CUT_LEAD_US
    assert [f.t_us for f in frames] == [
        lead,
        40_000 + lead,
        80_000 + lead,
        120_000 + lead,
        140_000 + lead,
    ]
    assert tl.seg_out_starts(segs, SOURCES) == [0, 120_000, 160_000]
    assert tl.frame_index_at(frames, 120_000 + lead + 500) == 3
    assert tl.frame_index_at(frames, 100_000) is None


def test_edit_segment_moves_and_flips_at_source_end():
    segs = [tl.Segment("s25", 100, 25), tl.Segment("s25", 1470, 25)]
    moved = tl.edit_segment(segs, SOURCES, 0, 25)
    assert moved[0] == tl.Segment("s25", 125, 25) and moved[1] == segs[1]
    flipped = tl.edit_segment(segs, SOURCES, 1, 25)
    assert flipped[1] == tl.Segment("s25", 1445, 25)
    assert segs[0].in_frame == 100  # original untouched


def test_expected_events_flash_and_beep_positions():
    # s2997 from frame 0 is not allowed by make_segments, but the maths is the same:
    seg = tl.Segment("s2997", 15, 60)  # 0.5005 s .. 2.5025 s, contains the 1.0 s and ... event
    ev = tl.expected_events([tl.Segment("s25", 10, 5), seg], SOURCES)
    assert len(ev) == 1
    e = ev[0]
    start = tl.pts_floor_us(15, SOURCES["s2997"].fps) - tl.CUT_LEAD_US
    assert e["beep_us"] == 200_000 + 1_000_000 - start
    # flash on frame 30 (1.001 s), the frame nearest 1.0 s
    assert e["flash_us"] == round(200_000 + Fraction(30 * 1_000_000) / SOURCES["s2997"].fps - start)
    assert e["frame_us"] == 33_367


def test_sync_report_shift_removes_source_offset():
    expected = [
        {"seg": 0, "beep_us": 1_000_000, "flash_us": 1_001_000, "frame_us": 33_367},
        {"seg": 1, "beep_us": 3_000_000, "flash_us": 3_000_000, "frame_us": 20_000},
    ]
    rep = tl.sync_report([1_001_000, 3_000_000], [1_002_000, 3_000_500], expected)
    assert rep["all_events_found"]
    assert rep["shift"] == {"n": 2, "min_us": 500, "max_us": 2000, "mean_us": 1250}
    assert rep["worst_abs_us"] == 2000
    assert rep["frame_us_used"] == 20_000
    missing = tl.sync_report([1_001_000], [1_002_000], expected)
    assert not missing["all_events_found"] and missing["events_found"] == 1


def test_mean_abs_diff_and_raw_screenshot():
    w, h, stride = 4, 2, 20  # padded rows
    raw = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            raw[y * stride + x * 4 : y * stride + x * 4 + 4] = bytes([10, 20, 30, 0])
    img = tl.bgr0_from_raw(bytes(raw), w, h, stride)
    assert img.shape == (2, 4, 4)
    ref = img.copy()
    ref[..., 3] = 255  # alpha/padding ignored
    assert tl.mean_abs_diff(img, ref) == 0
    ref[0, 0, :3] += 12
    assert tl.mean_abs_diff(img, ref) == pytest.approx(12 * 3 / (2 * 4 * 3))
    with pytest.raises(ValueError):
        tl.mean_abs_diff(img, img[:1])


def _tone_sources():
    sr = 1000  # 1 kHz "sample rate" keeps the arrays small
    srcs = {
        "a": tl.Source("a", "a", Fraction(25), 250),  # 10 s
        "b": tl.Source("b", "b", Fraction(25), 250),
    }
    t = np.arange(10 * sr) / sr
    audio = {"a": 0.3 * np.sin(2 * np.pi * 31 * t), "b": 0.3 * np.sin(2 * np.pi * 47 * t + 1)}
    segs = [tl.Segment("a", 10, 50), tl.Segment("b", 60, 40), tl.Segment("a", 150, 50)]
    return sr, srcs, audio, segs


def _ideal_cut(sr, srcs, audio, segs):
    parts = []
    for seg in segs:
        start, length = tl.seg_times(seg, srcs)
        s0 = round(start * sr / tl.US)
        parts.append(audio[seg.src][s0 : s0 + round(length * sr / tl.US)])
    return np.concatenate(parts)


def test_seam_residuals_pass_on_ideal_cut():
    sr, srcs, audio, segs = _tone_sources()
    out = _ideal_cut(sr, srcs, audio, segs)
    r = tl.seam_residuals(out, audio, segs, srcs, sr, max_lag=3)
    assert r["n_seams"] == 2 and r["n_baseline"] == 3
    assert r["seam_peak"]["max"] < 1e-9
    assert r["cut_step"]["max"] > 0.05  # the cuts themselves land mid-waveform
    assert r["seams_above_limit"] == 0 and r["lag_at_search_edge"] == 0


def test_seam_residuals_catch_gap_and_shift():
    sr, srcs, audio, segs = _tone_sources()
    ideal = _ideal_cut(sr, srcs, audio, segs)
    seam = round(tl.seg_out_starts(segs, srcs)[1] * sr / tl.US)
    gap = ideal.copy()
    gap[seam : seam + 3] = 0  # 3 ms of silence right after the first seam
    r = tl.seam_residuals(gap, audio, segs, srcs, sr, max_lag=3)
    assert r["seams_above_limit"] >= 1
    assert r["worst_seams"][0]["seam"] == 1
    late = np.concatenate([ideal[:seam], np.zeros(8), ideal[seam:]])  # 8 ms late
    r2 = tl.seam_residuals(late, audio, segs, srcs, sr, max_lag=3)
    assert r2["seams_above_limit"] + r2["lag_at_search_edge"] >= 1


def test_edit_length_and_map_position():
    segs = [tl.Segment("s25", 100, 25), tl.Segment("s25", 400, 25), tl.Segment("s25", 1470, 25)]
    longer = tl.edit_length(segs, SOURCES, 0, 10)
    assert longer[0] == tl.Segment("s25", 100, 35) and longer[1:] == segs[1:]
    head = tl.edit_length(segs, SOURCES, 2, 10)  # no room at the tail
    assert head[2] == tl.Segment("s25", 1460, 35)
    old, new = tl.output_frames(segs, SOURCES), tl.output_frames(longer, SOURCES)
    pos = old[30].t_us  # 6th frame of segment 1
    mapped = tl.map_position(old, new, pos)
    assert mapped == pytest.approx(pos + 400_000)
    j = tl.frame_index_at(new, mapped)
    assert (new[j].seg, new[j].frame) == (1, 405)
    assert tl.map_position(old, new, old[3].t_us) == old[3].t_us  # before the edit


def test_painted_frame_helpers():
    assert tl.video_rect(
        {"w": 1920, "h": 1100, "mt": 10, "mb": 10, "ml": 0, "mr": 0}, 1920, 1100
    ) == (
        0,
        10,
        1920,
        1080,
    )
    ref = np.zeros((2, 3, 4), dtype=np.uint8)
    ref[..., 0], ref[..., 1], ref[..., 2] = 10, 20, 30  # bgr0
    rgba = np.zeros((6, 6, 4), dtype=np.uint8)
    rgba[1:5, :, 0], rgba[1:5, :, 1], rgba[1:5, :, 2] = 30, 20, 10  # RGB, 2x scale + bars
    small = tl.painted_small(rgba, (0, 1, 6, 4), 3, 2)
    assert small.shape == (2, 3, 3)
    assert tl.small_mad(small, ref) == 0
    odd = tl.resample_area(np.arange(5 * 7 * 1, dtype=np.uint8).reshape(5, 7, 1), 3, 2)
    assert odd.shape == (2, 3, 1)
    assert odd.min() >= 0 and odd.max() <= 34


def test_percentile_and_summary():
    assert tl.percentile([1, 2, 3, 4], 50) == 2.5
    assert tl.percentile([5], 95) == 5
    s = tl.summary_ms([float(i) for i in range(1, 101)])
    assert s["n"] == 100 and s["p95"] == 95.05 and s["max"] == 100
    assert tl.summary_ms([]) == {"n": 0}
    with pytest.raises(ValueError):
        tl.percentile([], 50)


def test_verdict():
    assert tl.verdict(120.0, tl.SEEK_P95_MS, unit="ms")["pass"] is True
    assert tl.verdict(151.0, tl.SEEK_P95_MS, unit="ms")["pass"] is False
    assert tl.verdict(None, 1, unit="x")["pass"] is None
    assert tl.verdict(1.0, 5, unit="x", ok=False)["pass"] is False
