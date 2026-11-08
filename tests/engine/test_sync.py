import math

import numpy as np

from toystudio.engine.analysis.sync import estimate_offset, sync_project


def noise_env(n: int, seed: int) -> np.ndarray:
    """Deterministic 100 Hz envelope of 'speech and music': bursts over a low floor."""
    rng = np.random.default_rng(seed)
    env = 0.02 + 0.01 * rng.random(n)
    pos = 0
    while pos < n:
        pos += int(rng.integers(20, 200))
        length = min(int(rng.integers(10, 150)), n - pos)
        if length <= 0:
            break
        env[pos : pos + length] += rng.uniform(0.1, 1.0) * (0.5 + rng.random(length))
        pos += length
    return env.astype(np.float32)


def test_offset_found_within_10ms():
    base = noise_env(60_00, 1)  # 60 s
    other = base[1234 : 1234 + 30_00]  # piece from 12.34 s, 30 s long
    off, conf = estimate_offset(base, other)
    assert abs(off - 12_340_000) <= 10_000 and conf >= 6.0


def test_unrelated_audio_low_confidence():
    _off, conf = estimate_offset(noise_env(6000, 1), noise_env(3000, 2))
    assert conf < 6.0


def test_same_seed_is_deterministic_and_seeds_differ():
    assert np.array_equal(noise_env(500, 3), noise_env(500, 3))
    assert not np.array_equal(noise_env(500, 3), noise_env(500, 4))


def test_negative_offset():
    base = noise_env(6000, 1)
    other = np.concatenate([noise_env(500, 9), base[:3000]])  # starts 5 s before ref
    off, conf = estimate_offset(base, other)
    assert abs(off - (-5_000_000)) <= 10_000 and conf >= 6.0


def test_sync_skips_media_without_audio():
    res = {
        r.media_id: r
        for r in sync_project({1: noise_env(6000, 1), 2: noise_env(6000, 1)[500:3800], 3: None})
    }
    assert res[1].offset_us == 0 and res[1].confidence == math.inf
    assert abs(res[2].offset_us - 5_000_000) <= 10_000
    assert res[3].offset_us is None and res[3].confidence == 0.0


def test_short_overlap_rejected():
    base = noise_env(6000, 1)
    other = base[1000:2000]  # 10 s overlap, well matched
    res = {r.media_id: r for r in sync_project({1: base, 2: other})}
    assert res[2].offset_us is None
    assert res[2].confidence >= 6.0


def test_other_camera_noise_and_gain():
    base = noise_env(6000, 5)
    piece = base[2000:5000]
    rng = np.random.default_rng(11)
    other = 0.3 * piece + rng.normal(0.0, 0.2 * float(piece.std()), piece.shape)
    off, conf = estimate_offset(base, other.astype(np.float32))
    assert abs(off - 20_000_000) <= 20_000 and conf >= 6.0


def test_reference_is_longest_ties_lowest_id():
    res = {r.media_id: r for r in sync_project({5: noise_env(6000, 1), 2: noise_env(6000, 1)})}
    assert res[2].offset_us == 0 and res[2].confidence == math.inf
    assert res[5].offset_us == 0
