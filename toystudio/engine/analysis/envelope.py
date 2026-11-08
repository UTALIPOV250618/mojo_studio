"""Audio loudness envelope for the timeline waveform."""

import wave
from pathlib import Path

import numpy as np


def envelope_from_wav(wav: Path, rate_hz: int = 100) -> np.ndarray:
    """RMS per window of 1/rate_hz seconds (10 ms by default), float32 in 0..1."""
    with wave.open(str(wav), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        if width != 2:
            raise ValueError(f"expected 16-bit wav, got {8 * width}-bit")
        win = max(rate // rate_hz, 1)
        out: list[np.ndarray] = []
        carry = np.empty(0, dtype=np.float32)
        while True:
            raw = w.readframes(win * 4096)
            if not raw:
                break
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if channels > 1:
                x = x.reshape(-1, channels).mean(axis=1)
            x = np.concatenate([carry, x])
            n = (len(x) // win) * win
            carry = x[n:]
            if n:
                out.append(np.sqrt((x[:n].reshape(-1, win) ** 2).mean(axis=1)))
        if len(carry):
            out.append(np.sqrt(np.array([(carry**2).mean()], dtype=np.float32)))
    if not out:
        return np.empty(0, dtype=np.float32)
    return np.concatenate(out).astype(np.float32)
