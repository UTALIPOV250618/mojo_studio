"""S3 arithmetic helpers, command building and the orientation marker (pure logic)."""

import numpy as np
import pytest

from spikes.s3_decode import single_pass as sp
from spikes.s3_decode import sources, stats


def test_x_realtime_cpu_load_and_eta() -> None:
    assert stats.x_realtime(300, 20) == 15.0
    assert stats.x_realtime(300, 0) == 0.0
    assert stats.cpu_load_pct(6, 12) == 50.0
    assert stats.cpu_load_pct(24, 12) == 200.0
    assert stats.analysis_minutes(13200, 15) == pytest.approx(14.667, abs=1e-3)
    assert stats.analysis_minutes(13200, 0) == float("inf")
    assert stats.fmt_hms(3725) == "1:02:05"
    assert stats.median_of([3, 1, 2]) == 2 and stats.median_of([]) == 0.0


@pytest.mark.parametrize(
    ("avg", "want"),
    [
        ("25/1", "25"),
        ("30000/1001", "30000/1001"),
        ("2997/100", "30000/1001"),
        ("50/1", "50"),
        ("29/1", "30000/1001"),
        ("0/0", "25"),
        ("30/1", "30"),
    ],
)
def test_pick_cfr(avg: str, want: str) -> None:
    assert stats.pick_cfr(avg) == want


def _info(w: int, h: int, hdr: bool = False, rot: int = 0) -> sp.SourceInfo:
    return sp.SourceInfo(w, h, rot, "30/1", 10.0, hdr, True, "hevc", "yuv420p10le", "", 0)


def test_sizes_for_landscape_and_portrait() -> None:
    assert sp.proxy_size_filter(1920, 1080) == "scale=-2:540"
    assert sp.proxy_size_filter(2160, 3840) == "scale=540:-2"
    assert sp.metric_size(1920, 1080) == (160, 90)
    assert sp.metric_size(2160, 3840) == (90, 160)


def test_graph_hdr_zscale_and_fallback_and_nometrics() -> None:
    g, used = sp.build_filter_graph(
        _info(2160, 3840, hdr=True), metrics=True, metric_fps=4, tonemap="zscale"
    )
    assert used == "zscale" and "tonemap=tonemap=hable" in g and "scale=90:160" in g
    assert g.index("scale=540:-2") < g.index("zscale") < g.index("split=2")
    g2, used2 = sp.build_filter_graph(
        _info(2160, 3840, hdr=True), metrics=True, metric_fps=4, tonemap="fallback"
    )
    assert used2 == "fallback" and "colorspace=all=bt709" in g2
    g3, used3 = sp.build_filter_graph(
        _info(1920, 1080), metrics=False, metric_fps=4, tonemap="zscale"
    )
    assert used3 == "none" and "split" not in g3 and g3.endswith("[proxy]")


def test_command_is_argument_list_with_three_outputs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    src = tmp_path / "Той Студия" / "клип 1.mp4"
    out = tmp_path / "вывод 1"
    info = _info(1920, 1080)
    cmd, _ = sp.build_command(src, out, info, hwaccel="d3d11va", metrics=True)
    assert cmd[cmd.index("-hwaccel") : cmd.index("-hwaccel") + 2] == ["-hwaccel", "d3d11va"]
    assert str(src) in cmd and str(out / "proxy.mp4") in cmd and str(out / "audio.wav") in cmd
    assert cmd[-1] == "pipe:1" and cmd.count("-map") == 3
    assert cmd.index("-hwaccel") < cmd.index("-i")
    cmd2, _ = sp.build_command(src, out, info, metrics=False)
    assert "pipe:1" not in cmd2 and "-hwaccel" not in cmd2


def test_parse_progress_line() -> None:
    assert sp.parse_progress_line("out_time_us=1500000\n") == ("out_time_us", "1500000")
    assert sp.parse_progress_line("Error while decoding stream") is None


def test_rotation_of_side_data_and_tag() -> None:
    assert sp.rotation_of({"side_data_list": [{"rotation": -90}]}) == -90
    assert sp.rotation_of({"tags": {"rotate": "90"}}) == 90
    assert sp.rotation_of({}) == 0


@pytest.mark.parametrize(
    ("rot", "want"),
    [
        (0, "top-left"),
        (-90, "top-right"),
        (90, "bottom-left"),
        (180, "bottom-right"),
        (-180, "bottom-right"),
    ],
)
def test_expected_marker_quadrant(rot: int, want: str) -> None:
    assert sources.expected_marker_quadrant(rot) == want


def test_marker_quadrant_finds_bright_corner() -> None:
    f = np.full((20, 10), 40, dtype=np.uint8)
    f[:8, 5:] = 255
    name, lead = sources.marker_quadrant(f)
    assert name == "top-right" and lead > 0.3


def test_classify_hwaccel_log() -> None:
    ok = "x\n[hevc @ 0x1] Format videotoolbox_vld chosen by get_format().\n"
    assert sp.classify_hwaccel_log(ok) == (True, "videotoolbox_vld")
    assert sp.classify_hwaccel_log("[h264 @ 1] Format d3d11 chosen by get_format().")[0] is True
    soft = "[h264 @ 1] Format yuv420p chosen by get_format()."
    assert sp.classify_hwaccel_log(soft) == (False, "yuv420p")
    bad = "[mpeg4 @ 1] Failed setup for format videotoolbox_vld: hwaccel initialisation returned error."
    assert sp.classify_hwaccel_log(bad) == (False, None)
    assert sp.classify_hwaccel_log("nothing here") == (False, None)
    assert sp.HW_FALLBACK_RE.search("Failed setup for format d3d11: hwaccel initialisation")


def test_hw_runs_log_warnings_and_threads_cap(tmp_path) -> None:  # type: ignore[no-untyped-def]
    info = _info(1920, 1080)
    hw, _ = sp.build_command(tmp_path / "a.mp4", tmp_path, info, hwaccel="d3d11va", threads=4)
    assert hw[hw.index("-loglevel") + 1] == "warning"
    assert hw.count("-threads") == 2 and "-filter_threads" in hw
    assert hw.index("-threads") < hw.index("-i")
    sw, _ = sp.build_command(tmp_path / "a.mp4", tmp_path, info)
    assert sw[sw.index("-loglevel") + 1] == "error" and "-threads" not in sw


def _ramp(n: int = 400) -> np.ndarray:
    return np.linspace(16, 235, n).astype(np.uint8)


def test_analyze_ramp_accepts_clean_sdr_ramp() -> None:
    res = sources.analyze_ramp(_ramp())
    assert res["pass"] is True and res["monotonic_drops"] == 0


def test_analyze_ramp_rejects_clipping_lifted_black_and_inversion() -> None:
    clipped = np.minimum(_ramp(), 200).astype(np.uint8)
    clipped = np.where(_ramp() > 200, 235, clipped).astype(np.uint8)
    assert sources.analyze_ramp(clipped)["pass"] is False  # highlight plateau
    lifted = np.linspace(80, 235, 400).astype(np.uint8)
    assert sources.analyze_ramp(lifted)["pass"] is False  # washed-out blacks
    assert sources.analyze_ramp(_ramp()[::-1].copy())["pass"] is False  # not monotonic


def test_parse_only_never_needs_an_empty_argument() -> None:
    from spikes.s3_decode import bench_decode as bd

    assert bd.parse_only("all") == list(bd.SYNTH)
    assert bd.parse_only("none") == []
    assert bd.parse_only("h264_1080p25,hevc_4k_hlg_rot") == ["h264_1080p25", "hevc_4k_hlg_rot"]
    with pytest.raises(SystemExit):
        bd.parse_only("bogus")


def test_read_real_list_utf8(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from spikes.s3_decode import bench_decode as bd

    f = tmp_path / "real.txt"
    f.write_text("# c\ncamera=D:\\Свадьба\\камера 1\\C0001.MP4\n\n", encoding="utf-8-sig")
    assert bd.read_real_list(f) == ["camera=D:\\Свадьба\\камера 1\\C0001.MP4"]


def _row(hw: str, x: float, cpu: float, active: bool | None, codec: str = "h264", h: int = 1080):  # type: ignore[no-untyped-def]
    return {
        "hwaccel": hw,
        "info": {"codec": codec, "size_displayed": [1920, h]},
        "stats": {
            "x_rt": x, "python_cpu_pct": 2.0, "total_peak_mb": 300.0, "ffmpeg_cpu_pct": cpu,
            "hw_active": active, "hw_format": "x", "hw_fallback_warning": active is False,
        },
    }  # fmt: skip


def test_verdicts_hw_inactive_invalidates_threshold_and_gain() -> None:
    from spikes.s3_decode import bench_decode as bd

    cpu = _row("none", 11.0, 750, None)
    fake_hw = _row("d3d11va", 11.2, 740, False)
    v = bd.verdicts(fake_hw, None, cpu)
    assert v["hw_verdict_valid"] is False and v["ge_12x"] is None and "hw_gain_x" not in v
    assert v["hw_cpu_suspicious"] is True
    real_hw = _row("d3d11va", 13.0, 300, True)
    v2 = bd.verdicts(real_hw, None, cpu)
    assert v2["hw_verdict_valid"] is True and v2["ge_12x"] is True and v2["hw_gain_x"] == 1.18
    assert v2["hw_cpu_suspicious"] is False
    v3 = bd.verdicts(_row("none", 2.8, 700, None, codec="hevc", h=3840), None, None)
    assert v3["ge_12x"] is None  # threshold is defined for 1080p H.264 only
