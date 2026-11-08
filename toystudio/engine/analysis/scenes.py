"""Scene cut detection over per-frame metrics (stage 3)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

CUT_K = 6.0  # порог histdiff: median + CUT_K × MAD
CUT_FLOOR = 0.25  # абсолютный минимум histdiff
MOTION_K = 2.0  # motion должен превышать MOTION_K × median(motion)
MIN_GAP_S = 1.0  # минимальный интервал между резами


def _mad(x: np.ndarray) -> float:
    med = float(np.median(x))
    return float(np.median(np.abs(x - med)))


def detect_cuts(m: Mapping[str, np.ndarray], fps: float = 4.0) -> list[tuple[int, float]]:
    """Return (at_us, score) for each cut, sorted by time.

    A cut at frame i needs a histogram jump far above the file's typical variation,
    an absolute jump of at least CUT_FLOOR, and a motion spike (a flash alone is not
    a cut). Cuts closer than MIN_GAP_S keep the stronger one.
    """
    hist = np.asarray(m["histdiff"], dtype=np.float64)
    motion = np.asarray(m["motion"], dtype=np.float64)
    if hist.size == 0:
        return []
    h_thr = float(np.median(hist)) + CUT_K * _mad(hist)
    m_thr = MOTION_K * float(np.median(motion))
    peak = float(hist.max())
    candidates = np.flatnonzero((hist > h_thr) & (hist > CUT_FLOOR) & (motion > m_thr))
    if candidates.size == 0 or peak <= 0.0:
        return []

    gap_frames = MIN_GAP_S * fps
    kept: list[int] = []
    # Strongest first: a candidate is accepted only if no accepted cut is too close.
    for i in sorted(candidates.tolist(), key=lambda j: (-hist[j], j)):
        if all(abs(i - k) >= gap_frames for k in kept):
            kept.append(i)

    out = [(round(i / fps * 1_000_000), float(hist[i] / peak)) for i in sorted(kept)]
    return out
