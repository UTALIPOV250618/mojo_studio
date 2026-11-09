"""Audio events (speech, applause, music, cheer, laughter) with the bundled YAMNet (ONNX)."""

import csv
import time
import wave
from collections.abc import Callable, Mapping
from pathlib import Path

import numpy as np
import onnxruntime as ort

from toystudio.core.models import AudioEvent, AudioLabel
from toystudio.engine.analysis.single_pass import Stopped
from toystudio.engine.audio import ORT_THREADS
from toystudio.engine.audio.wav import SAMPLE_RATE, read_blocks
from toystudio.engine.models import model_path

HOP = 7_680  # samples between frames (0.48 s)
WINDOW = 15_600  # samples one frame needs: 96 mel patches * 160 + 240 STFT overlap
WINDOW_US = 960_000  # nominal frame length reported for events
HOP_US = HOP * 1_000_000 // SAMPLE_RATE
MIN_EVENT_S = 1.0
MAX_GAP_FRAMES = 1  # inactive frames that may separate two parts of one event
PROGRESS_EVERY_S = 0.25

LABELS: tuple[AudioLabel, ...] = tuple(AudioLabel)
EVENT_ON: dict[AudioLabel, float] = {
    AudioLabel.SPEECH: 0.5,
    AudioLabel.APPLAUSE: 0.3,
    AudioLabel.MUSIC: 0.4,
    AudioLabel.CHEER: 0.3,
    AudioLabel.LAUGHTER: 0.3,
}
LABEL_CLASSES: dict[AudioLabel, tuple[str, ...]] = {
    AudioLabel.SPEECH: ("Speech", "Conversation", "Narration, monologue"),
    AudioLabel.APPLAUSE: ("Applause", "Clapping"),
    AudioLabel.MUSIC: (
        "Music",
        "Musical instrument",
        "Singing",
        "Pop music",
        "Folk music",
        "Dance music",
        "Electronic music",
        "Drum",
        "Accordion",
        "Guitar",
        "Keyboard (musical)",
        "Synthesizer",
    ),
    AudioLabel.CHEER: ("Cheering", "Crowd", "Shout", "Yell"),
    AudioLabel.LAUGHTER: ("Laughter", "Giggle", "Chuckle, chortle"),
}


def _resolve_indices(classes: Mapping[AudioLabel, tuple[str, ...]]) -> dict[AudioLabel, list[int]]:
    with model_path("yamnet_class_map").open(encoding="utf-8", newline="") as f:
        by_name = {row["display_name"]: int(row["index"]) for row in csv.DictReader(f)}
    out: dict[AudioLabel, list[int]] = {}
    for label, names in classes.items():
        out[label] = [by_name[name] for name in names]  # KeyError carries the missing name
    return out


LABEL_INDICES = _resolve_indices(LABEL_CLASSES)


def label_scores(scores: np.ndarray) -> np.ndarray:
    """[frames, 521] class scores -> [frames, len(LABELS)] (max over each label's classes)."""
    return np.stack([scores[:, LABEL_INDICES[label]].max(axis=1) for label in LABELS], axis=1)


def events_from_label_scores(
    media_id: int, scores: np.ndarray, duration_us: int | None = None
) -> list[AudioEvent]:
    """Threshold, merge (gap <= MAX_GAP_FRAMES) and drop short events from [frames, labels]."""
    events: list[AudioEvent] = []
    for col, label in enumerate(LABELS):
        column = scores[:, col]
        active = np.flatnonzero(column >= EVENT_ON[label])
        if not len(active):
            continue
        # split where more than MAX_GAP_FRAMES inactive frames separate neighbours
        breaks = np.flatnonzero(np.diff(active) > MAX_GAP_FRAMES + 1) + 1
        for run in np.split(active, breaks):
            start_us = int(run[0]) * HOP_US
            end_us = int(run[-1]) * HOP_US + WINDOW_US
            if duration_us is not None:
                end_us = min(end_us, duration_us)
            if (end_us - start_us) / 1_000_000 < MIN_EVENT_S:
                continue
            mean = float(np.mean(column[run[0] : run[-1] + 1]))
            events.append(AudioEvent(media_id, start_us, end_us, label, mean))
    events.sort(key=lambda e: (e.start_us, e.end_us))
    return events


def events_from_scores(
    media_id: int, scores: np.ndarray, duration_us: int | None = None
) -> list[AudioEvent]:
    """Pure post-processing over a YAMNet [frames, 521] scores matrix."""
    return events_from_label_scores(media_id, label_scores(scores), duration_us)


def _session() -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = ORT_THREADS
    opts.inter_op_num_threads = 1
    return ort.InferenceSession(str(model_path("yamnet")), opts, providers=["CPUExecutionProvider"])


def _class_scores(session: ort.InferenceSession, chunk: np.ndarray, frames: int) -> np.ndarray:
    """First `frames` rows of the model output.

    The model's row count is off by one at some exact chunk lengths; frame k only depends on
    its own samples, so keep exactly the frames asked for.
    """
    out = session.run(["output_0"], {"waveform": np.ascontiguousarray(chunk)})[0]
    return out[:frames]


def label_scores_from_wav(
    wav: Path,
    *,
    block_s: float = 30.0,
    on_progress: Callable[[float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> np.ndarray:
    """Per-frame label scores [frames, labels] on the global 0.48 s grid, streamed in blocks.

    The unconsumed tail of the buffer always starts at a frame boundary, so the samples that
    overlap the next frame (WINDOW - HOP, 7920) are carried into the next chunk and the frame
    scores do not depend on block_s.
    """
    session = _session()
    rows: list[np.ndarray] = []
    buf = np.empty(0, dtype=np.float32)
    total = 0
    last_emit = 0.0

    def run(chunk: np.ndarray, frames: int) -> None:
        rows.append(label_scores(_class_scores(session, chunk, frames)))

    for start, block in read_blocks(wav, block_s):
        if should_stop is not None and should_stop():
            raise Stopped()
        buf = np.concatenate([buf, block]) if len(buf) else block
        total = start + len(block)
        if len(buf) >= WINDOW:
            frames = (len(buf) - WINDOW) // HOP + 1
            run(buf[: WINDOW + (frames - 1) * HOP], frames)
            buf = buf[frames * HOP :]
        if on_progress is not None:
            now = time.monotonic()
            if now - last_emit >= PROGRESS_EVERY_S:
                last_emit = now
                on_progress(total / SAMPLE_RATE)
    # Tail not covered by a full frame (the previous frame reached HOP samples into it).
    if total and (not rows or len(buf) > WINDOW - HOP):
        run(np.pad(buf, (0, max(0, WINDOW - len(buf)))), 1)
    if on_progress is not None:
        on_progress(total / SAMPLE_RATE)
    if not rows:
        return np.empty((0, len(LABELS)), dtype=np.float32)
    return np.concatenate(rows, axis=0)


def detect_events(
    media_id: int,
    wav: Path,
    *,
    on_progress: Callable[[float], None],
    should_stop: Callable[[], bool],
) -> list[AudioEvent]:
    scores = label_scores_from_wav(wav, on_progress=on_progress, should_stop=should_stop)
    with wave.open(str(wav), "rb") as w:
        duration_us = w.getnframes() * 1_000_000 // SAMPLE_RATE
    return events_from_label_scores(media_id, scores, duration_us)


def music_share(events: list[AudioEvent], start_us: int, end_us: int) -> float:
    """Fraction of [start_us, end_us) covered by MUSIC events (overlaps counted once)."""
    if end_us <= start_us:
        return 0.0
    spans = sorted(
        (max(e.start_us, start_us), min(e.end_us, end_us))
        for e in events
        if e.label == AudioLabel.MUSIC and e.end_us > start_us and e.start_us < end_us
    )
    covered = 0
    cursor = start_us
    for a, b in spans:
        a = max(a, cursor)
        if b > a:
            covered += b - a
            cursor = b
    return covered / (end_us - start_us)
