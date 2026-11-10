import tracemalloc
import wave
from pathlib import Path

import numpy as np
import pytest

from tests.media_fixtures import speechlike
from toystudio.engine.analysis.single_pass import Stopped
from toystudio.engine.audio.vad import FRAME, detect_speech, regions_from_probs

SR = 16_000
FRAME_US = FRAME * 1_000_000 // SR  # 32 000


def write_wav(path: Path, samples: np.ndarray) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def noise(seconds: float, db: float = -40.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SR)) * 10 ** (db / 20)).astype(np.float32)


def on_off(on: int, off: int) -> list[float]:
    return [0.9] * on + [0.0] * off


# --- pure post-processing -------------------------------------------------


def test_regions_empty_and_below_threshold():
    assert regions_from_probs([]) == []
    assert regions_from_probs([0.49] * 100) == []


def test_regions_basic_times_and_mean_prob():
    probs = [0.0] * 10 + [0.8] * 20 + [0.0] * 10
    (r,) = regions_from_probs(probs)
    assert r.start_us == 10 * FRAME_US
    assert r.end_us == 30 * FRAME_US
    assert r.prob == pytest.approx(0.8)


def test_regions_hysteresis_keeps_region_between_off_and_on():
    probs = [0.0] * 5 + [0.9] * 10 + [0.4] * 20 + [0.9] * 10 + [0.0] * 5  # 0.4 is above OFF
    (r,) = regions_from_probs(probs)
    assert (r.start_us, r.end_us) == (5 * FRAME_US, 45 * FRAME_US)


def test_regions_below_on_never_starts():
    assert regions_from_probs([0.4] * 100) == []


def test_regions_merge_short_pause_but_not_long():
    short = [0.9] * 20 + [0.0] * 9 + [0.9] * 20 + [0.0] * 3  # 9 * 32 = 288 ms < 300
    assert len(regions_from_probs(short)) == 1
    long = [0.9] * 20 + [0.0] * 10 + [0.9] * 20 + [0.0] * 3  # 320 ms
    r = regions_from_probs(long)
    assert len(r) == 2
    assert r[0].end_us == 20 * FRAME_US  # region ends at last voiced frame, not after the gap


def test_regions_drop_too_short():
    assert regions_from_probs([0.0] * 5 + [0.9] * 7 + [0.0] * 20) == []  # 224 ms < 250
    assert len(regions_from_probs([0.0] * 5 + [0.9] * 8 + [0.0] * 20)) == 1  # 256 ms


def test_regions_open_at_end_is_closed():
    (r,) = regions_from_probs([0.0] * 3 + [0.9] * 20)
    assert r.end_us == 23 * FRAME_US


# --- with the model ---------------------------------------------------------


def run(wav: Path, stop=lambda: False):
    calls: list[float] = []
    regions = detect_speech(wav, on_progress=calls.append, should_stop=stop)
    return regions, calls


def test_vad_on_silence_returns_no_regions(tmp_path):
    wav = write_wav(tmp_path / "s.wav", np.zeros(SR * 5, dtype=np.float32))
    regions, _ = run(wav)
    assert regions == []


def test_vad_finds_speech_like_burst(tmp_path):
    sig = np.concatenate([noise(10, seed=2), speechlike(5), noise(10, seed=3)])
    regions, _ = run(write_wav(tmp_path / "b.wav", sig))
    assert len(regions) == 1
    r = regions[0]
    assert abs(r.start_us / 1e6 - 10) < 0.3
    assert abs(r.end_us / 1e6 - 15) < 0.3


def test_vad_merges_short_pauses(tmp_path):
    sig = np.concatenate([noise(3, seed=4), speechlike(2), noise(0.15, seed=5), speechlike(2, 7)])
    regions, _ = run(write_wav(tmp_path / "m.wav", sig))
    assert len(regions) == 1


def test_vad_streams_in_blocks(tmp_path):
    n = 10 * 60 * SR
    rng = np.random.default_rng(0)
    sig = (rng.standard_normal(n) * 0.003).astype(np.float32)
    wav = write_wav(tmp_path / "long.wav", sig)
    del sig
    tracemalloc.start()
    _, calls = run(wav)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 64 * 1024 * 1024
    assert len(calls) > 1
    assert calls == sorted(calls) and calls[-1] == pytest.approx(600, abs=0.1)


def test_vad_stop(tmp_path):
    wav = write_wav(tmp_path / "s.wav", np.zeros(SR * 5, dtype=np.float32))
    with pytest.raises(Stopped):
        run(wav, stop=lambda: True)


def test_vad_rejects_wrong_format(tmp_path):
    p = tmp_path / "x.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0" * 400)
    with pytest.raises(ValueError):
        run(p)
