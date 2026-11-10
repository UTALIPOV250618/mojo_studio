"""Synthetic media helpers for tests (lavfi test pattern + sine tone or noise bursts)."""

import math
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
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


def noise_burst_track(total_s: float, *, seed: int = 1, rate: int = 16_000) -> np.ndarray:
    """Deterministic 'speech and music': white noise gated by random loud bursts (int16)."""
    rng = np.random.default_rng(seed)
    n_env = round(total_s * 100)  # 100 Hz gain curve
    env = 0.02 + 0.01 * rng.random(n_env)
    pos = 0
    while pos < n_env:
        pos += int(rng.integers(20, 200))
        length = min(int(rng.integers(10, 150)), n_env - pos)
        if length <= 0:
            break
        env[pos : pos + length] += rng.uniform(0.1, 0.6) * (0.5 + rng.random(length))
        pos += length
    gain = np.repeat(env, rate // 100)
    carrier = rng.uniform(-1.0, 1.0, gain.size)
    return np.clip(gain * carrier * 32767, -32768, 32767).astype(np.int16)


def make_noise_clip(
    path: Path,
    *,
    duration_s: float,
    start_s: float = 0.0,
    track_s: float | None = None,
    seed: int = 1,
    size: str = "320x180",
    fps: int = 25,
) -> Path:
    """Clip whose audio is [start_s, start_s + duration_s) of a seeded noise-burst track.

    Two clips with the same seed and track_s share one track, so the second can be a
    piece of the first shifted by start_s (a sine tone has a flat envelope and cannot sync).
    """
    import wave

    rate = 16_000
    total = track_s if track_s is not None else start_s + duration_s
    track = noise_burst_track(total, seed=seed, rate=rate)
    piece = track[round(start_s * rate) : round((start_s + duration_s) * rate)]
    path.parent.mkdir(parents=True, exist_ok=True)
    wav = path.with_name(path.stem + ".noise.wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(piece.tobytes())
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration_s}",
             "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-g", str(fps), "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)],
            check=True, capture_output=True,
        )  # fmt: skip
    finally:
        wav.unlink(missing_ok=True)
    return path


def write_wav16(path: Path, samples: np.ndarray, rate: int = 16_000) -> Path:
    """Mono 16-bit wav from int16 samples or floats in [-1, 1]."""
    import wave

    pcm = samples if samples.dtype == np.int16 else (np.clip(samples, -1, 1) * 32767)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype("<i2").tobytes())
    return path


def read_wav16(path: Path) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()


def make_wav_clip(path: Path, wav: Path, *, size: str = "320x180", fps: int = 25) -> Path:
    """Test-pattern video as long as `wav`, with `wav` as its (AAC) audio track."""
    import wave

    with wave.open(str(wav), "rb") as w:
        duration_s = w.getnframes() / w.getframerate()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration_s}",
         "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-g", str(fps), "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)],
        check=True, capture_output=True,
    )  # fmt: skip
    return path


def speechlike(seconds: float, seed: int = 1) -> np.ndarray:
    """Glottal pulse train (100-220 Hz) through switching formant resonators, 4 Hz syllables."""
    rng = np.random.default_rng(seed)
    n = int(seconds * 16_000)
    t = np.arange(n) / 16_000
    f0 = 160 + 50 * np.sin(2 * math.pi * 0.7 * t) + 10 * np.sin(2 * math.pi * 3.1 * t)
    phase = np.cumsum(f0) / 16_000
    # sawtooth-like glottal source: rich in harmonics
    src = 2 * (phase % 1.0) - 1
    src = np.diff(src, prepend=src[0])
    vowels = [(730, 1090, 2440), (270, 2290, 3010), (300, 870, 2240), (530, 1840, 2480)]
    out = np.zeros(n, dtype=np.float64)
    seg = int(0.15 * 16_000)
    for i in range(0, n, seg):
        formants = vowels[int(rng.integers(0, len(vowels)))]
        chunk = src[i : i + seg].astype(np.float64)
        acc = np.zeros_like(chunk)
        for f in formants:
            r = math.exp(-math.pi * 90 / 16_000)
            a1, a2 = -2 * r * math.cos(2 * math.pi * f / 16_000), r * r
            y = np.zeros_like(chunk)
            y1 = y2 = 0.0
            for k in range(len(chunk)):
                yk = chunk[k] - a1 * y1 - a2 * y2
                y[k] = yk
                y2, y1 = y1, yk
            acc += y / (1 + f / 1000)
        out[i : i + seg] = acc
    env = 0.5 * (1 + np.sin(2 * math.pi * 4 * t - math.pi / 2)) ** 0.5
    sig = out * env
    sig = sig / np.max(np.abs(sig)) * 0.5
    return (sig + 0.003 * rng.standard_normal(n)).astype(np.float32)


APPLAUSE_WAV = Path(__file__).resolve().parent / "data" / "applause_cc0_16k.wav"


def make_toast_clip(tmp_path: Path) -> Path:
    """30 s of speech-like signal, then the real CC0 applause (10 s)."""
    speech = (speechlike(30) * 32767).astype(np.int16)
    track = np.concatenate([speech, read_wav16(APPLAUSE_WAV)])
    wav = write_wav16(tmp_path / "src" / "тост.wav", track)
    return make_wav_clip(tmp_path / "src" / "тост.mp4", wav)
