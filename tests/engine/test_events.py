import csv
import math
from pathlib import Path

import numpy as np
import pytest

from tests.engine.test_vad import SR, noise, speechlike, write_wav
from toystudio.core.models import AudioEvent, AudioLabel
from toystudio.engine.analysis.single_pass import Stopped
from toystudio.engine.audio import events as ev
from toystudio.engine.audio.events import (
    EVENT_ON,
    HOP_US,
    LABEL_CLASSES,
    MIN_EVENT_S,
    WINDOW_US,
    detect_events,
    events_from_scores,
    label_scores_from_wav,
    music_share,
)
from toystudio.engine.models import model_path

NCLS = 521


def class_index(name: str) -> int:
    with model_path("yamnet_class_map").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return next(int(r["index"]) for r in rows if r["display_name"] == name)


def injected(active: dict[str, list[float]]) -> np.ndarray:
    """Scores matrix with the given per-frame values on the first class of each named label."""
    n = max(len(v) for v in active.values())
    m = np.zeros((n, NCLS), dtype=np.float32)
    for label, vals in active.items():
        idx = class_index(LABEL_CLASSES[AudioLabel(label)][0])
        m[: len(vals), idx] = vals
    return m


def test_label_classes_exist_in_class_map() -> None:
    with model_path("yamnet_class_map").open(encoding="utf-8", newline="") as f:
        names = {r["display_name"] for r in csv.DictReader(f)}
    for label, classes in LABEL_CLASSES.items():
        for c in classes:
            assert c in names, (label, c)


def test_unknown_class_raises_key_error() -> None:
    with pytest.raises(KeyError, match="No Such Class"):
        ev._resolve_indices({AudioLabel.SPEECH: ("No Such Class",)})


def test_events_merge_and_min_length() -> None:
    # music: 6 active frames, 1-frame gap, 4 active -> merged; gap of 2 frames splits.
    music = [0.9] * 6 + [0.0] + [0.9] * 4 + [0.0, 0.0] + [0.9] * 5 + [0.0] * 2
    # applause: single frame -> 0.96 s < MIN_EVENT_S -> dropped
    applause = [0.0, 0.0, 0.9]
    out = events_from_scores(7, injected({"music": music, "applause": applause}))
    assert all(e.media_id == 7 for e in out)
    assert [e.label for e in out] == [AudioLabel.MUSIC, AudioLabel.MUSIC]
    first, second = out
    assert first.start_us == 0
    assert first.end_us == 10 * HOP_US + WINDOW_US  # last active frame is #10
    assert second.start_us == 13 * HOP_US
    assert second.end_us == 17 * HOP_US + WINDOW_US
    assert first.score == pytest.approx(0.9 * 10 / 11, abs=1e-6)
    assert (second.end_us - second.start_us) / 1e6 >= MIN_EVENT_S


def test_events_threshold_per_label() -> None:
    below = EVENT_ON[AudioLabel.SPEECH] - 0.05
    above = EVENT_ON[AudioLabel.APPLAUSE] + 0.05
    out = events_from_scores(1, injected({"speech": [below] * 6, "applause": [above] * 6}))
    assert [e.label for e in out] == [AudioLabel.APPLAUSE]


def test_events_label_score_is_max_over_its_classes() -> None:
    m = np.zeros((4, NCLS), dtype=np.float32)
    m[:, class_index("Guitar")] = 0.8  # a MUSIC class other than the first
    out = events_from_scores(1, m)
    assert [e.label for e in out] == [AudioLabel.MUSIC]


def test_events_clipped_to_duration() -> None:
    out = events_from_scores(1, injected({"music": [0.9] * 4}), duration_us=2_000_000)
    assert out[0].end_us == 2_000_000


def test_music_share() -> None:
    events = [
        AudioEvent(1, 0, 4_000_000, AudioLabel.MUSIC, 0.9),
        AudioEvent(1, 3_000_000, 6_000_000, AudioLabel.MUSIC, 0.9),  # overlaps the first
        AudioEvent(1, 8_000_000, 9_000_000, AudioLabel.SPEECH, 0.9),
    ]
    assert music_share(events, 0, 10_000_000) == pytest.approx(0.6)
    assert music_share(events, 5_000_000, 7_000_000) == pytest.approx(0.5)
    assert music_share(events, 8_000_000, 9_000_000) == 0.0
    assert music_share(events, 5, 5) == 0.0
    assert music_share([], 0, 10) == 0.0


def test_events_silence_is_empty(tmp_path: Path) -> None:
    wav = write_wav(tmp_path / "s.wav", np.zeros(SR * 5, dtype=np.float32))
    assert detect_events(1, wav, on_progress=lambda _: None, should_stop=lambda: False) == []


def music_like(seconds: float) -> np.ndarray:
    """Chord changes every 0.5 s; harmonic notes, decaying plucks and a kick-like beat."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    chords = [(220, 277.2, 329.6), (246.9, 311.1, 370), (261.6, 329.6, 392), (293.7, 370, 440)]
    for i in range(int(seconds / 0.5)):
        a, b = i * SR // 2, (i + 1) * SR // 2
        tt = t[a:b] - t[a]
        env = np.exp(-3 * tt)
        for f in chords[i % len(chords)]:
            for h, amp in ((1, 1.0), (2, 0.5), (3, 0.3), (4, 0.15)):
                out[a:b] += amp * env * np.sin(2 * math.pi * f * h * tt) / 3
        kick = np.exp(-25 * tt) * np.sin(2 * math.pi * 60 * tt)
        out[a:b] += 0.8 * kick
    return (out / np.max(np.abs(out)) * 0.7).astype(np.float32)


def applause_like(seconds: float, seed: int = 3) -> np.ndarray:
    """Dense random broadband clicks with decaying envelopes (many people clapping)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = np.zeros(n)
    kernel_t = np.arange(int(0.02 * SR)) / SR
    for _ in range(int(seconds * 80)):
        pos = int(rng.integers(0, n - len(kernel_t)))
        k = rng.standard_normal(len(kernel_t)) * np.exp(-kernel_t / rng.uniform(0.002, 0.006))
        out[pos : pos + len(kernel_t)] += k * rng.uniform(0.3, 1.0)
    return (out / np.max(np.abs(out)) * 0.6).astype(np.float32)


def run(wav: Path) -> list[AudioEvent]:
    return detect_events(1, wav, on_progress=lambda _: None, should_stop=lambda: False)


def test_events_detects_music_like_tone_sequence(tmp_path: Path) -> None:
    wav = write_wav(tmp_path / "m.wav", music_like(15.0))
    got = [e for e in run(wav) if e.label == AudioLabel.MUSIC]
    assert sum(e.end_us - e.start_us for e in got) / 1e6 >= 10.0


@pytest.mark.xfail(
    reason="YAMNet does not label synthetic clicks as Applause (max 0.02); needs a real CC0 sample",
    strict=False,
)
def test_events_detects_applause_like_noise(tmp_path: Path) -> None:
    wav = write_wav(tmp_path / "a.wav", applause_like(6.0))
    got = [e for e in run(wav) if e.label == AudioLabel.APPLAUSE]
    assert got, "no APPLAUSE event"
    assert sum(e.end_us - e.start_us for e in got) / 1e6 >= 3.0


def test_events_detects_speech_like(tmp_path: Path) -> None:
    wav = write_wav(tmp_path / "sp.wav", speechlike(6.0))
    assert any(e.label == AudioLabel.SPEECH for e in run(wav))


def test_events_streams_in_blocks(tmp_path: Path) -> None:
    sig = np.concatenate([noise(3.0, -30, 5), speechlike(5.0), noise(2.0, -50, 6)])
    wav = write_wav(tmp_path / "x.wav", sig)
    a = label_scores_from_wav(wav, block_s=30.0)
    b = label_scores_from_wav(wav, block_s=1.7)
    c = label_scores_from_wav(wav, block_s=0.5)
    assert a.shape == b.shape == c.shape
    # frames on the global 0.48 s grid: 1 + ceil(max(0, n - 15600) / 7680)
    n = len(sig)
    assert a.shape[0] == 1 + math.ceil(max(0, n - 15600) / 7680)
    np.testing.assert_allclose(a, b, atol=1e-4)
    np.testing.assert_allclose(a, c, atol=1e-4)


def test_events_progress_and_stop(tmp_path: Path) -> None:
    wav = write_wav(tmp_path / "p.wav", noise(4.0, -30))
    seen: list[float] = []
    detect_events(1, wav, on_progress=seen.append, should_stop=lambda: False)
    assert seen and seen[-1] == pytest.approx(4.0, abs=0.01)
    with pytest.raises(Stopped):
        detect_events(1, wav, on_progress=lambda _: None, should_stop=lambda: True)


def test_class_scores_keeps_exactly_requested_rows() -> None:
    n = 38_640  # the model returns 5 rows here; only 1 + (n - 15600) // 7680 = 4 are full
    out = ev._class_scores(ev._session(), np.zeros(n, dtype=np.float32), 1 + (n - 15_600) // 7_680)
    assert out.shape == (4, NCLS)
