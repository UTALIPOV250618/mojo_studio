"""One tiny end-to-end single_pass run (small frames, ~2 s of work)."""

import re
import shutil
import subprocess

import numpy as np
import pytest

from spikes.common.media import run_ffmpeg
from spikes.s3_decode import single_pass as sp


def _ffmpeg_major() -> int:
    exe = shutil.which("ffmpeg")
    if exe is None or shutil.which("ffprobe") is None:
        return 0
    out = subprocess.run([exe, "-version"], capture_output=True, text=True, check=False).stdout
    m = re.match(r"ffmpeg version n?(\d+)\.", out)
    return int(m.group(1)) if m else 99


@pytest.mark.skipif(_ffmpeg_major() < 7, reason="needs ffmpeg/ffprobe 7.0+ on PATH")
def test_single_pass_three_outputs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    src = tmp_path / "Той Студия" / "клип 1.mp4"
    src.parent.mkdir()
    run_ffmpeg(
        ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)]
    )  # fmt: skip
    out = tmp_path / "вывод"
    stats = sp.run_single_pass(src, out, tonemap="fallback")
    assert stats.returncode == 0, stats.stderr_tail
    assert (out / "proxy.mp4").stat().st_size > 0 and (out / "audio.wav").stat().st_size > 0
    assert stats.frames == 8 and stats.partial_bytes == 0  # 2 s at 4 fps
    data = np.load(out / "frames.npz")
    assert data["sharpness"].shape == (8,) and float(data["brightness"].mean()) > 0.05


@pytest.mark.skipif(_ffmpeg_major() < 7, reason="needs ffmpeg/ffprobe 7.0+ on PATH")
def test_verify_outputs_rotated_hlg_through_fallback(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from spikes.s3_decode import bench_decode as bd
    from spikes.s3_decode import sources

    monkeypatch.setattr(bd, "RESULTS_DIR", tmp_path / "results")
    src = sources.make_hevc_hlg_rotated(
        tmp_path / "hlg.mp4", duration_s=2, fps="25", size="320x180", mbps=2
    )
    info = sp.source_info(src)
    assert info.hdr and info.rotation == -90 and (info.width, info.height) == (180, 320)
    stats = sp.run_single_pass(src, tmp_path / "o", tonemap="fallback", metrics="none")
    assert stats.returncode == 0, stats.stderr_tail
    res = bd.verify_outputs(tmp_path / "o", info, marker=True, frame_name="t")
    assert res["orientation_ok"] is True and res["marker_found"] == "top-right"
    assert res["proxy_tags_sdr"] is True and res["proxy_rotation"] is False
    assert res["proxy_size"] == [540, 960] and res["wav_ok"] is True
    assert (tmp_path / "results" / "s3" / "frames" / "t.jpg").exists()


@pytest.mark.skipif(_ffmpeg_major() < 7, reason="needs ffmpeg/ffprobe 7.0+ on PATH")
def test_source_without_audio_has_no_wav(tmp_path) -> None:  # type: ignore[no-untyped-def]
    src = tmp_path / "silent.mp4"
    run_ffmpeg(
        ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)]
    )  # fmt: skip
    stats = sp.run_single_pass(src, tmp_path / "o", tonemap="fallback")
    assert stats.returncode == 0, stats.stderr_tail
    assert (tmp_path / "o" / "proxy.mp4").exists() and not (tmp_path / "o" / "audio.wav").exists()
