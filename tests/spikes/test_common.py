import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from spikes.common import results
from spikes.common.detect import detect_beeps, detect_flashes
from spikes.common.media import generate_clip, probe
from spikes.common.results import write_result

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH",
)


@needs_ffmpeg
def test_generate_cyrillic_path(tmp_path: Path) -> None:
    p = generate_clip(tmp_path / "Той Студия" / "клип 25.mp4", duration_s=5, fps="25")
    assert probe(p)["streams"][0]["r_frame_rate"] == "25/1"


@needs_ffmpeg
def test_flashes_and_beeps_align(tmp_path: Path) -> None:
    p = generate_clip(tmp_path / "a.mp4", duration_s=7, fps="30000/1001")
    f, b = detect_flashes(p), detect_beeps(p)
    assert len(f) == len(b) == 3  # 1.0 / 3.0 / 5.0 s
    assert all(abs(x - y) <= 34_000 for x, y in zip(f, b, strict=True))
    assert abs(f[0] - 1_000_000) <= 34_000


def test_gitignore_covers_data() -> None:
    out = subprocess.run(
        ["git", "check-ignore", "spikes/data/x.mp4"], check=False, capture_output=True
    )
    assert out.returncode == 0


def test_write_result_has_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(results, "RESULTS_DIR", tmp_path)
    path = write_result("s0", "t", {"x": 1, "текст": "привет"})
    assert path.parent == tmp_path / "s0"
    data = json.loads(path.read_text("utf-8"))
    assert data["x"] == 1 and {"machine", "ffmpeg_version", "git_rev"} <= data.keys()
    assert "привет" in path.read_text("utf-8")


@needs_ffmpeg
@pytest.mark.parametrize(
    ("fps", "frame_us"), [("25", 40_000), ("50", 20_000), ("30000/1001", 33_367)]
)
def test_long_clip_has_exactly_one_flash_per_event(fps: str, frame_us: int, tmp_path: Path) -> None:
    p = generate_clip(tmp_path / "long.mp4", duration_s=41, fps=fps, size="320x180")
    f, b = detect_flashes(p), detect_beeps(p)
    assert len(f) == len(b) == 20  # 1, 3, ..., 39 s
    assert all(abs(x - y) <= frame_us for x, y in zip(f, b, strict=True))
