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
