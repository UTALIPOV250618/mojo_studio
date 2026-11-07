"""S3 frame metrics and the pipe frame reader (pure logic, tiny arrays)."""

import io

import numpy as np
import pytest

from spikes.s3_decode import metrics
from spikes.s3_decode.metrics import FrameReader, MetricsAccumulator


def _noise(seed: int = 0, h: int = 18, w: int = 32) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (h, w), dtype=np.uint8)


def test_laplacian_flat_is_zero_and_noise_is_sharper_than_blur() -> None:
    flat = np.full((18, 32), 100, dtype=np.uint8)
    assert metrics.laplacian_variance(flat) == 0.0
    sharp = _noise()
    blur = np.full_like(sharp, int(sharp.mean()))
    assert metrics.laplacian_variance(sharp) > metrics.laplacian_variance(blur) + 100


def test_laplacian_numpy_matches_known_value() -> None:
    f = np.zeros((5, 5), dtype=np.uint8)
    f[2, 2] = 10  # centre response -40, four neighbours +10 each, rest 0 (interior 3x3)
    lap = np.array([0, 10, 0, 10, -40, 10, 0, 10, 0], dtype=np.float32)
    assert metrics.laplacian_variance_np(f) == pytest.approx(float(lap.var()))


def test_laplacian_cv2_agrees_with_numpy_on_interior_sign() -> None:
    if not metrics.has_opencv():
        pytest.skip("OpenCV not installed")
    f = _noise(1)
    a, b = metrics.laplacian_variance(f), metrics.laplacian_variance_np(f)
    assert a == pytest.approx(b, rel=0.15)  # border handling differs


def test_brightness_and_motion() -> None:
    black = np.zeros((9, 16), dtype=np.uint8)
    white = np.full((9, 16), 255, dtype=np.uint8)
    assert metrics.mean_brightness(black) == 0.0
    assert metrics.mean_brightness(white) == 1.0
    assert metrics.frame_motion(black, white) == 1.0
    assert metrics.frame_motion(white, white) == 0.0


def test_phase_shift_finds_integer_roll() -> None:
    a = _noise(2, 36, 64)
    assert metrics.phase_shift(a, a) == 0.0
    assert metrics.phase_shift(a, np.roll(a, (3, -4), axis=(0, 1))) == pytest.approx(5.0)
    assert metrics.phase_shift(np.zeros((9, 16), np.uint8), np.zeros((9, 16), np.uint8)) == 0.0


def test_accumulator_keeps_previous_frame_across_chunks() -> None:
    frames = np.stack([_noise(i) for i in range(5)])
    whole = MetricsAccumulator(fps=4.0)
    whole.update(frames)
    split = MetricsAccumulator(fps=4.0)
    split.update(frames[:2])
    split.update(frames[2:])
    a, b = whole.result(), split.result()
    for key in ("sharpness", "brightness", "motion", "shake"):
        np.testing.assert_allclose(a[key], b[key])
    assert a["motion"][0] == 0.0 and a["motion"][1] > 0.0
    np.testing.assert_allclose(a["t"], [0, 0.25, 0.5, 0.75, 1.0])


class Trickle(io.RawIOBase):
    """Fake pipe: returns at most `step` bytes per read, like a real pipe."""

    def __init__(self, data: bytes, step: int) -> None:
        self.buf = io.BytesIO(data)
        self.step = step

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:  # type: ignore[override]
        return self.buf.read(min(size, self.step) if size >= 0 else self.step)


def _stream(n: int, w: int = 4, h: int = 3) -> bytes:
    return b"".join(bytes([i]) * (w * h) for i in range(n))


@pytest.mark.parametrize("step", [1, 5, 12, 1000])
def test_reader_chunks_survive_short_reads(step: int) -> None:
    reader = FrameReader(Trickle(_stream(7), step), 4, 3, chunk_frames=3)  # type: ignore[arg-type]
    sizes, firsts = [], []
    for chunk in reader.chunks():
        sizes.append(chunk.shape[0])
        firsts.extend(int(c[0, 0]) for c in chunk)
    assert sizes == [3, 3, 1]
    assert firsts == list(range(7))
    assert reader.frames_read == 7 and reader.partial_bytes == 0


def test_reader_reports_trailing_partial_frame() -> None:
    reader = FrameReader(io.BytesIO(_stream(2) + b"xx"), 4, 3, chunk_frames=8)
    chunks = list(reader.chunks())
    assert [c.shape for c in chunks] == [(2, 3, 4)]
    assert reader.partial_bytes == 2


def test_reader_empty_stream_and_bad_args() -> None:
    assert list(FrameReader(io.BytesIO(b""), 4, 3).chunks()) == []
    with pytest.raises(ValueError, match="positive"):
        FrameReader(io.BytesIO(b""), 0, 3)
