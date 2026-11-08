"""Synthetic media helpers for tests (lavfi test pattern + sine tone)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


def _ffmpeg_major() -> int:
    exe = shutil.which("ffmpeg")
    if exe is None or shutil.which("ffprobe") is None:
        return 0
    out = subprocess.run([exe, "-version"], capture_output=True, text=True, check=False).stdout
    m = re.match(r"ffmpeg version n?(\d+)\.", out)
    return int(m.group(1)) if m else 99


needs_ffmpeg = pytest.mark.skipif(_ffmpeg_major() < 7, reason="needs ffmpeg/ffprobe 7.0+ on PATH")


def make_clip(
    path: Path,
    *,
    duration_s: float = 4.0,
    size: str = "640x360",
    fps: int = 25,
    audio: bool = True,
    rotate: int = 0,
    tone_hz: int = 440,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration_s}"]  # fmt: skip
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:duration={duration_s}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(fps)]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    if not rotate:
        subprocess.run([*cmd, str(path)], check=True, capture_output=True)
        return path
    # display matrix is an input option on a real file: remux with -display_rotation
    plain = path.with_name(path.stem + ".plain" + path.suffix)
    subprocess.run([*cmd, str(plain)], check=True, capture_output=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-display_rotation", str(rotate), "-i", str(plain),
         "-c", "copy", str(path)],
        check=True, capture_output=True,
    )  # fmt: skip
    plain.unlink()
    return path


def make_truncated(src: Path, dst: Path, keep: float = 0.5) -> Path:
    data = src.read_bytes()
    dst.write_bytes(data[: int(len(data) * keep)])
    return dst
