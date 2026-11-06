"""Detect white flashes (video) and 1 kHz beeps (audio); times in integer microseconds."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from spikes.common.media import SAMPLE_RATE, tool

FLASH_YAVG = 200.0
BEEP_FREQ = 1000.0
BEEP_THRESHOLD = 0.2
DEDUPE_US = 100_000
WINDOW_S = 0.005

_PTS = re.compile(r"pts_time:(\S+)")
_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=(\S+)")


def detect_flashes(video: Path) -> list[int]:
    cmd = [
        tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-an", "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-",
    ]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()}")
    out: list[int] = []
    pts: int | None = None
    for line in proc.stdout.splitlines():
        m = _PTS.search(line)
        if m:
            pts = round(float(m.group(1)) * 1_000_000)
            continue
        m = _YAVG.search(line)
        if m and pts is not None and float(m.group(1)) > FLASH_YAVG:
            out.append(pts)
    return out


def detect_beeps(video_or_audio: Path) -> list[int]:
    cmd = [
        tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(video_or_audio),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
    ]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    samples = np.frombuffer(proc.stdout, dtype="<f4")
    win = round(SAMPLE_RATE * WINDOW_S)
    n = len(samples) // win
    if n == 0:
        return []
    frames = samples[: n * win].reshape(n, win).astype(np.float64)
    # Goertzel-style single-bin DFT at 1 kHz (5 ms = exactly 5 cycles).
    basis = np.exp(-2j * np.pi * BEEP_FREQ * np.arange(win) / SAMPLE_RATE)
    magnitude = 2 * np.abs(frames @ basis) / win  # ~ sine amplitude
    out: list[int] = []
    for i in np.flatnonzero(magnitude > BEEP_THRESHOLD):
        t_us = round(int(i) * win / SAMPLE_RATE * 1_000_000)
        if not out or t_us - out[-1] >= DEDUPE_US:
            out.append(t_us)
    return out
