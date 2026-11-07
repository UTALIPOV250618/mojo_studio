"""Short-source / EOF padding of the S5 export (small frames, ~3 s of work)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from spikes.common.detect import detect_beeps, detect_flashes
from spikes.common.media import generate_clip, generate_music, probe, run_ffmpeg
from spikes.s5_export import build_chunks


def _ffmpeg_major() -> int:
    exe = shutil.which("ffmpeg")
    if exe is None or shutil.which("ffprobe") is None:
        return 0
    out = subprocess.run([exe, "-version"], capture_output=True, text=True, check=False).stdout
    m = re.match(r"ffmpeg version n?(\d+)\.", out)
    return int(m.group(1)) if m else 99  # git/master builds: assume new


needs_ffmpeg = pytest.mark.skipif(
    _ffmpeg_major() < 7, reason="needs ffmpeg/ffprobe 7.0+ on PATH (-/filter_complex)"
)


def _export(tmp: Path, *, pad: bool) -> Path:
    src = tmp / "Той src"
    full = generate_clip(src / "full.mp4", duration_s=3, fps="25", size="320x180")
    if not (src / "short.mp4").exists():
        # same video, audio 0.3 s shorter than the video
        run_ffmpeg(["-i", str(full), "-c:v", "copy", "-af", "atrim=end=2.7",
                    "-c:a", "aac", str(src / "short.mp4")])  # fmt: skip
        generate_music(src / "music.m4a", duration_s=5)
    edl = {
        "sources": {"full": {"file": "full.mp4", "fps": "25"},
                    "short": {"file": "short.mp4", "fps": "25"}},
        "events": {"source_duration_us": 3_000_000, "first_us": 1_000_000, "every_us": 2_000_000},
        "segments": [
            # ends exactly at EOF; 1.51 s rounds UP to 38 frames = 1.52 s (10 ms past EOF)
            # and the source audio ends 0.3 s before the cut
            {"src": "short", "in_us": 1_490_000, "dur_us": 1_510_000},
            {"src": "full", "in_us": 500_000, "dur_us": 1_000_000},  # event at 1.0 s
        ],
    }  # fmt: skip
    (tmp / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
    out = tmp / ("pad" if pad else "nopad")
    argv = [
        str(tmp / "edl.json"), "--sources", str(src), "--music", str(src / "music.m4a"),
        "--preset", "test180", "--out", str(out), "--chunk-s", "30",
    ] + ([] if pad else ["--no-pad"])  # fmt: skip
    build_chunks.build(build_chunks.make_parser().parse_args(argv))
    return out / "export.mp4"


@needs_ffmpeg
def test_padding_keeps_later_segments_in_place(tmp_path: Path) -> None:
    video = _export(tmp_path, pad=True)
    info = probe(video)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert abs(float(v["duration"]) - 2.52) < 0.001  # 38 + 25 frames
    assert abs(float(a["duration"]) - 2.52) < 0.03
    # second segment: event 0.5 s after its start at 1.52 s -> 2.02 s
    beeps = detect_beeps(video)
    flashes = detect_flashes(video)
    assert any(abs(b - 2_020_000) <= 5_000 for b in beeps)
    # flash: rel 0.5 s = 12.5 frames, rounds half up to tick 38 + 13 = 51 -> 2.04 s
    assert any(abs(f - 2_040_000) <= 1_000 for f in flashes)


@needs_ffmpeg
def test_without_padding_audio_after_short_segment_shifts(tmp_path: Path) -> None:
    video = _export(tmp_path, pad=False)
    beeps = detect_beeps(video)
    assert not any(abs(b - 2_020_000) <= 5_000 for b in beeps)
    assert any(abs(b - 1_720_000) <= 10_000 for b in beeps)  # pulled 0.3 s forward
