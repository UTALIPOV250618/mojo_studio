"""Speech regions from the analysis wav with the bundled Silero VAD (v5, ONNX)."""

import time
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import onnxruntime as ort

from toystudio.core.models import SpeechRegion
from toystudio.engine.analysis.single_pass import Stopped
from toystudio.engine.audio import ORT_THREADS
from toystudio.engine.audio.wav import SAMPLE_RATE, read_blocks
from toystudio.engine.models import model_path

FRAME = 512  # samples per model call at 16 kHz (32 ms)
CONTEXT = 64  # Silero v5 expects the previous 64 samples in front of every frame
VAD_ON = 0.5
VAD_OFF = 0.35
MIN_SPEECH_MS = 250
MERGE_GAP_MS = 300
PROGRESS_EVERY_S = 0.25
US_PER_FRAME = FRAME * 1_000_000 // SAMPLE_RATE
FRAME_MS = FRAME * 1000 / SAMPLE_RATE


def regions_from_probs(probs: Sequence[float]) -> list[SpeechRegion]:
    """Hysteresis, gap merging and minimum length over per-frame speech probabilities.

    A region starts at a frame with prob >= VAD_ON, stays alive while prob >= VAD_OFF and
    ends after a pause (prob < VAD_OFF) of MERGE_GAP_MS or more; it ends at its last live frame.
    """
    gap_frames = MERGE_GAP_MS / FRAME_MS  # pause length (frames) that closes a region
    regions: list[SpeechRegion] = []
    start = -1
    last_live = -1

    def close() -> None:
        end = last_live + 1
        if (end - start) * FRAME_MS >= MIN_SPEECH_MS:
            mean = float(np.mean(probs[start:end]))
            regions.append(SpeechRegion(start * US_PER_FRAME, end * US_PER_FRAME, mean))

    for i, p in enumerate(probs):
        if start < 0:
            if p >= VAD_ON:
                start = last_live = i
            continue
        if p >= VAD_OFF:
            last_live = i
        elif i - last_live >= gap_frames:
            close()
            start = -1
    if start >= 0:
        close()
    return regions


def detect_speech(
    wav: Path,
    *,
    on_progress: Callable[[float], None],
    should_stop: Callable[[], bool],
) -> list[SpeechRegion]:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = ORT_THREADS
    opts.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(model_path("silero_vad")), opts, providers=["CPUExecutionProvider"]
    )
    sr = np.array(SAMPLE_RATE, dtype=np.int64)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros(CONTEXT, dtype=np.float32)
    carry = np.empty(0, dtype=np.float32)
    probs: list[float] = []
    last_emit = 0.0
    done_s = 0.0

    def run_frames(samples: np.ndarray) -> None:
        nonlocal state, context
        for off in range(0, len(samples), FRAME):
            frame = samples[off : off + FRAME]
            x = np.concatenate([context, frame])[None, :]
            out, state = session.run(None, {"input": x, "state": state, "sr": sr})
            probs.append(float(out[0, 0]))
            context = frame[-CONTEXT:]

    for start, block in read_blocks(wav):
        if should_stop():
            raise Stopped()
        data = np.concatenate([carry, block]) if len(carry) else block
        usable = len(data) // FRAME * FRAME
        run_frames(data[:usable])
        carry = data[usable:]
        done_s = (start + len(block)) / SAMPLE_RATE
        now = time.monotonic()
        if now - last_emit >= PROGRESS_EVERY_S:
            last_emit = now
            on_progress(done_s)
    if len(carry):
        run_frames(np.pad(carry, (0, FRAME - len(carry))))
    on_progress(done_s)
    return regions_from_probs(probs)
