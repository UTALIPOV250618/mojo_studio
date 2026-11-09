"""Streaming reader for the analysis wav (pcm_s16le, 16 kHz, mono)."""

import wave
from collections.abc import Iterator
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


def read_blocks(wav: Path, block_s: float = 30.0) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (start_sample, float32 samples in [-1, 1]) blocks of about block_s seconds."""
    with wave.open(str(wav), "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(
                f"Expected 16 kHz mono 16-bit wav, got {w.getframerate()} Hz, "
                f"{w.getnchannels()} ch, {8 * w.getsampwidth()} bit"
            )
        block = max(1, int(block_s * SAMPLE_RATE))
        pos = 0
        while True:
            raw = w.readframes(block)
            if not raw:
                return
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            yield pos, data
            pos += len(data)
