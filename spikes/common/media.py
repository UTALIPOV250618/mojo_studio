"""Synthetic test clips (flash + beep at the same instants) and an ffprobe wrapper."""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

FIRST_EVENT_S = 1.0
BEEP_S = 0.02
SAMPLE_RATE = 48_000


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"{name} not found on PATH")
    return found


def run_ffmpeg(args: list[str]) -> None:
    cmd = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()}")


def generate_clip(
    path: Path,
    *,
    duration_s: float,
    fps: str,
    size: str = "1920x1080",
    flash_every_s: float = 2.0,
    codec: str = "libx264",
) -> Path:
    """Make testsrc2 video with a 1-frame white flash and a 1 kHz 20 ms beep.

    Both occur at 1.0 s + k * flash_every_s. The flash lands on the first frame
    at or after the target time.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_dur = 1 / Fraction(fps)
    every = repr(float(flash_every_s))
    # Window is one frame wide but centred on the target, so every frame time sits
    # half a frame from the window edges and float rounding cannot match two frames.
    half = float(frame_dur) / 2
    start = repr(FIRST_EVENT_S - half)
    at_event = f"gte(t,{start})*lt(mod(t-{start},{every}),{float(frame_dur)!r})"
    video = (
        f"testsrc2=size={size}:rate={fps}:duration={duration_s},"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{at_event}',format=yuv420p"
    )
    beep = f"gte(t,{FIRST_EVENT_S})*lt(mod(t-{FIRST_EVENT_S},{every}),{BEEP_S})"
    expr = f"0.8*sin(2*PI*1000*t)*{beep}"
    audio = f"aevalsrc=exprs='{expr}|{expr}':s={SAMPLE_RATE}:d={duration_s}"
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", video,
            "-f", "lavfi", "-i", audio,
            "-c:v", codec, "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
            "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-ac", "2",
            "-shortest", str(path),
        ]
    )  # fmt: skip
    return path


def generate_music(path: Path, *, duration_s: float = 120.0) -> Path:
    """Sine 'music' track with a slow amplitude envelope."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    expr = "0.5*sin(2*PI*440*t)*(0.6+0.4*sin(2*PI*0.5*t))"
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='{expr}|{expr}':s={SAMPLE_RATE}:d={duration_s}",
            "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-ac", "2", str(path),
        ]
    )  # fmt: skip
    return path


def generate_set(dir: Path) -> list[Path]:
    """Set for S2/S5: three 60 s clips (25, 29.97, 50 fps) and a 120 s music track."""
    dir = Path(dir)
    paths = [
        generate_clip(dir / f"clip_{label}.mp4", duration_s=60, fps=fps)
        for label, fps in (("25", "25"), ("29.97", "30000/1001"), ("50", "50"))
    ]
    paths.append(generate_music(dir / "music.m4a", duration_s=120))
    return paths


def probe(path: Path) -> dict[str, Any]:
    cmd = [
        tool("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    data: dict[str, Any] = json.loads(proc.stdout)
    return data
